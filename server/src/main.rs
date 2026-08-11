//! `spire-mcp` — the Spire game client, served over MCP.
//!
//! Launched by the Claude Code plugin via `.mcp.json`, which passes
//! `${CLAUDE_PLUGIN_ROOT}` through so the binary can find `scripts/run.py`.
//!
//!     spire-mcp                 # stdio (default; what the plugin uses)
//!     spire-mcp --describe      # dump the tool + resource surface as JSON
//!
//! `--describe` exists so the protocol tests and the local demo host can read
//! the surface without speaking JSON-RPC first.

mod engine;
mod render;
mod server;
mod tools;

use rmcp::transport::stdio;
use rmcp::ServiceExt;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();

    if args.iter().any(|a| a == "--help" || a == "-h") {
        eprintln!("{}", HELP);
        return Ok(());
    }

    if args.iter().any(|a| a == "--describe") {
        let surface = serde_json::json!({
            "server": "spire",
            "version": env!("CARGO_PKG_VERSION"),
            "extension": server::ui_extension_declaration(),
            "resource": {
                "uri": tools::APP_URI,
                "mimeType": tools::APP_MIME,
                "bytes": server::app_html().len(),
            },
            "tools": tools::TOOLS.iter().map(|t| serde_json::json!({
                "name": t.name,
                "title": t.title,
                "visibility": t.visibility,
                "command": t.command,
                "schema": (t.schema)(),
            })).collect::<Vec<_>>(),
            "pluginRoot": engine::plugin_root(),
            "projectDir": engine::project_dir(),
        });
        println!("{}", serde_json::to_string_pretty(&surface)?);
        return Ok(());
    }

    // Anything written to stdout is protocol traffic, so diagnostics go to
    // stderr. A stray println here is a parse error on the other end.
    let service = server::Spire::new().serve(stdio()).await?;
    service.waiting().await?;
    Ok(())
}

const HELP: &str = "\
spire-mcp — Spire's MCP server

USAGE:
    spire-mcp              serve over stdio (default)
    spire-mcp --describe   print the tool and resource surface as JSON
    spire-mcp --help       this message

ENVIRONMENT:
    SPIRE_PLUGIN_ROOT   where the spire plugin lives (holds scripts/run.py)
    SPIRE_PROJECT_DIR   the repository being climbed (falls back to
                        CLAUDE_PROJECT_DIR, then the working directory)
    SPIRE_PYTHON        python interpreter to use (default: python3)
";
