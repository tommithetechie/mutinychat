// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::process::{Command, Stdio};

fn backend_exec_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();

    if let Ok(cwd) = std::env::current_dir() {
        candidates.push(cwd.join("backend/dist/mutinychat-backend"));
        candidates.push(cwd.join("../backend/dist/mutinychat-backend"));
    }

    #[cfg(target_os = "windows")]
    {
        if let Ok(cwd) = std::env::current_dir() {
            candidates.push(cwd.join("backend/dist/mutinychat-backend.exe"));
            candidates.push(cwd.join("../backend/dist/mutinychat-backend.exe"));
        }
    }

    candidates
}

fn launch_backend_on_startup() {
    for path in backend_exec_candidates() {
        if !path.exists() {
            continue;
        }

        let spawn_result = Command::new(&path)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn();

        match spawn_result {
            Ok(_) => return,
            Err(err) => {
                eprintln!("Failed to launch backend at {}: {err}", path.display());
            }
        }
    }
}

fn main() {
    launch_backend_on_startup();
    tauri_app_lib::run()
}
