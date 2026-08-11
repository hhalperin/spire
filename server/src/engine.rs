//! The bridge to the Python engine.
//!
//! Every piece of run state lives in `.spire/deck.json` and is mutated only by
//! `scripts/deck.py`. This crate owns the protocol and the presentation; it owns
//! no game state at all. That split is deliberate — two implementations of the
//! rules would drift, and the Python one is the one with 200-seed property tests
//! behind it.

use std::path::{Path, PathBuf};

use serde_json::Value;
use tokio::process::Command;

/// Where the plugin (and therefore `scripts/run.py`) lives.
///
/// Claude Code substitutes `${CLAUDE_PLUGIN_ROOT}` into the `.mcp.json` env
/// block, so that is the first thing we trust. Falling back to the binary's
/// own location keeps `cargo run` working during development.
pub fn plugin_root() -> PathBuf {
    if let Ok(root) = std::env::var("SPIRE_PLUGIN_ROOT") {
        if !root.is_empty() {
            return PathBuf::from(root);
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        // target/{debug,release}/spire-mcp -> server/ -> repo root
        for ancestor in exe.ancestors().skip(1) {
            if ancestor.join("scripts").join("run.py").is_file() {
                return ancestor.to_path_buf();
            }
        }
    }
    PathBuf::from(".")
}

/// The repository the run is climbing.
pub fn project_dir() -> PathBuf {
    for key in ["SPIRE_PROJECT_DIR", "CLAUDE_PROJECT_DIR"] {
        if let Ok(dir) = std::env::var(key) {
            if !dir.is_empty() {
                return PathBuf::from(dir);
            }
        }
    }
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

fn python() -> String {
    std::env::var("SPIRE_PYTHON").unwrap_or_else(|_| "python3".to_string())
}

/// A refusal from the engine, already shaped for the client.
///
/// `run.py` never exits non-zero and never prints a traceback, so anything that
/// lands here is either a structured refusal we pass straight through or a real
/// failure to launch Python — which we still turn into a renderable message
/// rather than a dropped connection.
pub fn error_value(code: &str, message: impl Into<String>) -> Value {
    serde_json::json!({
        "ok": false,
        "error": { "code": code, "message": message.into() }
    })
}

/// Invoke one `run.py` subcommand and parse its JSON reply.
pub async fn call(args: &[String]) -> Value {
    let root = plugin_root();
    let script = root.join("scripts").join("run.py");
    if !script.is_file() {
        return error_value(
            "engine_missing",
            format!(
                "Could not find scripts/run.py under {}. Set SPIRE_PLUGIN_ROOT to the plugin directory.",
                root.display()
            ),
        );
    }

    let repo = project_dir();
    let mut cmd = Command::new(python());
    cmd.arg(&script).arg("--path").arg(&repo);
    for arg in args {
        cmd.arg(arg);
    }
    cmd.current_dir(repo_or_cwd(&repo));

    match cmd.output().await {
        Ok(out) => {
            let stdout = String::from_utf8_lossy(&out.stdout);
            match serde_json::from_str::<Value>(stdout.trim()) {
                Ok(value) => value,
                Err(err) => {
                    let stderr = String::from_utf8_lossy(&out.stderr);
                    error_value(
                        "engine_output",
                        format!("run.py did not return JSON ({err}). stderr: {}", stderr.trim()),
                    )
                }
            }
        }
        Err(err) => error_value(
            "engine_launch",
            format!("Could not launch {} : {err}", python()),
        ),
    }
}

fn repo_or_cwd(repo: &Path) -> PathBuf {
    if repo.is_dir() {
        repo.to_path_buf()
    } else {
        std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
    }
}
