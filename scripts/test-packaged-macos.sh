#!/usr/bin/env bash
set -euo pipefail

APP_PATH="src-tauri/target/release/bundle/macos/MutinyChat.app"
APP_ABS_PATH="$PWD/$APP_PATH"
APP_BIN="$APP_PATH/Contents/MacOS/tauri-app"
BACKEND_BIN="$APP_PATH/Contents/MacOS/mutinychat-backend"
TOR_BIN="$APP_PATH/Contents/MacOS/tor"

fail() {
  echo "[FAIL] $1"
  exit 1
}

[[ -d "$APP_PATH" ]] || fail "Packaged app not found at $APP_PATH. Run: npm run tauri build"
[[ -x "$APP_BIN" ]] || fail "Main app binary missing: $APP_BIN"
[[ -x "$BACKEND_BIN" ]] || fail "Backend sidecar missing: $BACKEND_BIN"
[[ -x "$TOR_BIN" ]] || fail "Tor sidecar missing: $TOR_BIN"

echo "[OK] Bundle files present (app + backend + tor)."

echo "Launching packaged app (detached, no terminal)..."
open -na "$APP_ABS_PATH"

sleep 3
if pgrep -f "$APP_ABS_PATH/Contents/MacOS/tauri-app" >/dev/null; then
  echo "[OK] App process is running."
else
  fail "App process did not appear after launch."
fi

echo
echo "Manual smoke checklist (should all pass):"
echo "1) Retro window is visible."
echo "2) Create Room works without runtime errors."
echo "3) No missing file errors are shown in the UI."
echo
echo "If needed, inspect recent app logs with:"
echo "log show --last 5m --predicate 'process contains \"tauri-app\"' --style compact"
