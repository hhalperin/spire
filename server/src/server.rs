//! The MCP server: `ServerHandler` implemented by hand.
//!
//! Not the `#[tool]` macros, because every tool has to carry `_meta.ui` and the
//! visibility split, and hand-rolling `list_tools` is the honest way to keep
//! that under our control.
//!
//! Each tool result carries the same state three ways:
//!   * `content[0].text` — the terminal rendering, for hosts without MCP Apps
//!     (Claude Code, notably)
//!   * `structuredContent` — the machine-readable payload the app renders from
//!   * `_meta.ui.resourceUri` — the view a host *with* Apps should render instead
//!
//! One call, both surfaces, no host detection anywhere.

use std::future::Future;

use rmcp::handler::server::ServerHandler;
use rmcp::model::{
    Annotated, CallToolRequestParams, CallToolResult, Content, Implementation, ListResourcesResult,
    ListToolsResult, PaginatedRequestParams, ProtocolVersion, RawResource, ReadResourceRequestParams,
    ReadResourceResult, ResourceContents, ServerCapabilities, ServerInfo,
};
use rmcp::service::RequestContext;
use rmcp::{ErrorData as McpError, RoleServer};
use serde_json::{json, Value};

use crate::engine;
use crate::render;
use crate::tools::{self, APP_MIME, APP_URI, UI_EXTENSION};

/// The bundled app, compiled into the binary so there is no runtime file lookup
/// and no way for the served HTML to drift from the server that advertises it.
const APP_HTML: &str = include_str!("../assets/app.html");

#[derive(Clone, Default)]
pub struct Spire;

impl Spire {
    pub fn new() -> Self {
        Self
    }
}

/// Turn one `run.py` reply into a dual-surface tool result.
fn result_from(payload: Value, text: String) -> CallToolResult {
    let is_error = payload.get("ok") == Some(&Value::Bool(false));
    let mut result = CallToolResult::success(vec![Content::text(text)]);
    result.structured_content = Some(payload);
    result.is_error = Some(is_error);
    // The host reads this to know which view renders the result in its place.
    result.meta = tools::to_rmcp(&tools::TOOLS[0]).meta;
    result
}

/// Pick the right renderer for whatever the engine just returned.
///
/// The format docs assign each facet a hero region; this is where that decision
/// gets made for the text surface.
fn render_payload(command: &str, payload: &Value) -> String {
    if payload.get("ok") == Some(&Value::Bool(false)) {
        let err = payload.get("error").cloned().unwrap_or(Value::Null);
        return render::error(&err);
    }
    let state = payload.get("state").cloned().unwrap_or(Value::Null);
    let empty_hand = json!([]);

    match command {
        "map" => render::map(
            payload.get("map").unwrap_or(&Value::Null),
            payload.get("state").unwrap_or(&Value::Null),
        ),
        "badges" => render::badges(payload.get("badges").unwrap_or(&Value::Null)),
        "shop" if payload.get("wares").is_some() => render::shop(payload, &state),
        "state" => {
            // Title/deck view unless a room is open, in which case the room owns
            // the screen — chrome.md's single-task rule made visible.
            let room = state.get("room").cloned().unwrap_or(Value::Null);
            if room.is_null() {
                if let Some(pending) = state.get("pending_reward").filter(|v| !v.is_null()) {
                    return render::reward(pending, &state);
                }
                render::deck_view(&state)
            } else {
                let hand = state.get("hand").cloned().unwrap_or(empty_hand);
                render::room(&room, &hand, &state)
            }
        }
        _ => {
            if let Some(pending) = payload.get("reward").filter(|v| !v.is_null()) {
                return render::reward(pending, &state);
            }
            let room = payload
                .get("room")
                .cloned()
                .filter(|v| !v.is_null())
                .or_else(|| state.get("room").cloned().filter(|v| !v.is_null()));
            match room {
                Some(room) => {
                    let hand = payload
                        .get("hand")
                        .cloned()
                        .or_else(|| state.get("hand").cloned())
                        .unwrap_or(empty_hand);
                    render::room(&room, &hand, &state)
                }
                None if !state.is_null() => render::deck_view(&state),
                None => serde_json::to_string_pretty(payload).unwrap_or_default(),
            }
        }
    }
}

/// Map a tool call's arguments onto `run.py`'s argv.
fn build_argv(spec: &tools::ToolSpec, args: &Value) -> Vec<String> {
    let mut argv = vec![spec.command.to_string()];

    // Two tools are one engine verb with a mode switch, so they get handled here
    // rather than pretending the mapping is uniform.
    if spec.name == "spire_clear_or_flee" {
        let action = args.get("action").and_then(Value::as_str).unwrap_or("clear");
        argv[0] = action.to_string();
        if action == "flee" && args.get("no_notes") == Some(&Value::Bool(true)) {
            argv.push("--no-notes".to_string());
        }
        return argv;
    }
    if spec.name == "spire_reward_resolve" && args.get("skip") == Some(&Value::Bool(true)) {
        argv.push("--skip".to_string());
        return argv;
    }

    for (arg, flag) in spec.flags {
        if let Some(value) = args.get(*arg) {
            let rendered = match value {
                Value::String(s) => Some(s.clone()),
                Value::Number(n) => Some(n.to_string()),
                Value::Bool(true) => None,
                _ => None,
            };
            match rendered {
                Some(v) => {
                    argv.push((*flag).to_string());
                    argv.push(v);
                }
                None => {
                    if value == &Value::Bool(true) {
                        argv.push((*flag).to_string());
                    }
                }
            }
        }
    }
    argv
}

impl ServerHandler for Spire {
    fn get_info(&self) -> ServerInfo {
        let mut implementation =
            Implementation::new("spire", env!("CARGO_PKG_VERSION"));
        implementation.title = Some("Spire".to_string());
        implementation.website_url = Some("https://github.com/hhalperin/spire".to_string());

        let mut info = ServerInfo::default();
        info.protocol_version = ProtocolVersion::default();
        info.capabilities = ServerCapabilities::builder()
            .enable_tools()
            .enable_resources()
            .build();
        info.server_info = implementation;
        info.instructions = Some(
                "Spire turns a repository into a roguelike run. Call spire_get_run first, then \
                 spire_map_refresh to see the climb. The player picks one node; you never pick \
                 for them. An intent is only shown when a deterministic check stands behind it — \
                 if a room has no intent, say so rather than guessing what it will do. Skipping a \
                 card reward is the skilled play, not a failure to engage."
                .to_string(),
        );
        info
    }

    fn list_tools(
        &self,
        _request: Option<PaginatedRequestParams>,
        _context: RequestContext<RoleServer>,
    ) -> impl Future<Output = Result<ListToolsResult, McpError>> + Send + '_ {
        std::future::ready(Ok(ListToolsResult::with_all_items(tools::model_tools())))
    }

    fn list_resources(
        &self,
        _request: Option<PaginatedRequestParams>,
        _context: RequestContext<RoleServer>,
    ) -> impl Future<Output = Result<ListResourcesResult, McpError>> + Send + '_ {
        std::future::ready(Ok(ListResourcesResult::with_all_items(vec![Annotated::new(
                RawResource {
                    uri: APP_URI.to_string(),
                    name: "spire".to_string(),
                    title: Some("Spire".to_string()),
                    description: Some(
                        "The Spire game client: map, room, hand, reward, campfire, shop."
                            .to_string(),
                    ),
                    mime_type: Some(APP_MIME.to_string()),
                    size: Some(APP_HTML.len() as u32),
                    icons: None,
                    meta: Some(tools::resource_meta()),
                },
                None,
            )])))
    }

    fn read_resource(
        &self,
        request: ReadResourceRequestParams,
        _context: RequestContext<RoleServer>,
    ) -> impl Future<Output = Result<ReadResourceResult, McpError>> + Send + '_ {
        let found = request.uri == APP_URI;
        std::future::ready(if found {
            Ok(ReadResourceResult::new(vec![
                ResourceContents::TextResourceContents {
                    uri: APP_URI.to_string(),
                    mime_type: Some(APP_MIME.to_string()),
                    text: APP_HTML.to_string(),
                    meta: Some(tools::resource_meta()),
                },
            ]))
        } else {
            Err(McpError::resource_not_found(
                format!("no resource at {}", request.uri),
                None,
            ))
        })
    }

    async fn call_tool(
        &self,
        request: CallToolRequestParams,
        _context: RequestContext<RoleServer>,
    ) -> Result<CallToolResult, McpError> {
        {
            let Some(spec) = tools::spec(&request.name) else {
                let payload = engine::error_value(
                    "no_such_tool",
                    format!("spire has no tool named {}", request.name),
                );
                let text = render_payload("", &payload);
                return Ok(result_from(payload, text));
            };

            let args = request
                .arguments
                .map(Value::Object)
                .unwrap_or_else(|| json!({}));
            let argv = build_argv(spec, &args);
            let command = argv[0].clone();
            let payload = engine::call(&argv).await;
            let text = render_payload(&command, &payload);
            Ok(result_from(payload, text))
        }
    }
}

/// The extension block a server advertises so an Apps-capable host knows to
/// look for `ui://` resources. Sent alongside `initialize`.
pub fn ui_extension_declaration() -> Value {
    json!({ UI_EXTENSION: { "mimeTypes": [APP_MIME] } })
}

/// The bundled app, exposed for the protocol tests.
pub fn app_html() -> &'static str {
    APP_HTML
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clear_and_flee_share_one_tool_but_two_verbs() {
        let spec = tools::spec("spire_clear_or_flee").unwrap();
        assert_eq!(build_argv(spec, &json!({"action": "clear"})), vec!["clear"]);
        assert_eq!(
            build_argv(spec, &json!({"action": "flee", "no_notes": true})),
            vec!["flee", "--no-notes"]
        );
    }

    #[test]
    fn skip_is_a_flag_not_a_value() {
        let spec = tools::spec("spire_reward_resolve").unwrap();
        assert_eq!(build_argv(spec, &json!({"skip": true})), vec!["reward", "--skip"]);
        assert_eq!(
            build_argv(spec, &json!({"take": "c-ftf"})),
            vec!["reward", "--take", "c-ftf"]
        );
    }

    #[test]
    fn numeric_arguments_survive_the_trip() {
        let spec = tools::spec("spire_new_run").unwrap();
        assert_eq!(build_argv(spec, &json!({"seed": 7})), vec!["new-run", "--seed", "7"]);
    }

    #[test]
    fn shop_buy_maps_ware_onto_the_buy_flag() {
        let spec = tools::spec("spire_shop_buy").unwrap();
        assert_eq!(
            build_argv(spec, &json!({"ware": "w-char"})),
            vec!["shop", "--buy", "w-char"]
        );
    }

    #[test]
    fn a_refusal_renders_as_text_and_is_flagged_as_an_error() {
        let payload = engine::error_value("room_active", "Room active: Flaky Suite");
        let text = render_payload("enter", &payload);
        assert!(text.contains("Flaky Suite"), "{text}");
        let result = result_from(payload, text);
        assert_eq!(result.is_error, Some(true));
        assert!(!result.content.is_empty());
    }

    #[test]
    fn the_bundled_app_is_a_real_document_with_the_bridge_in_it() {
        assert!(APP_HTML.len() > 2000, "app.html looks empty");
        assert!(APP_HTML.contains("ui/initialize"), "the app never handshakes");
        assert!(
            !APP_HTML.contains("fonts.googleapis.com"),
            "the app must not reach a CDN under a deny-by-default CSP"
        );
    }

    #[test]
    fn the_extension_declaration_names_the_apps_mime_type() {
        let decl = ui_extension_declaration();
        assert_eq!(decl[UI_EXTENSION]["mimeTypes"][0], APP_MIME);
    }
}
