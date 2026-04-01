#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
VENV_PYINSTALLER="$ROOT_DIR/.venv/bin/pyinstaller"

if [[ ! -d "$BACKEND_DIR" ]]; then
  echo "[WARN] backend directory not found, skipping sidecar build"
  exit 0
fi

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "[INFO] Non-macOS-arm64 environment detected; skipping local sidecar rebuild"
  exit 0
fi

if [[ ! -x "$VENV_PYINSTALLER" ]]; then
  echo "[WARN] pyinstaller not found at $VENV_PYINSTALLER, skipping sidecar rebuild"
  exit 0
fi

echo "[INFO] Rebuilding backend sidecar (macOS arm64)..."
cd "$BACKEND_DIR"
"$VENV_PYINSTALLER" --clean --noconfirm mutinychat-backend-aarch64-apple-darwin.spec >/dev/null
echo "[OK] Rebuilt backend/dist/mutinychat-backend-aarch64-apple-darwin"
