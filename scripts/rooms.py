#!/usr/bin/env python3
"""Building a room from a node, and the hand you face it with.

`build_room` is a pure function of the save and the seed: the same node on the
same climb produces the same enemy, the same event, the same chest, on any
machine. Nothing here writes — the caller decides whether the room it was handed
becomes the active one.

`build_hand` sits alongside it because a hand only means anything against a
room. Legality, affordability and "already played" are all questions about this
room right now, and answering them in one place is what lets the client render a
card's greyed-out reason without knowing any rules.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mapgen  # noqa: E402
from gamedata import card_by_id, content, object_by_id  # noqa: E402
from rewards import draw_from_treasure  # noqa: E402
from runstate import Run  # noqa: E402

# --------------------------------------------------------------------------- #
# intents — the disclosure surface
# --------------------------------------------------------------------------- #

def sensor_backed(intents: list[dict]) -> list[dict]:
    """Drop every intent with no deterministic sensor behind it.

    This is the whole reason the field exists. sts-fidelity.md records Mega
    Crit's own playtest: bracketed, partial information tested *worse* than
    either full information or none, because players could not tell whether a
    range meant randomness or ignorance. So an intent we cannot stand behind is
    not softened into a guess — it is removed, and the client shows nothing.
    """
    kept = []
    for intent in intents or []:
        if intent.get("sensor"):
            kept.append(intent)
        elif intent.get("kind") == "unknown":
            # An explicit "nothing measures this yet" is honest disclosure and
            # is exactly what StS's own Unknown intent communicates.
            kept.append({"kind": "unknown", "sensor": None, "text": intent.get("text", "")})
    return kept


# --------------------------------------------------------------------------- #
# room instances
# --------------------------------------------------------------------------- #

def pick_enemy(run: Run, node: mapgen.Node, kind: str) -> dict:
    """Choose this node's enemy deterministically from the per-floor RNG."""
    bucket = "elite" if kind == "elite" else "monster"
    pool = content("enemies")[bucket]
    rng = mapgen.floor_rng(mapgen.act_seed(run.seed, run.act), node.row)
    return dict(pool[rng.randrange(len(pool))])


def pick_event(run: Run, node: mapgen.Node) -> dict:
    events = content("events")["events"]
    rng = mapgen.floor_rng(mapgen.act_seed(run.seed, run.act) + 1, node.row)
    return dict(events[rng.randrange(len(events))])


def boss_room(run: Run, smap: mapgen.SpireMap) -> dict:
    boss = dict(smap.boss)
    return {
        "id": boss.get("id", "boss"),
        "name": boss.get("name", "The Boss"),
        "room": boss.get("room", "design"),
        "clear_at": 5,
        "telegraph": "known since floor one",
        "blurb": "The act's final exam. It was visible from the moment you entered.",
        "acceptance": {"type": "manual_confirm", "expect": boss.get("acceptance", "")},
        "intents": [{"kind": "attack", "tier": 5, "sensor": "tests_failing",
                     "text": boss.get("intent", "")}],
        "turn_effect": "The deadline moves one day closer.",
    }


def build_room(run: Run, smap: mapgen.SpireMap, node: mapgen.Node) -> dict:
    """Create the room instance for a node. Pure function of save + seed."""
    kind = run.resolved_kind(smap, node)

    if kind == "boss":
        enemy = boss_room(run, smap)
    elif kind in ("monster", "elite"):
        enemy = pick_enemy(run, node, kind)
    else:
        enemy = None

    room: dict = {
        "id": node.id,
        "node": node.id,
        "kind": kind,
        "floor": node.row + 1,
        "log": [],
    }

    if enemy is not None:
        room_type = enemy.get("room", "bug")
        room.update({
            "name": enemy["name"],
            "room_type": room_type,
            "telegraph": enemy.get("telegraph", ""),
            "blurb": enemy.get("blurb", ""),
            "acceptance": enemy.get("acceptance", {}),
            "intents": sensor_backed(enemy.get("intents", [])),
            "turn_effect": enemy.get("turn_effect", ""),
            "clear_at": run.clear_at_for(room_type, int(enemy.get("clear_at", 3))),
            "progress": 0,
            "energy_max": run.energy_for(room_type),
            "energy": run.energy_for(room_type),
            "turn": 1,
            "played": [],
        })
    elif kind == "event":
        event = pick_event(run, node)
        room.update({"name": event["title"], "room_type": "design", "event": event,
                     "intents": []})
    elif kind == "shop":
        room.update({"name": "The Merchant", "room_type": "orient",
                     "wares": [ware_detail(w) for w in content("shop")["wares"]],
                     "intents": []})
    elif kind == "rest":
        room.update({"name": "Campfire", "room_type": "orient",
                     "options": content("shop")["campfire"]["options"], "intents": []})
    elif kind == "treasure":
        room.update({"name": "Chest", "room_type": "orient",
                     "offer": roll_treasure(run, node), "intents": []})
    return room


def ware_detail(ware: dict) -> dict:
    """A shop ware with the thing it refers to attached.

    `content/shop.json` stores a ware as id, ref, kind and price — enough to sell
    it, not enough to name it. The merchant *screen* resolved the ref itself, so
    entering a shop node handed the room raw wares and the drawn shelf listed
    `c-char` where a title belongs. One resolver, used by both, so the room and
    the shelf cannot disagree about what a ware is called.
    """
    entry = dict(ware)
    bucket = "relics" if ware["kind"] == "relic" else "potions"
    entry["detail"] = card_by_id(ware["ref"]) or object_by_id(bucket, ware["ref"]) or {}
    return entry


def roll_treasure(run: Run, node: mapgen.Node) -> dict:
    rng = mapgen.floor_rng(mapgen.act_seed(run.seed, run.act) + 2, node.row)
    # Potions are never excluded, so a chest always has something to offer even
    # for a player holding every relic.
    return draw_from_treasure(rng, owned=set(run.deck.get("relics") or [])) or {}


# --------------------------------------------------------------------------- #
# hand
# --------------------------------------------------------------------------- #

def legal_for(card: dict, room_type: str) -> bool:
    rooms = card.get("rooms") or []
    return not rooms or room_type in rooms


def build_hand(run: Run, room: dict) -> list[dict]:
    """Every owned card, annotated with why it can or cannot be played.

    Illegal cards are returned rather than hidden. StS shows you the whole hand
    including the cards you cannot afford — knowing what you are missing is part
    of solving the turn.
    """
    room_type = room.get("room_type", "orient")
    energy = int(room.get("energy", 0))
    played = room.get("played") or []
    hand = []
    for card_id in run.owned_card_ids():
        card = card_by_id(card_id)
        if card is None:
            continue
        legal = legal_for(card, room_type)
        affordable = card["cost"] <= energy
        spent = card["id"] in played
        hand.append({
            "id": card["id"],
            "title": card["title"],
            "cost": card["cost"],
            "type": card["type"],
            "body": card["body"],
            "rooms": card["rooms"],
            "rarity": card["rarity"],
            "progress": card["progress"],
            "agent_skill": card.get("agent_skill"),
            "legal": legal,
            "affordable": affordable,
            # A card is spent for the rest of the turn once played. This field
            # existed from the start and nothing consumed it, which is how a
            # cost-0 card came to clear any room on turn one.
            "playable": legal and affordable and not spent,
            "played_this_turn": spent,
            "reason": (None if legal and affordable and not spent
                       else "not legal in this room" if not legal
                       else "already played this turn" if spent
                       else "not enough energy"),
        })
    hand.sort(key=lambda c: (not c["playable"], c["cost"], c["title"]))
    return hand
