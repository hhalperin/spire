//! The terminal surface.
//!
//! Claude Code is the plugin's primary host and it is *not* on the MCP Apps
//! support matrix, so every tool result carries a text rendering alongside the
//! `ui://` app. This module is that rendering. It follows the same region order
//! the format docs froze in `design/spire-ai/ui/formats/`, so the two surfaces
//! agree about what owns the top of the screen.
//!
//! Unicode box drawing only — no ANSI escapes. A host that does not interpret
//! escapes would print them as literal garbage, and this text is read by a model
//! as often as by a person. It also has to survive a *chat panel*: the VS Code
//! extension renders tool output as markdown in a conversation, not a terminal,
//! which is a second reason escapes are out and fixed-width glyphs are in.
//!
//! Every room draws its own choices. A rest, chest or shop room carries
//! `options` / `offer` / `wares` rather than `intents`, and where there is no
//! app this text *is* the screen, not a summary of it — see `choices`.
//!
//! Glyphs come from `design/spire-ai/ui/ENTITY_STANDARDS.md`. The silhouette
//! carries the meaning so the surface survives having no colour at all, which is
//! exactly the situation here.

use serde_json::Value;

pub const W: usize = 68;

pub fn glyph(kind: &str) -> &'static str {
    match kind {
        "monster" => "✦",
        "elite" => "✸",
        "rest" => "▲",
        "shop" => "◆",
        "treasure" => "▮",
        "boss" => "☠",
        // The demo shipped without this one, so a resolved event node rendered
        // the literal string "undefined" on the map.
        "event" => "◇",
        _ => "?",
    }
}

fn kind_label(kind: &str) -> &'static str {
    match kind {
        "monster" => "Monster room",
        "elite" => "Elite room",
        "rest" => "Campfire",
        "shop" => "Merchant",
        "treasure" => "Chest",
        "boss" => "Act boss",
        "event" => "Event",
        _ => "Unknown node",
    }
}

/// Width in terminal cells. The glyph set is BMP symbols and box drawing, which
/// render single-width; counting chars rather than bytes is what matters here.
fn width(s: &str) -> usize {
    s.chars().count()
}

fn pad(s: &str, n: usize) -> String {
    let len = width(s);
    if len >= n {
        s.chars().take(n).collect()
    } else {
        format!("{s}{}", " ".repeat(n - len))
    }
}

pub fn rule(kind: char) -> String {
    let line: String = std::iter::repeat_n(kind, W - 2).collect();
    format!("├{line}┤")
}

pub fn top() -> String {
    format!("┌{}┐", "─".repeat(W - 2))
}

pub fn bottom() -> String {
    format!("└{}┘", "─".repeat(W - 2))
}

pub fn row(s: &str) -> String {
    format!("│ {} │", pad(s, W - 4))
}

pub fn blank() -> String {
    row("")
}

fn str_of<'a>(v: &'a Value, key: &str) -> &'a str {
    v.get(key).and_then(Value::as_str).unwrap_or("")
}

fn int_of(v: &Value, key: &str) -> i64 {
    v.get(key).and_then(Value::as_i64).unwrap_or(0)
}

/// Energy as filled and empty pips. `●●○` is the whole budget at a glance.
fn pips(now: i64, max: i64) -> String {
    let mut out = String::new();
    for i in 0..max.max(now) {
        out.push(if i < now { '●' } else { '○' });
    }
    out
}

/// The per-room progress meter. Deliberately not a health bar: it fills upward
/// toward a target and resets with the room.
fn meter(now: i64, max: i64) -> String {
    let cells = 20i64;
    let filled = if max <= 0 { 0 } else { (now * cells / max).clamp(0, cells) };
    format!(
        "{}{}",
        "█".repeat(filled as usize),
        "░".repeat((cells - filled) as usize)
    )
}

// --------------------------------------------------------------------------- //
// chrome
// --------------------------------------------------------------------------- //

/// Act · Floor · Class · Deck · Ascension · Focus.
///
/// Deck size is on this line on purpose: ENTITY_STANDARDS rule 3 requires the
/// live count during a run, because an invisible deck cost means refusing a
/// card never feels like it paid.
pub fn chrome(state: &Value) -> Vec<String> {
    let deck_size = int_of(state, "deck_size");
    let cap = int_of(state, "soft_cap");
    let cap_mark = if state.get("over_soft_cap") == Some(&Value::Bool(true)) {
        " ⚠"
    } else {
        ""
    };
    let mut lines = vec![
        row(&format!(
            "SPIRE · {} · Floor {} · {}",
            str_of(state, "act_label"),
            int_of(state, "floor"),
            str_of(state, "class_name"),
        )),
        row(&format!(
            "Deck {deck_size}/{cap}{cap_mark}   Focus ◈{}   Streak {}   Ascension A{}",
            int_of(state, "focus"),
            int_of(state, "streak"),
            int_of(state, "ascension"),
        )),
    ];

    // chrome.md: the banner is the single-task policy made visible.
    if let Some(room) = state.get("room").filter(|r| !r.is_null()) {
        lines.push(rule('─'));
        lines.push(row(&format!(
            "● ROOM ACTIVE: {} · {}",
            str_of(room, "name"),
            str_of(room, "room_type")
        )));
        lines.push(row("  Finish it or flee — no second room."));
    }
    lines
}

// --------------------------------------------------------------------------- //
// map
// --------------------------------------------------------------------------- //

/// The climb, boss at the top, floor 1 at the bottom.
///
/// Only reachable nodes are offered, because commit-to-edge is the mechanic that
/// makes the map a plan rather than a menu.
pub fn map(map: &Value, state: &Value) -> String {
    let mut out = vec![top()];
    out.extend(chrome(state));
    out.push(rule('─'));

    let boss = map.get("boss").cloned().unwrap_or(Value::Null);
    out.push(row(&format!(
        "MAP · {}   boss: ☠ {}",
        str_of(map, "act_label"),
        str_of(&boss, "name")
    )));
    out.push(row(&format!("  \"{}\"", str_of(&boss, "intent"))));
    out.push(rule('─'));

    let empty = vec![];
    let nodes = map.get("nodes").and_then(Value::as_array).unwrap_or(&empty);
    let cols = int_of(map, "cols").max(1) as usize;
    let rows = int_of(map, "rows").max(1);

    let mut legal_ids: Vec<String> = Vec::new();

    for r in (0..=rows).rev() {
        let mut lane = String::new();
        for c in 0..cols {
            let node = nodes.iter().find(|n| int_of(n, "row") == r && int_of(n, "col") == c as i64);
            match node {
                None => lane.push_str("    "),
                Some(n) => {
                    let kind = n
                        .get("resolved")
                        .and_then(Value::as_str)
                        .filter(|s| !s.is_empty())
                        .unwrap_or_else(|| str_of(n, "kind"));
                    let g = glyph(kind);
                    let cleared = n.get("cleared") == Some(&Value::Bool(true));
                    let current = n.get("current") == Some(&Value::Bool(true));
                    let legal = n.get("legal") == Some(&Value::Bool(true));
                    if legal {
                        legal_ids.push(str_of(n, "id").to_string());
                    }
                    // Bracket the choices, dot the history, arrow where you stand.
                    let cell = if current {
                        format!("▸{g} ")
                    } else if legal {
                        format!("[{g}]")
                    } else if cleared {
                        format!(" {g}·")
                    } else {
                        format!(" {g} ")
                    };
                    lane.push_str(&pad(&cell, 4));
                }
            }
        }
        let marks: Vec<String> = nodes
            .iter()
            .filter(|n| int_of(n, "row") == r)
            .filter_map(|n| {
                n.get("mark")
                    .and_then(Value::as_str)
                    .map(|m| format!("{}:{m}", str_of(n, "id")))
            })
            .collect();
        let suffix = if marks.is_empty() {
            String::new()
        } else {
            format!("  ✎ {}", marks.join(", "))
        };
        let floor_label = if r == rows {
            "BOSS".to_string()
        } else {
            format!("{:>4}", r + 1)
        };
        out.push(row(&format!("{floor_label} {lane}{suffix}")));
    }

    out.push(rule('─'));
    out.push(row("✦ monster  ✸ elite  ▲ campfire  ◆ shop  ▮ chest  ◇ event  ? unknown"));
    out.push(row("[x] reachable   ▸x you are here   x· cleared"));

    if state.get("active_room").map(|v| !v.is_null()).unwrap_or(false) {
        out.push(rule('─'));
        out.push(row("Map locked: a room is open. Clear it or flee."));
    } else if legal_ids.is_empty() {
        out.push(rule('─'));
        out.push(row("No reachable nodes. The act is over."));
    } else {
        out.push(rule('─'));
        out.push(row(&format!("Reachable: {}", legal_ids.join("  "))));
    }

    out.push(bottom());
    out.join("\n")
}

// --------------------------------------------------------------------------- //
// room, intent, combat
// --------------------------------------------------------------------------- //

fn intent_line(intent: &Value) -> String {
    let kind = str_of(intent, "kind");
    let mark = match kind {
        "attack" => "⚔",
        "defend" => "⛨",
        "buff" => "↑",
        "debuff" => "↓",
        "status" => "⬚",
        "affliction" => "▧",
        "heal" => "+",
        "summon" => "◉",
        "deathblow" => "☠",
        "cowardly" => "↷",
        "stunned" => "✳",
        "sleeping" => "z",
        _ => "?",
    };
    // StS shows the exact number on an attack and nothing on a block, because a
    // bracketed middle tested worse than either extreme. Where we have a count,
    // we print it; where we do not, we print the glyph alone and never a range.
    let count = intent.get("count").and_then(Value::as_i64);
    let tier = intent.get("tier").and_then(Value::as_i64);
    let magnitude = match (kind, count, tier) {
        ("attack", _, Some(t)) => format!(" {t}"),
        (_, Some(c), _) => format!(" {c}"),
        _ => String::new(),
    };
    let sensor = intent
        .get("sensor")
        .and_then(Value::as_str)
        .map(|s| format!("  [{s}]"))
        .unwrap_or_else(|| "  [no sensor]".to_string());
    format!("{mark}{magnitude}  {}{sensor}", str_of(intent, "text"))
}

/// The choices a rest, chest or shop room offers, or None if it is a fight.
///
/// Each returns the verb the player has to name, because in a text host there
/// is nothing to click: the id in brackets is the argument. The three rooms
/// share a shape — a heading, then rows of "what it is / what it costs / what
/// to say" — so they share a function.
fn choices(room: &Value, state: &Value) -> Option<Vec<String>> {
    let empty = vec![];
    let mut out = Vec::new();

    if let Some(options) = room.get("options").and_then(Value::as_array) {
        out.push(row("CAMPFIRE · one of these, then the floor is spent"));
        out.push(blank());
        let relics = state.get("relics").and_then(Value::as_array).unwrap_or(&empty);
        for option in options {
            // Dig is gated on a relic. Listing it as available to a player who
            // cannot take it is the same lie the map's `legal` flag exists to
            // avoid, so it is marked rather than hidden.
            let needs = str_of(option, "requires_relic");
            let held = needs.is_empty()
                || relics.iter().any(|r| str_of(r, "id") == needs);
            let mark = if held { "▸" } else { "·" };
            out.push(row(&format!(
                "{mark} {}  {}",
                pad(str_of(option, "name"), 8),
                str_of(option, "blurb")
            )));
            if !held {
                out.push(row(&format!("    needs the {needs} relic")));
            }
        }
        out.push(blank());
        out.push(row("  --option smith|prune|dig   (smith and prune take --card)"));
        return Some(out);
    }

    if let Some(offer) = room.get("offer").filter(|v| !v.is_null()) {
        let title = str_of(offer, "title");
        if title.is_empty() && str_of(offer, "ref").is_empty() {
            return None;
        }
        out.push(row("CHEST · what is inside is already decided"));
        out.push(blank());
        out.push(row(&format!(
            "◇ {}  ({})",
            if title.is_empty() { str_of(offer, "ref") } else { title },
            str_of(offer, "kind")
        )));
        for line in wrap(str_of(offer, "body"), W - 8) {
            out.push(row(&format!("    {line}")));
        }
        out.push(blank());
        out.push(row("  Clear the room, then take or skip the offer."));
        return Some(out);
    }

    if let Some(wares) = room.get("wares").and_then(Value::as_array) {
        out.push(row(&format!(
            "THE MERCHANT   you hold ◈{}",
            int_of(state, "focus")
        )));
        out.push(blank());
        for ware in wares {
            let detail = ware.get("detail").cloned().unwrap_or(Value::Null);
            out.push(row(&format!(
                "· ◈{}  {}  ({})  [{}]",
                int_of(ware, "price"),
                pad(ware_label(&detail, ware), 22),
                str_of(ware, "kind"),
                str_of(ware, "id")
            )));
        }
        out.push(blank());
        out.push(row("  spire_shop_list prices these against the focus you hold."));
        return Some(out);
    }

    None
}

pub fn room(room: &Value, hand: &Value, state: &Value) -> String {
    let mut out = vec![top()];
    out.extend(chrome(state));
    out.push(rule('─'));

    let kind = str_of(room, "kind");
    out.push(row(&format!(
        "{} {}  ·  {}",
        glyph(kind),
        str_of(room, "name"),
        kind_label(kind)
    )));
    if !str_of(room, "telegraph").is_empty() {
        out.push(row(&format!("  Telegraph: {}", str_of(room, "telegraph"))));
    }
    if !str_of(room, "blurb").is_empty() {
        out.push(blank());
        out.push(row(str_of(room, "blurb")));
    }

    // A rest, chest or shop room offers choices rather than a fight, and those
    // choices are the screen. They used to be dropped: `render_payload` routes
    // every non-map verb here, and this function read `intents` and nothing
    // else — so entering a campfire drew its name, then "INTENT: none shown",
    // and never mentioned smith, prune or dig. In a host that cannot render the
    // app this text *is* the game, so the room was unplayable by reading.
    if let Some(lines) = choices(room, state) {
        out.push(rule('─'));
        out.extend(lines);
        out.push(bottom());
        return out.join("\n");
    }

    // intent.md: the threat owns the hero region, before the hand is usable.
    let empty = vec![];
    let intents = room.get("intents").and_then(Value::as_array).unwrap_or(&empty);
    out.push(rule('─'));
    if intents.is_empty() {
        out.push(row("INTENT: none shown."));
        out.push(row("  No deterministic check stands behind this room, so"));
        out.push(row("  nothing is telegraphed. A guess would be worse."));
    } else {
        out.push(row("INTENT"));
        for intent in intents {
            out.push(row(&format!("  {}", intent_line(intent))));
        }
    }

    if let Some(acc) = room.get("acceptance").filter(|v| !v.is_null()) {
        out.push(rule('─'));
        out.push(row("ACCEPTANCE"));
        let detail = match str_of(acc, "type") {
            "command" => format!("  run the repo's {} gate → exit 0", str_of(acc, "cmd")),
            "file_exists" => format!("  {} → {}", str_of(acc, "path"), str_of(acc, "expect")),
            other => format!("  {other}: {}", str_of(acc, "expect")),
        };
        out.push(row(&detail));
        if let Some(result) = room.get("acceptance_result").filter(|v| !v.is_null()) {
            out.push(row(&format!(
                "  last run: {} {}",
                str_of(result, "result").to_uppercase(),
                str_of(result, "reason")
            )));
        }
    }

    // combat.md: enemy state is the hero; the hand is the tool row beneath it.
    if room.get("clear_at").is_some() {
        let progress = int_of(room, "progress");
        let target = int_of(room, "clear_at");
        out.push(rule('─'));
        out.push(row(&format!(
            "PROGRESS  {}  {progress}/{target}",
            meter(progress, target)
        )));
        out.push(row(&format!(
            "ENERGY    {}   turn {}",
            pips(int_of(room, "energy"), int_of(room, "energy_max")),
            int_of(room, "turn")
        )));

        let cards = hand.as_array().unwrap_or(&empty);
        if !cards.is_empty() {
            out.push(rule('─'));
            out.push(row("HAND"));
            for card in cards {
                let playable = card.get("playable") == Some(&Value::Bool(true));
                let bullet = if playable { "▸" } else { " " };
                let note = if playable {
                    format!("+{}", int_of(card, "progress"))
                } else {
                    str_of(card, "reason").to_string()
                };
                out.push(row(&format!(
                    "{bullet} ({}) {}  — {}  [{}]",
                    int_of(card, "cost"),
                    pad(str_of(card, "title"), 22),
                    note,
                    str_of(card, "id"),
                )));
            }
        }
    }

    if let Some(event) = room.get("event").filter(|v| !v.is_null()) {
        out.push(rule('─'));
        out.push(row(str_of(event, "title")));
        out.push(blank());
        for line in wrap(str_of(event, "body"), W - 6) {
            out.push(row(&format!("  {line}")));
        }
        out.push(blank());
        for choice in event.get("choices").and_then(Value::as_array).unwrap_or(&empty) {
            out.push(row(&format!(
                "  [{}] {} — {}",
                str_of(choice, "id"),
                str_of(choice, "label"),
                str_of(choice, "consequence")
            )));
        }
    }

    let log = room.get("log").and_then(Value::as_array).unwrap_or(&empty);
    if !log.is_empty() {
        out.push(rule('─'));
        out.push(row("LOG"));
        for line in log.iter().rev().take(4).collect::<Vec<_>>().into_iter().rev() {
            out.push(row(&format!("  {}", line.as_str().unwrap_or(""))));
        }
    }

    out.push(bottom());
    out.join("\n")
}

// --------------------------------------------------------------------------- //
// reward
// --------------------------------------------------------------------------- //

/// reward.md deliberately inverts normal upgrade-shop UI: Skip owns the hero
/// region and is never styled as the disabled option.
pub fn reward(reward: &Value, state: &Value) -> String {
    let mut out = vec![top()];
    out.extend(chrome(state));
    out.push(rule('─'));
    out.push(row("ROOM CLEARED"));
    out.push(rule('═'));
    out.push(row("  ▶  SKIP  —  take nothing"));
    out.push(row(&format!(
        "     Skipping is skilled play.  Pays ◈{}",
        int_of(reward, "skip_payout")
    )));
    out.push(rule('═'));
    out.push(row("Offers (secondary)"));

    let empty = vec![];
    for offer in reward.get("offers").and_then(Value::as_array).unwrap_or(&empty) {
        let rarity = str_of(offer, "rarity");
        let notch = match rarity {
            "rare" => "◆◆◆",
            "uncommon" => "◆◆ ",
            _ => "◆  ",
        };
        out.push(blank());
        out.push(row(&format!(
            "  {notch} ({}) {}  [{}]",
            int_of(offer, "cost"),
            str_of(offer, "title"),
            str_of(offer, "id")
        )));
        out.push(row(&format!("      {}", str_of(offer, "body"))));
        let rooms = offer
            .get("rooms")
            .and_then(Value::as_array)
            .map(|r| {
                if r.is_empty() {
                    "every room".to_string()
                } else {
                    r.iter().filter_map(Value::as_str).collect::<Vec<_>>().join(", ")
                }
            })
            .unwrap_or_default();
        out.push(row(&format!("      {rarity} · legal in {rooms}")));
    }

    if state.get("over_soft_cap") == Some(&Value::Bool(true)) {
        out.push(rule('─'));
        out.push(row("Deck is at the soft cap. Taking one means trading one away."));
    }
    out.push(bottom());
    out.join("\n")
}

// --------------------------------------------------------------------------- //
// badges
// --------------------------------------------------------------------------- //

pub fn badges(list: &Value) -> String {
    let mut out = vec![top(), row("BADGES · what made this run yours"), rule('─')];
    let empty = vec![];
    let items = list.as_array().unwrap_or(&empty);
    if items.is_empty() {
        out.push(row("None yet. Badges are earned from the save, never granted."));
    }
    for badge in items {
        out.push(row(&format!("★ {}", str_of(badge, "name"))));
        for line in wrap(str_of(badge, "blurb"), W - 8) {
            out.push(row(&format!("   {line}")));
        }
    }
    out.push(bottom());
    out.join("\n")
}

// --------------------------------------------------------------------------- //
// deck, shop, errors
// --------------------------------------------------------------------------- //

pub fn deck_view(state: &Value) -> String {
    let empty = vec![];
    let mut out = vec![top()];
    out.extend(chrome(state));
    out.push(rule('─'));

    let groups: [(&str, &str, &str); 4] = [
        ("cards", "CARDS · invoked, cost budget", "name"),
        ("relics", "RELICS · always on, cost nothing", "name"),
        ("powers", "POWERS · fire on an event", "name"),
        ("curses", "CURSES · cost you, unwanted", "name"),
    ];
    for (key, title, label) in groups {
        let items = state.get(key).and_then(Value::as_array).unwrap_or(&empty);
        out.push(row(&format!("{title}  ({})", items.len())));
        for item in items {
            let name = item
                .get(label)
                .and_then(Value::as_str)
                .unwrap_or_else(|| item.as_str().unwrap_or("?"));
            let detail = if item.get("rule").is_some() {
                str_of(item, "rule").to_string()
            } else if item.get("cost").is_some() {
                str_of(item, "cost").to_string()
            } else if item.get("event").is_some() {
                format!("on {}", str_of(item, "event"))
            } else {
                format!("{} plays", int_of(item, "plays"))
            };
            out.push(row(&format!("  · {}  {detail}", pad(name, 22))));
        }
        out.push(blank());
    }
    out.push(bottom());
    out.join("\n")
}

/// The name to print for one ware: `name`, then `title`, then its ref.
fn ware_label<'a>(detail: &'a Value, ware: &'a Value) -> &'a str {
    [str_of(detail, "name"), str_of(detail, "title"), str_of(ware, "ref")]
        .into_iter()
        .find(|candidate| !candidate.is_empty())
        .unwrap_or("")
}

pub fn shop(payload: &Value, state: &Value) -> String {
    let empty = vec![];
    let mut out = vec![top()];
    out.extend(chrome(state));
    out.push(rule('─'));
    out.push(row(&format!(
        "THE MERCHANT   you hold ◈{}",
        int_of(state, "focus")
    )));
    out.push(rule('─'));
    for ware in payload.get("wares").and_then(Value::as_array).unwrap_or(&empty) {
        let detail = ware.get("detail").cloned().unwrap_or(Value::Null);
        let mark = if ware.get("affordable") == Some(&Value::Bool(true)) {
            "▸"
        } else {
            "·"
        };
        out.push(row(&format!(
            "{mark} ◈{}  {}  ({})  [{}]",
            int_of(ware, "price"),
            // name, then title, then the ref — the same fallback the client
            // uses. `.max()` on two strings picks the lexicographically larger
            // one, so a ware carrying both fields could be labelled with
            // whichever sorted higher, and one carrying neither showed blank
            // instead of its ref.
            pad(ware_label(&detail, ware), 22),
            str_of(ware, "kind"),
            str_of(ware, "id")
        )));
    }
    out.push(rule('─'));
    out.push(row(&format!(
        "Card removal costs ◈{} and rises every time it is used.",
        int_of(state, "removal_cost")
    )));
    out.push(bottom());
    out.join("\n")
}

pub fn error(err: &Value) -> String {
    let mut out = vec![top(), row("SPIRE — refused"), rule('─')];
    for line in wrap(str_of(err, "message"), W - 6) {
        out.push(row(&format!("  {line}")));
    }
    let code = str_of(err, "code");
    if !code.is_empty() {
        out.push(rule('─'));
        out.push(row(&format!("  code: {code}")));
    }
    out.push(bottom());
    out.join("\n")
}

fn wrap(text: &str, cols: usize) -> Vec<String> {
    let mut lines = Vec::new();
    let mut current = String::new();
    for word in text.split_whitespace() {
        if !current.is_empty() && width(&current) + 1 + width(word) > cols {
            lines.push(std::mem::take(&mut current));
        }
        if !current.is_empty() {
            current.push(' ');
        }
        current.push_str(word);
    }
    if !current.is_empty() {
        lines.push(current);
    }
    if lines.is_empty() {
        lines.push(String::new());
    }
    lines
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn state() -> Value {
        json!({
            "act_label": "Act I", "floor": 3, "class_name": "The Defect",
            "deck_size": 6, "soft_cap": 12, "over_soft_cap": false,
            "focus": 2, "streak": 4, "ascension": 10,
            "active_room": null, "room": null,
            "cards": [], "relics": [], "powers": [], "curses": [],
            "removal_cost": 3
        })
    }

    /// The three rooms that offer choices instead of a fight.
    ///
    /// Each of these drew its name and then "INTENT: none shown", which in a
    /// host with no app is the whole screen — the player was told a campfire
    /// exists and never told what it does.
    #[test]
    fn a_campfire_names_what_it_offers() {
        let room = json!({
            "kind": "rest", "name": "Campfire", "room_type": "orient", "intents": [],
            "options": [
                {"id": "smith", "name": "Smith", "blurb": "Upgrade one card, permanently."},
                {"id": "prune", "name": "Prune", "blurb": "Remove one card from the deck."},
                {"id": "dig", "name": "Dig", "blurb": "Draw one relic.",
                 "requires_relic": "vendored-fork"},
            ],
        });
        let text = super::room(&room, &json!([]), &state());
        for named in ["Smith", "Prune", "Dig", "--option"] {
            assert!(text.contains(named), "campfire never mentions {named}:\n{text}");
        }
        // Dig is relic-gated and this save holds none, so it must not read as
        // available — same honesty rule as the map's `legal` flag.
        assert!(text.contains("needs the vendored-fork relic"), "{text}");
        assert!(!text.contains("INTENT"), "a campfire has no intent to show:\n{text}");
    }

    #[test]
    fn a_chest_says_what_is_in_it() {
        let room = json!({
            "kind": "treasure", "name": "Chest", "room_type": "orient", "intents": [],
            "offer": {"id": "t-singing-bowl", "ref": "singing-bowl", "kind": "relic",
                      "title": "Singing Bowl", "body": "Skipping pays ◈2 instead of ◈1."},
        });
        let text = super::room(&room, &json!([]), &state());
        assert!(text.contains("Singing Bowl"), "{text}");
        assert!(text.contains("Skipping pays"), "{text}");
    }

    #[test]
    fn a_shop_room_lists_its_stock() {
        let room = json!({
            "kind": "shop", "name": "The Merchant", "room_type": "orient", "intents": [],
            "wares": [{"id": "w-ftf", "ref": "c-ftf", "kind": "card", "price": 12,
                       "detail": {"title": "Failing Test First"}}],
        });
        let text = super::room(&room, &json!([]), &state());
        assert!(text.contains("Failing Test First"), "{text}");
        assert!(text.contains("◈12"), "{text}");
    }

    /// A fight still draws as a fight — the choice block must not swallow it.
    #[test]
    fn a_room_with_no_choices_still_shows_its_intent() {
        let room = json!({
            "kind": "monster", "name": "Flaky Suite", "room_type": "bug",
            "clear_at": 3, "progress": 0, "energy": 3, "energy_max": 3, "turn": 1,
            "intents": [{"kind": "attack", "tier": 4, "sensor": "tests_failing",
                         "text": "Will fail CI randomly."}],
        });
        let text = super::room(&room, &json!([]), &state());
        assert!(text.contains("INTENT"), "{text}");
        assert!(text.contains("PROGRESS"), "{text}");
    }

    #[test]
    fn every_node_kind_has_its_own_glyph() {
        let kinds = ["monster", "elite", "rest", "shop", "treasure", "boss", "event"];
        let mut seen = std::collections::HashSet::new();
        for kind in kinds {
            assert!(seen.insert(glyph(kind)), "{kind} reuses a glyph");
        }
        // The bug the demo shipped: a resolved event node rendered "undefined".
        assert_eq!(glyph("event"), "◇");
    }

    #[test]
    fn chrome_always_shows_the_live_deck_count() {
        let lines = chrome(&state()).join("\n");
        assert!(lines.contains("Deck 6/12"), "{lines}");
    }

    #[test]
    fn the_room_banner_appears_only_while_a_room_is_open() {
        assert!(!chrome(&state()).join("\n").contains("ROOM ACTIVE"));
        let mut s = state();
        s["room"] = json!({"name": "Flaky Suite", "room_type": "bug"});
        assert!(chrome(&s).join("\n").contains("ROOM ACTIVE: Flaky Suite"));
    }

    #[test]
    fn a_room_with_no_sensor_shows_no_intent_rather_than_a_guess() {
        let room = json!({"kind": "monster", "name": "Perf Cliff", "intents": []});
        let out = super::room(&room, &json!([]), &state());
        assert!(out.contains("INTENT: none shown."), "{out}");
        assert!(!out.contains("likely"), "{out}");
    }

    #[test]
    fn an_attack_intent_prints_its_exact_tier_and_its_sensor() {
        let room = json!({
            "kind": "elite", "name": "Flaky Suite",
            "intents": [{"kind": "attack", "tier": 4, "sensor": "tests_failing",
                         "text": "Will fail CI randomly."}]
        });
        let out = super::room(&room, &json!([]), &state());
        assert!(out.contains("⚔ 4"), "{out}");
        assert!(out.contains("[tests_failing]"), "{out}");
    }

    #[test]
    fn skip_owns_the_reward_hero_region() {
        let payload = json!({"skip_payout": 2, "offers": [
            {"id": "c-ftf", "title": "Failing Test First", "rarity": "common",
             "cost": 1, "body": "Pin it.", "rooms": ["bug"]}
        ]});
        let out = reward(&payload, &state());
        let skip = out.find("SKIP").expect("skip present");
        let offers = out.find("Offers").expect("offers present");
        assert!(skip < offers, "Skip must come first:\n{out}");
    }

    #[test]
    fn energy_pips_show_spent_and_remaining() {
        assert_eq!(pips(1, 3), "●○○");
        assert_eq!(pips(3, 3), "●●●");
    }

    #[test]
    fn the_progress_meter_is_bounded_at_both_ends() {
        assert!(meter(0, 3).starts_with('░'));
        assert_eq!(meter(9, 3).matches('█').count(), 20);
    }

    #[test]
    fn every_rendered_line_is_the_same_width() {
        let out = map(
            &json!({
                "act_label": "Act I", "rows": 1, "cols": 2,
                "boss": {"name": "Unclear Requirements", "intent": "Will block ship."},
                "nodes": [
                    {"id": "r0c0", "row": 0, "col": 0, "kind": "monster", "legal": true,
                     "cleared": false, "current": false, "resolved": null, "mark": null},
                    {"id": "r1c1", "row": 1, "col": 1, "kind": "boss", "legal": false,
                     "cleared": false, "current": false, "resolved": null, "mark": null}
                ]
            }),
            &state(),
        );
        for line in out.lines() {
            assert_eq!(line.chars().count(), W, "ragged line: {line:?}");
        }
    }
}
