use std::path::PathBuf;
use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::{io::Write, string::String};

use serde_json::json;

struct BackendSession {
    child: std::process::Child,
    stdin: std::process::ChildStdin,
    stdout: BufReader<std::process::ChildStdout>,
}

impl BackendSession {
    fn start() -> Result<Self, String> {
        let mut cmd = resolve_backend_command()?;
        let mut child = cmd
            .arg("--stdio-json")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|err| format!("Failed to spawn backend process: {err}"))?;

        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| "Failed to open backend stdin".to_string())?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "Failed to open backend stdout".to_string())?;

        Ok(Self {
            child,
            stdin,
            stdout: BufReader::new(stdout),
        })
    }

    fn is_running(&mut self) -> bool {
        match self.child.try_wait() {
            Ok(None) => true,
            Ok(Some(_)) => false,
            Err(_) => false,
        }
    }

    fn send_command(&mut self, payload: &serde_json::Value) -> Result<String, String> {
        let line = format!("{}\n", payload);
        self.stdin
            .write_all(line.as_bytes())
            .map_err(|err| format!("Failed writing JSON command to backend stdin: {err}"))?;
        self.stdin
            .flush()
            .map_err(|err| format!("Failed flushing backend stdin: {err}"))?;

        let mut response_line = String::new();
        loop {
            response_line.clear();
            let read = self
                .stdout
                .read_line(&mut response_line)
                .map_err(|err| format!("Failed reading backend stdout: {err}"))?;

            if read == 0 {
                return Err("Backend process exited unexpectedly".to_string());
            }

            let trimmed = response_line.trim();
            if trimmed.is_empty() {
                continue;
            }

            let parsed = serde_json::from_str::<serde_json::Value>(trimmed);
            if let Ok(serde_json::Value::Object(map)) = parsed {
                if map.get("type") == Some(&serde_json::Value::String("received".to_string())) {
                    continue;
                }
                return Ok(trimmed.to_string());
            }
        }
    }
}

static BACKEND_SESSION: OnceLock<Mutex<Option<BackendSession>>> = OnceLock::new();

fn backend_session_lock() -> &'static Mutex<Option<BackendSession>> {
    BACKEND_SESSION.get_or_init(|| Mutex::new(None))
}

#[tauri::command]
fn backend_ipc(command: String, message: Option<String>, room_name: Option<String>) -> Result<String, String> {
    run_backend_command(&command, message.as_deref(), room_name.as_deref())
}

fn backend_exec_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();

    for root in backend_search_roots() {
        candidates.push(root.join("mutinychat-backend"));
        candidates.push(root.join("backend/mutinychat-backend"));
        candidates.push(root.join("backend/dist/mutinychat-backend"));
        candidates.push(root.join("../backend/dist/mutinychat-backend"));
    }

    #[cfg(target_os = "windows")]
    {
        for root in backend_search_roots() {
            candidates.push(root.join("mutinychat-backend.exe"));
            candidates.push(root.join("backend/mutinychat-backend.exe"));
            candidates.push(root.join("backend/dist/mutinychat-backend.exe"));
            candidates.push(root.join("../backend/dist/mutinychat-backend.exe"));
        }
    }

    candidates
}

fn backend_script_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();

    for root in backend_search_roots() {
        candidates.push(root.join("backend/main.py"));
        candidates.push(root.join("../backend/main.py"));
    }

    candidates
}

fn backend_search_roots() -> Vec<PathBuf> {
    let mut roots = Vec::new();

    if let Ok(cwd) = std::env::current_dir() {
        roots.push(cwd);
    }

    if let Ok(exe_path) = std::env::current_exe() {
        if let Some(mut dir) = exe_path.parent() {
            for _ in 0..6 {
                roots.push(dir.to_path_buf());
                if let Some(parent) = dir.parent() {
                    dir = parent;
                } else {
                    break;
                }
            }
        }
    }

    roots
}

fn resolve_backend_command() -> Result<Command, String> {
    if cfg!(debug_assertions) {
        for script in backend_script_candidates() {
            if !script.exists() {
                continue;
            }

            let mut cmd = Command::new("python3");
            cmd.arg(script);
            return Ok(cmd);
        }
    }

    for path in backend_exec_candidates() {
        if !path.exists() {
            continue;
        }

        let cmd = Command::new(&path);
        return Ok(cmd);
    }

    for script in backend_script_candidates() {
        if !script.exists() {
            continue;
        }

        let mut cmd = Command::new("python3");
        cmd.arg(script);
        return Ok(cmd);
    }

    Err("Backend not found. Expected mutinychat-backend sidecar, backend/dist/mutinychat-backend, or backend/main.py relative to the project or app executable.".to_string())
}

fn run_backend_command(command: &str, message: Option<&str>, room_name: Option<&str>) -> Result<String, String> {
    let mut payload = json!({ "cmd": command });
    if let Some(msg) = message {
        payload["message"] = json!(msg);
    }
    if let Some(name) = room_name {
        payload["name"] = json!(name);
    }

    let lock = backend_session_lock();
    let mut guard = lock
        .lock()
        .map_err(|_| "Failed to lock backend session".to_string())?;

    let needs_restart = match guard.as_mut() {
        Some(session) => !session.is_running(),
        None => true,
    };

    if needs_restart {
        *guard = Some(BackendSession::start()?);
    }

    let session = guard
        .as_mut()
        .ok_or_else(|| "Backend session unavailable".to_string())?;

    match session.send_command(&payload) {
        Ok(response) => Ok(response),
        Err(_) => {
            *guard = Some(BackendSession::start()?);
            let retry_session = guard
                .as_mut()
                .ok_or_else(|| "Backend session unavailable after restart".to_string())?;
            retry_session.send_command(&payload)
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![backend_ipc])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
