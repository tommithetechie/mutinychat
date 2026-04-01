# MutinyChat

MutinyChat is a retro-styled desktop chat app (AOL/MSN-inspired) built with Tauri + Svelte + Python.

It supports direct room sharing over Tor Hidden Services with ephemeral chat behavior and encrypted message transport in the current implementation.

## Work In Progress

This project is still a work in progress.

I know it is still buggy in places, and that is expected right now. I am building this in public as a fun project and improving it over time.

## Current Project Status

This repository is an actively developed MVP/prototype with:
- Tauri v2 desktop shell
- Svelte 5 frontend (SPA mode)
- Python backend sidecar (stdio JSON protocol)
- Tor integration via `stem`
- Message encryption via `PyNaCl` (`nacl.secret.SecretBox`)
- Responsive Bootstrap-based layout with custom retro styling

## Implemented Features

- Create room (host)
- Join room using share link or pasted `.onion` value
- Automatic random room name generation
- QR code generation for room sharing
- Tor startup and status signaling
- Polling-based message delivery
- Encrypted message send/receive path once key exchange is established
- Ephemeral room cleanup behavior (`room_deleted` flow)
- Retro sound effects toggle
- Username confirmation and persistence
- Sender names shown in chat bubbles
- macOS packaging to `.app` and `.dmg`

## Architecture

### Frontend
- SvelteKit + Vite
- SPA mode (`adapter-static` + `fallback: index.html`, SSR disabled)
- Bootstrap CSS imported locally from npm (not CDN)
- Main UI in `src/App.svelte`

### Desktop Shell
- Tauri v2 Rust host in `src-tauri`
- Frontend loaded from Vite in dev and static `build/` output in release

### Backend
- Python process launched by Rust side (`backend/main.py` in dev, compiled sidecar in release)
- Stdio JSON command protocol (`--stdio-json`)
- Tor Hidden Service + SOCKS client via `stem` and `PySocks`
- E2EE key exchange/messages via `PyNaCl`

## Repository Layout

- `src/` Svelte frontend
- `backend/` Python backend and sidecar build spec
- `scripts/build-backend-sidecar.sh` macOS arm64 sidecar rebuild helper
- `scripts/test-packaged-macos.sh` packaged app smoke helper
- `src-tauri/` Rust/Tauri application

## Requirements

- Node.js + npm
- Rust toolchain + Cargo
- Python 3 with a virtual environment at `.venv`

Python backend dependencies are listed in `backend/requirements.txt`:
- `stem`
- `pynacl`
- `cryptography`
- `PySocks`

## Setup

Install frontend dependencies:

```bash
npm install
```

Create and populate Python environment (example):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt pyinstaller
```

## Development

Run desktop dev mode:

```bash
npm run tauri dev
```

Useful scripts:

- `npm run dev` run Vite only
- `npm run build` build Svelte static output
- `npm run check` run `svelte-check`
- `npm run check:watch` watch mode type checks

## Packaging

Create production bundles:

```bash
npm run tauri build
```

Current verified output paths:
- `src-tauri/target/release/bundle/macos/MutinyChat.app`
- `src-tauri/target/release/bundle/dmg/MutinyChat_0.1.0_aarch64.dmg`

### Packaging Notes

- `tauri build` automatically runs:
	- `npm run build`
	- `npm run build:backend:sidecar`
- `build:backend:sidecar` currently rebuilds only on macOS arm64 and no-ops elsewhere.
- Tauri bundle config includes external binaries:
	- `mutinychat-backend`
	- `tor`

## Packaged App Smoke Test (macOS)

Run:

```bash
npm run test:packaged:macos
```

This verifies:
- `.app` bundle exists
- bundled backend sidecar exists
- bundled `tor` binary exists
- app process starts

## Runtime Behavior Summary

- Host creates a room and receives a share link + QR.
- Guest joins via pasted link/onion.
- Messages are sent over Tor sockets.
- Incoming messages are polled by the frontend.
- Room state and messages are cleaned when room closes/disconnect conditions are met.

## Troubleshooting

### Blank/white window in packaged app

Use local bundled CSS imports (already configured) rather than CDN assets, then rebuild:

```bash
npm run tauri build
```

### Backend process exited unexpectedly

Rebuild sidecar and package again:

```bash
npm run build:backend:sidecar
npm run tauri build
```

### Tor startup failure

In dev, ensure Tor is installed or bundled path is valid. The backend checks multiple tor locations and reports friendly errors.

### Check recent app logs (macOS)

```bash
log show --last 5m --predicate 'process contains "tauri-app"' --style compact
```

## Security and Privacy Notes

- This is a prototype implementation; audit before production use.
- Encrypted transport is implemented with symmetric key exchange in-session.
- No central server is used by design.

## License

MIT
