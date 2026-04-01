"""MutinyChat backend skeleton.

This module will later host Tor Hidden Service bootstrapping and end-to-end encryption logic.
"""

import argparse
import atexit
import base64
import json
import os
import random
import re
import shutil
import socket
import sys
import tempfile
import threading
from typing import Dict, Optional

import nacl.secret
import nacl.utils
import socks
import stem.process
from stem.control import Controller

tor_controller = None
tor_process = None
active_service_id = None
active_local_port = None
active_socks_port = None
listener_thread = None
listener_socket = None
listener_stop_event = threading.Event()
guest_socket = None
active_peer_socket = None
peer_socket_lock = threading.Lock()
inbox_lock = threading.Lock()
_box: Optional[nacl.secret.SecretBox] = None
_shared_key: Optional[bytes] = None
_peer_count: int = 0
_active_room: Optional[dict] = None
_tor_data_dir: Optional[str] = None
_inbox: list[str] = []

ADJECTIVES = [
    "midnight",
    "pixel",
    "sunset",
    "neon",
    "velvet",
    "silver",
    "echo",
    "lunar",
]

NOUNS = [
    "ocean",
    "dream",
    "chat",
    "signal",
    "harbor",
    "voyage",
    "cipher",
    "comet",
]


def _pick_random_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MutinyChat backend entrypoint")
    parser.add_argument("--command", type=str, help="Backend command name")
    parser.add_argument("--message", type=str, help="Test IPC payload from frontend")
    parser.add_argument("--room-name", type=str, help="Friendly room name for hidden service")
    parser.add_argument("--onion-address", type=str, help="Onion host or full share link for join_room")
    parser.add_argument("--port", type=int, help="Onion service port for join_room")
    parser.add_argument("--stdio-json", action="store_true", help="Read JSON commands from stdin and respond on stdout")
    return parser.parse_args()


def _resolve_tor_cmd() -> str:
    env_tor = os.environ.get("MUTINYCHAT_TOR_PATH", "").strip()
    candidates = []
    if env_tor:
        candidates.append(env_tor)

    backend_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.extend(
        [
            os.path.join(backend_dir, "tor", "tor"),
            os.path.join(backend_dir, "dist", "tor"),
            "/opt/homebrew/bin/tor",
            "/usr/local/bin/tor",
            "/usr/bin/tor",
        ]
    )

    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    path_tor = shutil.which("tor")
    if path_tor:
        return path_tor

    raise RuntimeError(
        "Tor executable not found. Install Tor with 'brew install tor' or set MUTINYCHAT_TOR_PATH to your tor binary."
    )


def _friendly_error(exc: Exception) -> str:
    message = str(exc).strip()
    lowered = message.lower()

    if "tor executable not found" in lowered:
        return "Tor is not installed. Install Tor and try again."
    if "failed to start tor" in lowered or "broken state" in lowered:
        return "Tor failed to start. Please try again."
    if "no valid .onion" in lowered:
        return "Invalid share link. Paste a valid room link and try again."
    if "host unreachable" in lowered or "socket error" in lowered:
        return "Could not reach room host. Verify the link and that the host is online."
    if "timed out" in lowered:
        return "Connection timed out. Please try again."
    if "not connected" in lowered or "no active peer socket" in lowered:
        return "Not connected to a peer yet."
    if "e2ee not ready" in lowered:
        return "Secure session is not ready yet. Please wait a moment and try again."

    return message or "Something went wrong. Please try again."


def start_tor() -> Controller:
    global tor_controller, tor_process, active_socks_port, _tor_data_dir

    if tor_controller is not None:
        return tor_controller

    control_port = _pick_random_port()
    active_socks_port = _pick_random_port()
    _tor_data_dir = tempfile.mkdtemp(prefix="mutinychat-tor-")
    tor_cmd = _resolve_tor_cmd()
    try:
        tor_process = stem.process.launch_tor_with_config(
            config={
                "ControlPort": str(control_port),
                "SocksPort": str(active_socks_port),
                "DataDirectory": _tor_data_dir,
            },
            take_ownership=True,
            tor_cmd=tor_cmd,
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to start Tor using '{tor_cmd}': {exc}") from exc

    tor_controller = Controller.from_port(address="127.0.0.1", port=control_port)
    tor_controller.authenticate()
    return tor_controller


def generate_random_room_name() -> str:
    adjective = random.choice(ADJECTIVES)
    noun = random.choice(NOUNS)
    number = random.randint(100, 999)
    return f"{adjective}-{noun}-{number}"


def create_hidden_service(room_name: str) -> dict:
    del room_name
    global active_service_id, active_local_port

    controller = start_tor()
    virtual_port = 8080
    local_port = _pick_random_port()
    service = controller.create_ephemeral_hidden_service(
        {virtual_port: f"127.0.0.1:{local_port}"},
        await_publication=True,
    )

    active_service_id = service.service_id
    active_local_port = local_port
    onion_address = f"{service.service_id}.onion"
    return {"onion_address": onion_address, "port": virtual_port}


def _handle_guest_connection(conn: socket.socket) -> None:
    update_peer_count(1)
    _set_active_peer_socket(conn)
    _queue_frontend_message("__peer_joined__")
    _send_e2ee_key_to_peer(conn)
    _read_socket_messages(conn)
    _clear_active_peer_socket(conn)
    update_peer_count(-1)
    try:
        conn.close()
    except OSError:
        pass


def _queue_frontend_message(text: str) -> None:
    with inbox_lock:
        _inbox.append(text)


def _send_e2ee_key_to_peer(conn: socket.socket) -> None:
    if _shared_key is None:
        return

    key_b64 = base64.b64encode(_shared_key).decode("utf-8")
    try:
        conn.sendall(f"__e2ee_key__:{key_b64}\n".encode("utf-8"))
    except OSError:
        pass


def _set_active_peer_socket(conn: socket.socket) -> None:
    global active_peer_socket
    with peer_socket_lock:
        active_peer_socket = conn


def _clear_active_peer_socket(conn: socket.socket) -> None:
    global active_peer_socket
    with peer_socket_lock:
        if active_peer_socket is conn:
            active_peer_socket = None


def _read_socket_messages(conn: socket.socket) -> None:
    conn.settimeout(1.0)
    buffer = ""
    while not listener_stop_event.is_set():
        try:
            chunk = conn.recv(1024)
            if not chunk:
                break
            buffer += chunk.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                text = line.strip()
                if text:
                    if text == "__disconnect__":
                        break
                    if text.startswith("__e2ee_key__:"):
                        key_b64 = text.split(":", 1)[1].strip()
                        receive_e2ee_key(key_b64)
                        continue
                    decrypted_text = decrypt_message(text)
                    _queue_frontend_message(decrypted_text)
        except socket.timeout:
            continue
        except OSError:
            break
        except Exception:
            continue


def _send_disconnect_signal() -> None:
    with peer_socket_lock:
        conn = active_peer_socket

    if conn is None:
        return

    try:
        conn.sendall(b"__disconnect__\n")
    except OSError:
        pass


def _listener_loop(host: str, port: int) -> None:
    global listener_socket

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(5)
    server.settimeout(1.0)
    listener_socket = server

    try:
        while not listener_stop_event.is_set():
            try:
                conn, _addr = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            threading.Thread(target=_handle_guest_connection, args=(conn,), daemon=True).start()
    finally:
        try:
            server.close()
        except OSError:
            pass


def start_listening(port: Optional[int] = None) -> dict:
    global listener_thread

    if listener_thread is not None and listener_thread.is_alive():
        return {"status": "listening", "port": port or active_local_port or 8080}

    listen_port = port or active_local_port or 8080
    listener_stop_event.clear()
    listener_thread = threading.Thread(
        target=_listener_loop,
        args=("127.0.0.1", listen_port),
        daemon=True,
    )
    listener_thread.start()
    return {"status": "listening", "port": listen_port}


def _extract_onion_host(value: str) -> str:
    candidate = value.strip()
    onion_match = re.search(r"([a-z2-7]{16,56}\.onion)", candidate, flags=re.IGNORECASE)
    if onion_match:
        return onion_match.group(1).lower()

    if candidate.endswith(".onion"):
        return candidate.lower()

    raise ValueError("No valid .onion host found in input")


def join_room(onion_address: str, port: int) -> dict:
    global guest_socket

    controller = start_tor()
    del controller

    onion_host = _extract_onion_host(onion_address)
    socks_port = active_socks_port or _pick_random_port()

    if guest_socket is not None:
        try:
            guest_socket.close()
        except OSError:
            pass
        guest_socket = None

    client = socks.socksocket(socket.AF_INET, socket.SOCK_STREAM)
    client.set_proxy(proxy_type=socks.SOCKS5, addr="127.0.0.1", port=socks_port)
    client.settimeout(20)
    client.connect((onion_host, port))
    guest_socket = client
    _set_active_peer_socket(client)
    threading.Thread(target=_read_socket_messages, args=(client,), daemon=True).start()

    return {"status": "connected", "onion_address": onion_host, "port": port}


def send_message(text: str) -> dict:
    payload = text.strip()
    if not payload:
        return {"status": "error", "error": "Message cannot be empty"}

    with peer_socket_lock:
        conn = active_peer_socket

    if conn is None:
        return {"status": "error", "error": "No active peer socket"}

    try:
        encrypted_payload = encrypt_message(payload)
        conn.sendall((encrypted_payload + "\n").encode("utf-8"))
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}
    except OSError as exc:
        return {"status": "error", "error": str(exc)}

    return {"status": "sent"}


def close_room() -> dict:
    global active_service_id, active_local_port, active_socks_port, tor_controller, tor_process, listener_thread, listener_socket, guest_socket, active_peer_socket, _tor_data_dir

    had_room_state = active_service_id is not None or _active_room is not None or _peer_count > 0

    if tor_controller is not None and active_service_id is not None:
        try:
            remove_hidden_service = getattr(tor_controller, "remove_hidden_service", None)
            if callable(remove_hidden_service):
                remove_hidden_service(active_service_id)
            else:
                tor_controller.remove_ephemeral_hidden_service(active_service_id)
        except Exception:  # noqa: BLE001
            pass

    if tor_controller is not None:
        try:
            tor_controller.close()
        except Exception:  # noqa: BLE001
            pass

    if tor_process is not None:
        try:
            tor_process.terminate()
            tor_process.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass

    listener_stop_event.set()
    if listener_socket is not None:
        try:
            listener_socket.close()
        except OSError:
            pass

    if listener_thread is not None and listener_thread.is_alive():
        listener_thread.join(timeout=1)

    _send_disconnect_signal()

    if guest_socket is not None:
        try:
            guest_socket.close()
        except OSError:
            pass

    with peer_socket_lock:
        if active_peer_socket is not None:
            try:
                active_peer_socket.close()
            except OSError:
                pass
            active_peer_socket = None

    active_service_id = None
    active_local_port = None
    active_socks_port = None
    tor_controller = None
    tor_process = None
    if _tor_data_dir:
        try:
            shutil.rmtree(_tor_data_dir, ignore_errors=True)
        except Exception:
            pass
        _tor_data_dir = None
    listener_thread = None
    listener_socket = None
    guest_socket = None

    cleanup_room()
    return {"status": "closed"}


def generate_e2ee_key() -> Dict[str, str]:
    global _shared_key, _box
    _shared_key = nacl.utils.random(nacl.secret.SecretBox.KEY_SIZE)
    _box = nacl.secret.SecretBox(_shared_key)
    key_b64 = base64.b64encode(_shared_key).decode('utf-8')
    return {"status": "keys_generated", "key_b64": key_b64}


def receive_e2ee_key(key_b64: str) -> Dict[str, str]:
    global _shared_key, _box
    try:
        _shared_key = base64.b64decode(key_b64)
        _box = nacl.secret.SecretBox(_shared_key)
        return {"status": "e2ee_key_loaded"}
    except Exception as e:
        return {"error": _friendly_error(e)}


def encrypt_message(message: str) -> str:
    if _box is None:
        raise RuntimeError("E2EE not ready yet. Wait for secure session handshake.")
    encrypted = _box.encrypt(message.encode('utf-8'))
    return base64.b64encode(encrypted).decode('utf-8')


def decrypt_message(encrypted_b64: str) -> str:
    if _box is None:
        raise RuntimeError("E2EE not ready for incoming message.")
    encrypted = base64.b64decode(encrypted_b64)
    decrypted = _box.decrypt(encrypted)
    return decrypted.decode('utf-8')


def update_peer_count(increment: int) -> dict:
    global _peer_count, peer_socket_lock
    with peer_socket_lock:
        _peer_count += increment
    cleanup_room()
    return {"peer_count": _peer_count}


def cleanup_room() -> dict:
    global _active_room, _peer_count, _box, tor_controller
    if _peer_count <= 0 and _active_room is not None:
        try:
            if tor_controller is not None:
                tor_controller.remove_hidden_service(_active_room["onion_address"])
        except Exception:
            pass
        # clear messages and keys
        _active_room = None
        _box = None
        _peer_count = 0
        _queue_frontend_message("room_deleted")
        return {"status": "room_deleted"}
    return {"status": "still_active"}


def poll_messages() -> dict:
    with inbox_lock:
        items = list(_inbox)
        _inbox.clear()
    return {
        "messages": items,
        "encrypted": _box is not None,
        "tor_active": tor_controller is not None,
    }


def generate_e2ee_keys() -> str:
    return "mutinychat-public-key-stub"


def build_room_response(room_name: str) -> dict:
    global _active_room
    room = create_hidden_service(room_name)
    share_link = f"Join {room_name} → {room['onion_address']}"
    e2ee_keys = generate_e2ee_key()
    _active_room = {
        "friendly_name": room_name,
        "onion_address": room["onion_address"],
        "service_id": active_service_id,
        "local_port": active_local_port,
    }
    return {
        "friendly_name": room_name,
        "onion_address": room["onion_address"],
        "share_link": share_link,
        "key_b64": e2ee_keys["key_b64"],
    }


def handle_json_command(payload: dict) -> dict:
    cmd = str(payload.get("cmd", "")).strip()

    try:
        if cmd == "ping":
            return {"status": "MutinyChat backend alive"}

        if cmd == "create_room":
            room_name = str(payload.get("room_name", payload.get("name", ""))).strip() or generate_random_room_name()
            return build_room_response(room_name)

        if cmd == "generate_random_room_name":
            return {"friendly_name": generate_random_room_name()}

        if cmd == "start_tor":
            start_tor()
            return {"status": "ready"}

        if cmd == "start_listening":
            return start_listening()

        if cmd == "join_room":
            raw_address = str(
                payload.get("onion_address", payload.get("share_link", payload.get("link", payload.get("message", ""))))
            )
            raw_port = payload.get("port", 8080)
            try:
                join_port = int(raw_port)
            except (TypeError, ValueError):
                join_port = 8080
            return join_room(raw_address, join_port)

        if cmd == "receive_e2ee_key":
            key_b64 = str(payload.get("key_b64", "")).strip()
            return receive_e2ee_key(key_b64)

        if cmd == "send_message":
            text = str(payload.get("text", payload.get("message", "")))
            return send_message(text)

        if cmd == "close_room":
            return close_room()

        if cmd == "get_peer_count":
            return {"peer_count": _peer_count}

        if cmd == "poll_messages":
            return poll_messages()

        if cmd == "generate_e2ee_keys":
            public_key = generate_e2ee_keys()
            return {"public_key": public_key}

        if cmd == "echo":
            message = str(payload.get("message", ""))
            return {"echo": message}

        return {"error": f"Unknown command: {cmd}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": _friendly_error(exc)}


def run_stdio_json_loop() -> None:
    try:
        for line in sys.stdin:
            raw = line.strip()
            if not raw:
                continue

            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError("JSON payload must be an object")
                response = handle_json_command(payload)
            except Exception as exc:  # noqa: BLE001
                response = {"error": _friendly_error(exc)}

            print(json.dumps(response), flush=True)
    finally:
        close_room()


def handle_command(args: argparse.Namespace) -> bool:
    if args.command == "start_tor":
        start_tor()
        print(json.dumps({"status": "ready"}))
        return True

    if args.command == "close_room":
        print(json.dumps(close_room()))
        return True

    if args.command == "start_listening":
        print(json.dumps(start_listening()))
        return True

    if args.command == "join_room":
        onion_input = args.onion_address or args.message or ""
        join_port = args.port or 8080
        print(json.dumps(join_room(onion_input, join_port)))
        return True

    if args.command == "send_message":
        print(json.dumps(send_message(args.message or "")))
        return True

    if args.command == "generate_e2ee_keys":
        public_key = generate_e2ee_keys()
        print(json.dumps({"public_key": public_key}))
        return True

    if args.command == "create_room":
        room_name = (args.room_name or "").strip() or generate_random_room_name()
        print(json.dumps(build_room_response(room_name)))
        return True

    if args.command == "generate_random_room_name":
        print(json.dumps({"friendly_name": generate_random_room_name()}))
        return True

    if args.command == "echo" and args.message:
        print(f"MutinyChat backend echo: {args.message}")
        return True

    return False


def main() -> None:
    """Backend entry point placeholder."""
    args = parse_args()

    if args.stdio_json:
        run_stdio_json_loop()
        return

    if handle_command(args):
        return

    print("MutinyChat backend ready")


atexit.register(close_room)


if __name__ == "__main__":
    main()
