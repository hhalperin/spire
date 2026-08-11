#!/usr/bin/env python3
"""The payloads the client renders — the whole run, the map, the merchant.

Every verb's reply carries `state`, and the map rides along with anything that
can move you. That redundancy is the contract: the client never derives a rule,
so a reply that changed something has to say what everything looks like now.

Nothing here writes to the save. `scene_table` is the one function that adds
rather than reports, and what it adds is dice — the client has no RNG, so the
engine pre-rolls the backgrounds and ships them on the payload whose lifetime
matches.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deck  # noqa: E402
import mapgen  # noqa: E402
from gamedata import card_by_id, content, object_by_id  # noqa: E402
from rewards import skip_payout  # noqa: E402
from rooms import build_hand  # noqa: E402
from runstate import SOFT_CAP, Run  # noqa: E402

ACT_ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV"}


def act_label(act: int) -> str:
    if act in ACT_ROMAN:
        return f"Act {ACT_ROMAN[act]}"
    return f"Act {act} · endless"


def scene_table(seed: int, act: int) -> dict[str, list[float]]:
    """The float vector the background composer walks, for every scene in an act.

    The client has no RNG — same contract as `mapgen.unknown_rolls`, and for the
    same reason. It rides on the *map* rather than on the state because that is
    the payload with the matching lifetime: scene rolls depend on the seed and
    the act and nothing else, and the map is exactly what the client refetches
    when the act changes. Putting it on the state would repeat kilobytes of dice
    on every card played; shipping only the current scene would leave the deck
    and badges screens undrawable, since the engine never sees them.

    Each scene gets exactly the length it needs — `mapgen.scene_budget` derives
    that from the grammar, so nothing here maintains a number. Because the vector
    is a prefix of one stream, growing a scene's grammar lengthens it without
    disturbing the floats already in it: the components already placed keep their
    dice.

    Rounded to three places. The composer's thresholds are coarse — how many
    pillars, which slot, which variant — so the fourth decimal cannot change a
    pixel, and dropping it is a third of the bytes.
    """
    names = [name for name in content("scenes")["scenes"] if not name.startswith("_")]
    return {
        name: [round(value, 3) for value in mapgen.scene_rolls(seed, act, name)]
        for name in names
    }


def serialize_map(run: Run) -> dict:
    smap = run.spire_map()
    current = run.current_node(smap)
    cleared = set(run.game.get("nodes_cleared") or [])
    legal = {n.id for n in mapgen.legal_moves(smap, current)}
    annotations = run.game.get("annotations") or {}

    nodes = []
    for node in sorted(smap.nodes.values(), key=lambda n: (n.row, n.col)):
        resolved = run.game.get("resolved", {}).get(node.id)
        nodes.append({
            "id": node.id,
            "row": node.row,
            "col": node.col,
            "kind": node.kind,
            "resolved": resolved,
            "next": sorted(node.next_cols),
            "cleared": node.id in cleared,
            "current": current is not None and node.id == current.id,
            # A pending reward blocks movement exactly as an open room does, so
            # both belong in the same answer. Offering nodes the engine will
            # refuse makes the map lie about what you can do next.
            "legal": (node.id in legal
                      and run.game.get("active_room") is None
                      and not run.game.get("pending_reward")),
            "mark": annotations.get(node.id),
        })
    return {
        "seed": run.seed,
        "act": run.act,
        "act_label": act_label(run.act),
        "rows": smap.rows,
        "cols": smap.cols,
        "boss": smap.boss,
        "fingerprint": smap.fingerprint(),
        "current": current.id if current else None,
        "nodes": nodes,
        "scene_rolls": scene_table(run.seed, run.act),
    }


def serialize_state(run: Run) -> dict:
    cards = run.deck.get("cards") or []
    room = run.game.get("room")
    # One read of the pool for the three answers that measure it. `hand_size`
    # rebuilds the list on every call, and this used to ask twice for the same
    # number and then a third time for the cards behind it.
    owned = run.owned_card_ids()
    deck_size = len(owned)
    return {
        "class": run.deck.get("class"),
        "classes": run.deck.get("classes") or [],
        "class_name": deck.CLASS_NAMES.get(run.deck.get("class", ""), "The Colorless"),
        "act": run.act,
        "act_label": act_label(run.act),
        "floor": int(run.deck.get("floor", 0)),
        "ascension": int(run.deck.get("ascension", 0)),
        "streak": int(run.deck.get("clean_room_streak", 0)),
        "rewards": run.deck.get("rewards") or {},
        # The playable pool, not the dealt-skill ledger — see `Run.hand_size`.
        # `cards` below stays the ledger, because the deck screen files it
        # separately as "the config this run wrote".
        "deck_size": deck_size,
        "soft_cap": SOFT_CAP,
        # ">=", not ">": the gate refuses at exactly SOFT_CAP, so a flag meaning
        # "the cap binds now" has to agree with it. At 12/12 the engine refused
        # the take while the client stayed silent, because the two used
        # different comparisons for the same question.
        "over_soft_cap": deck_size >= SOFT_CAP,
        "cards": cards,
        "relics": [dict(object_by_id("relics", r) or {"id": r, "name": r, "rule": ""})
                   for r in (run.deck.get("relics") or [])],
        # Two different things were both called "powers": `deck["powers"]` is
        # the ascension hook ledger from deck.py, and `game["powers"]` is the
        # power cards running this act. The client read the first and never saw
        # the second, so a card whose effect `clear_at_for` was applying did not
        # appear anywhere on screen.
        "powers": ((run.deck.get("powers") or [])
                   + [{"name": (card_by_id(p) or {}).get("title", p), "event": "this act"}
                      for p in (run.game.get("powers") or [])]),
        "potions": [object_by_id("potions", p) for p in (run.game.get("potions") or [])],
        "curses": [object_by_id("curses", c) for c in (run.game.get("curses") or [])],
        "focus": int(run.game.get("focus", 0)),
        "skip_payout": skip_payout(run),
        "removal_cost": (content("shop")["removal"]["base"]
                         + content("shop")["removal"]["step"] * int(run.game.get("removals", 0))),
        "active_room": run.game.get("active_room"),
        "room": room,
        "pending_reward": run.game.get("pending_reward"),
        "badges": run.game.get("badges") or [],
        "hand": build_hand(run, room) if room and "energy" in room else [],
        # The whole owned pool, unfiltered by room legality. The campfire needs
        # it to offer a card to smith or prune, and the deck facet needs it to
        # show what the run is actually carrying.
        "pool": [
            {
                "id": card["id"], "title": card["title"], "cost": card["cost"],
                "type": card["type"], "rarity": card["rarity"], "body": card["body"],
                "rooms": card["rooms"],
                "upgraded": card["id"] in (run.game.get("upgraded") or []),
            }
            for card in (card_by_id(cid) for cid in owned)
            if card
        ],
    }


def shelf(run: Run) -> list[dict]:
    """The merchant's stock, priced against the focus you hold right now."""
    wares = []
    for ware in content("shop")["wares"]:
        entry = dict(ware)
        ref = card_by_id(ware["ref"]) or object_by_id(
            "relics" if ware["kind"] == "relic" else "potions", ware["ref"])
        entry["detail"] = ref or {}
        entry["affordable"] = int(run.game.get("focus", 0)) >= ware["price"]
        wares.append(entry)
    return wares
