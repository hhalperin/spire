//! The tool surface, and the MCP Apps metadata that binds it to the UI.
//!
//! Names come from the draft table in `design/spire-ai/mcp-client.md`; what is
//! new here is that they have schemas, and that each one declares the `ui://`
//! resource the host should render in its place.
//!
//! Visibility is the interesting part. Per the Apps spec a tool may be visible
//! to `model`, to `app`, or both. Card plays and map annotations fire on every
//! click, so they are `app`-only: the agent should not be narrating each one,
//! and keeping them off `tools/list` for the model is what stops it trying.

use std::borrow::Cow;
use std::sync::Arc;

use rmcp::model::{JsonObject, Meta, Tool};
use serde_json::{json, Value};

/// The single UI resource. One app, many screens — `mcp-client.md` and
/// `sts-fidelity.md` both insist on one screen with everything relevant on it.
pub const APP_URI: &str = "ui://spire/app.html";

/// MCP Apps requires this exact profile on the mime type.
pub const APP_MIME: &str = "text/html;profile=mcp-app";

/// The Apps extension identifier, as negotiated at initialize.
pub const UI_EXTENSION: &str = "io.modelcontextprotocol/ui";

pub struct ToolSpec {
    pub name: &'static str,
    pub title: &'static str,
    pub description: &'static str,
    pub schema: fn() -> Value,
    /// `run.py` subcommand this maps onto.
    pub command: &'static str,
    /// Argument name -> CLI flag. Order is stable so tests can assert on it.
    pub flags: &'static [(&'static str, &'static str)],
    pub visibility: &'static [&'static str],
}

fn no_args() -> Value {
    json!({"type": "object", "properties": {}, "additionalProperties": false})
}

pub const TOOLS: &[ToolSpec] = &[
    ToolSpec {
        name: "spire_get_run",
        title: "Get run",
        description: "The whole run: class, act, floor, deck, relics, focus, and the active room \
                      if one is open. Call this first — the app renders from it.",
        schema: no_args,
        command: "state",
        flags: &[],
        visibility: &["model", "app"],
    },
    ToolSpec {
        name: "spire_map_refresh",
        title: "Show the map",
        description: "The climb: every node, which are reachable from where you stand, which are \
                      cleared, and any marks you have drawn on them.",
        schema: no_args,
        command: "map",
        flags: &[],
        visibility: &["model", "app"],
    },
    ToolSpec {
        name: "spire_enter_node",
        title: "Enter a node",
        description: "Open one room. Refuses if another room is already active — one room at a \
                      time is the product, not a limitation.",
        schema: || {
            json!({
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node id, e.g. 'r3c2'."}
                },
                "required": ["node"],
                "additionalProperties": false
            })
        },
        command: "enter",
        flags: &[("node", "--node")],
        visibility: &["model", "app"],
    },
    ToolSpec {
        name: "spire_list_hand",
        title: "List hand",
        description: "Cards legal in the active room, with cost and why each can or cannot be \
                      played.",
        schema: no_args,
        command: "hand",
        flags: &[],
        visibility: &["app"],
    },
    ToolSpec {
        name: "spire_play_card",
        title: "Play a card",
        description: "Spend energy and advance the room's progress.",
        schema: || {
            json!({
                "type": "object",
                "properties": {"card": {"type": "string", "description": "Card id, e.g. 'c-ftf'."}},
                "required": ["card"],
                "additionalProperties": false
            })
        },
        command: "play",
        flags: &[("card", "--card")],
        visibility: &["app"],
    },
    ToolSpec {
        name: "spire_end_turn",
        title: "End turn",
        description: "Refill energy and apply the room's turn effect. Unspent energy expires.",
        schema: no_args,
        command: "end-turn",
        flags: &[],
        visibility: &["app"],
    },
    ToolSpec {
        name: "spire_run_acceptance",
        title: "Run acceptance",
        description: "Run the room's deterministic check — the repo's own lint or test command, \
                      resolved from .spire/ascension.json. Reports pass, fail, or honestly \
                      reports that no command is bound.",
        schema: no_args,
        command: "acceptance",
        flags: &[],
        visibility: &["model", "app"],
    },
    ToolSpec {
        name: "spire_clear_or_flee",
        title: "Clear or flee",
        description: "Finish the room and roll a reward, or abandon it and lose the streak. \
                      Clearing an event room requires the `choice` id — its consequences are real \
                      and only apply when the choice is named.",
        schema: || {
            json!({
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["clear", "flee"]},
                    "choice": {
                        "type": "string",
                        "description": "Event choice id. Required when clearing an event room — \
                                        the choice IS the clear, and its effects only run if it \
                                        travels with it."
                    },
                    "no_notes": {
                        "type": "boolean",
                        "description": "Flee without writing anything down. Gains the Hesitation curse."
                    }
                },
                "required": ["action"],
                "additionalProperties": false
            })
        },
        command: "clear",
        flags: &[],
        visibility: &["model", "app"],
    },
    ToolSpec {
        name: "spire_reward_resolve",
        title: "Resolve a reward",
        description: "Take one offered card or skip. Skipping pays focus — refusing is the \
                      skilled play and it is scored as one.",
        schema: || {
            json!({
                "type": "object",
                "properties": {
                    "take": {"type": "string", "description": "Offer id to take."},
                    "skip": {"type": "boolean", "description": "Refuse the whole offer."},
                    "trade": {"type": "string", "description": "Card to remove when at the soft cap."}
                },
                "additionalProperties": false
            })
        },
        command: "reward",
        flags: &[("take", "--take"), ("trade", "--trade")],
        visibility: &["model", "app"],
    },
    ToolSpec {
        name: "spire_campfire",
        title: "Campfire",
        description: "Smith one card, prune one card, or dig. One action, then the floor is spent.",
        schema: || {
            json!({
                "type": "object",
                "properties": {
                    "option": {"type": "string", "enum": ["smith", "prune", "dig"]},
                    "card": {"type": "string"}
                },
                "required": ["option"],
                "additionalProperties": false
            })
        },
        command: "campfire",
        flags: &[("option", "--option"), ("card", "--card")],
        visibility: &["model", "app"],
    },
    ToolSpec {
        name: "spire_shop_list",
        title: "List the merchant's wares",
        description: "What the merchant sells and what you can afford.",
        schema: no_args,
        command: "shop",
        flags: &[],
        visibility: &["model", "app"],
    },
    ToolSpec {
        name: "spire_shop_buy",
        title: "Buy from the merchant",
        description: "Spend focus on one ware.",
        schema: || {
            json!({
                "type": "object",
                "properties": {"ware": {"type": "string"}},
                "required": ["ware"],
                "additionalProperties": false
            })
        },
        command: "shop",
        flags: &[("ware", "--buy")],
        visibility: &["model", "app"],
    },
    ToolSpec {
        name: "spire_annotate_node",
        title: "Mark a map node",
        description: "Draw on the map: mark a route, an elite, a rest you are saving. Pass \
                      'clear' to remove a mark.",
        schema: || {
            json!({
                "type": "object",
                "properties": {
                    "node": {"type": "string"},
                    "mark": {"type": "string", "description": "Short label, or 'clear'."}
                },
                "required": ["node"],
                "additionalProperties": false
            })
        },
        command: "annotate",
        flags: &[("node", "--node"), ("mark", "--mark")],
        visibility: &["app"],
    },
    ToolSpec {
        name: "spire_badges",
        title: "Badges",
        description: "What made this run unique, computed from the save. Nothing here is granted.",
        schema: no_args,
        command: "badges",
        flags: &[],
        visibility: &["model", "app"],
    },
    ToolSpec {
        name: "spire_new_run",
        title: "Start a climb",
        description: "Begin or restart a run at a given map seed.",
        schema: || {
            json!({
                "type": "object",
                "properties": {"seed": {"type": "integer", "minimum": 0}},
                "additionalProperties": false
            })
        },
        command: "new-run",
        flags: &[("seed", "--seed")],
        visibility: &["model", "app"],
    },
];

pub fn spec(name: &str) -> Option<&'static ToolSpec> {
    TOOLS.iter().find(|t| t.name == name)
}

pub fn object_of(value: Value) -> JsonObject {
    match value {
        Value::Object(map) => map,
        _ => JsonObject::new(),
    }
}

/// `_meta.ui` for a tool: which view renders it, and who may call it.
fn ui_meta(visibility: &[&str]) -> Meta {
    Meta(object_of(json!({
        "ui": {
            "resourceUri": APP_URI,
            "visibility": visibility,
        }
    })))
}

pub fn to_rmcp(spec: &ToolSpec) -> Tool {
    // `Tool` is #[non_exhaustive], so it is built through its constructor and
    // then filled in rather than written as a literal.
    let mut tool = Tool::new(
        Cow::Borrowed(spec.name),
        Cow::Borrowed(spec.description),
        Arc::new(object_of((spec.schema)())),
    );
    tool.title = Some(spec.title.to_string());
    tool.meta = Some(ui_meta(spec.visibility));
    tool
}

/// Tools the *model* may see. App-only tools stay callable over the UI bridge
/// but never appear in the agent's toolkit.
pub fn model_tools() -> Vec<Tool> {
    TOOLS
        .iter()
        .filter(|t| t.visibility.contains(&"model"))
        .map(to_rmcp)
        .collect()
}

/// `_meta.ui` for the resource itself.
///
/// The CSP lists are empty because the bundle is genuinely self-contained —
/// fonts are inlined as data URIs, there is no CDN, and nothing phones home.
/// Under the spec's deny-by-default policy that is the strongest position.
pub fn resource_meta() -> Meta {
    Meta(object_of(json!({
        "ui": {
            "csp": {
                "connectDomains": [],
                "resourceDomains": [],
                "frameDomains": [],
            },
            "prefersBorder": true,
        }
    })))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_tool_points_at_the_app() {
        for spec in TOOLS {
            let tool = to_rmcp(spec);
            let meta = tool.meta.expect("tool carries _meta");
            let uri = meta.0["ui"]["resourceUri"].as_str().unwrap();
            assert_eq!(uri, APP_URI, "{} is not bound to the app", spec.name);
        }
    }

    #[test]
    fn card_play_and_annotation_are_hidden_from_the_model() {
        let names: Vec<_> = model_tools().iter().map(|t| t.name.to_string()).collect();
        for hidden in ["spire_play_card", "spire_list_hand", "spire_annotate_node", "spire_end_turn"] {
            assert!(!names.contains(&hidden.to_string()), "{hidden} leaked to the model");
        }
        assert!(names.contains(&"spire_enter_node".to_string()));
    }

    #[test]
    fn every_tool_has_a_closed_object_schema() {
        for spec in TOOLS {
            let schema = (spec.schema)();
            assert_eq!(schema["type"], "object", "{}", spec.name);
            assert_eq!(
                schema["additionalProperties"], false,
                "{} accepts unknown properties",
                spec.name
            );
        }
    }

    #[test]
    fn every_flag_is_declared_in_its_schema() {
        for spec in TOOLS {
            let schema = (spec.schema)();
            for (arg, _) in spec.flags {
                assert!(
                    schema["properties"].get(*arg).is_some(),
                    "{} maps flag {arg} that its schema does not declare",
                    spec.name
                );
            }
        }
    }

    #[test]
    fn tool_names_are_unique() {
        let mut seen = std::collections::HashSet::new();
        for spec in TOOLS {
            assert!(seen.insert(spec.name), "duplicate tool {}", spec.name);
        }
    }

    #[test]
    fn the_resource_declares_no_external_origins() {
        let meta = resource_meta();
        assert_eq!(meta.0["ui"]["csp"]["connectDomains"].as_array().unwrap().len(), 0);
        assert_eq!(meta.0["ui"]["csp"]["resourceDomains"].as_array().unwrap().len(), 0);
    }
}
