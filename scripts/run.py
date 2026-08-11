#!/usr/bin/env python3
"""spire's headless run loop — the game the MCP client renders.

The engine already owned detection (`scan.py`), the save (`deck.py`) and the
map (`mapgen.py`). What it never owned was the *run*: entering a room, spending
energy, playing a card, checking acceptance, clearing a floor. That logic lived
inline in `design/spire-ai/ui/demo/demo.js`, which made the browser mock the only
implementation and left the real save untouched. This module is that logic,
moved where it belongs.

Everything here is stdlib and every subcommand speaks JSON on stdout, because
the Rust MCP server shells out to it and the shape of the reply is the contract.
Failures come back as `{"ok": false, "error": {...}}` with exit 0 — the client
must never brick on a bad verb (mcp-client.md: "All tools fail-open with
structured errors").

What this deliberately does NOT model, per design/spire-ai/sts-fidelity.md:
player HP, block, damage math, buffs and debuffs. Rooms carry `clear_at`, a
progress target that resets with the room. Nothing here is a health bar, and an
intent is only ever emitted when a deterministic sensor stands behind it.

This file is the *verbs*. The rules they call live in flat siblings, in the
order one room's worth of play visits them:

    gamedata.py    content/*.json, loaded once and looked up by id
    runstate.py    RunError and Run — the save, the map, the deck readings
    rooms.py       building a room from a node, and the hand you face it with
    acceptance.py  the deterministic sensor a room clears against
    events.py      event choices: which are available, what taking one does
    rewards.py     offers, chest draws, the skip payout, removal, badges
    serialize.py   the payloads the client renders

Every name those modules define is re-exported here, so `run.<anything>` still
resolves and the split stays a move rather than a new interface.

Subcommands:
    state       the whole run, as the client's initial payload
    map         nodes, legal moves, resolved unknowns, annotations
    enter       open a room (refuses while another is active)
    hand        the legal cards for the active room
    play        spend energy, advance progress
    end-turn    refill energy, apply the room's turn effect
    acceptance  run the room's deterministic check
    clear       finish the room, advance the floor, roll a reward
    flee        abandon the room, lose the streak
    reward      take or skip an offer
    campfire    smith / prune / dig
    shop        list or buy
    annotate    mark a map node (the Slay the Spire 2 draw tool, ported)
    badges      evaluate end-of-act badges
    new-run     start or restart a climb
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deck  # noqa: E402
import mapgen  # noqa: E402
from acceptance import resolve_command, run_acceptance  # noqa: E402
from events import EFFECTS, apply_effects, choice_is_available  # noqa: E402
from gamedata import CONTENT_DIR, card_by_id, content, object_by_id  # noqa: E402
from rewards import (  # noqa: E402
    SKIP_PAYOUT,
    SKIP_PAYOUT_WITH_BOWL,
    draw_from_treasure,
    evaluate_badges,
    remove_from_deck,
    roll_offers,
    skip_payout,
)
from rooms import (  # noqa: E402
    boss_room,
    build_hand,
    build_room,
    legal_for,
    pick_enemy,
    pick_event,
    roll_treasure,
    sensor_backed,
)
from runstate import (  # noqa: E402
    DEFAULT_ENERGY,
    DEFAULT_HAND,
    SOFT_CAP,
    Run,
    RunError,
)
from serialize import (  # noqa: E402
    ACT_ROMAN,
    act_label,
    scene_table,
    serialize_map,
    serialize_state,
    shelf,
)

# The run loop's whole surface, re-exported. Tests, and anything else that used
# to reach into this module before the split, still find every name here.
__all__ = [
    "ACT_ROMAN",
    "CONTENT_DIR",
    "DEFAULT_ENERGY",
    "DEFAULT_HAND",
    "EFFECTS",
    "HANDLERS",
    "SKIP_PAYOUT",
    "SKIP_PAYOUT_WITH_BOWL",
    "SOFT_CAP",
    "VERBS",
    "Run",
    "RunError",
    "act_label",
    "apply_effects",
    "boss_room",
    "build_hand",
    "build_parser",
    "build_room",
    "card_by_id",
    "choice_is_available",
    "content",
    "dispatch",
    "draw_from_treasure",
    "evaluate_badges",
    "legal_for",
    "main",
    "object_by_id",
    "pick_enemy",
    "pick_event",
    "remove_from_deck",
    "resolve_command",
    "roll_offers",
    "roll_treasure",
    "run_acceptance",
    "scene_table",
    "sensor_backed",
    "serialize_map",
    "serialize_state",
    "shelf",
    "skip_payout",
]


# --------------------------------------------------------------------------- #
# subcommands
# --------------------------------------------------------------------------- #

def require_room(run: Run) -> dict:
    room = run.game.get("room")
    if not room:
        raise RunError("no_active_room", "No room is open. Pick a node on the map first.")
    return room


def cmd_state(run: Run, args: argparse.Namespace) -> dict:
    return {"ok": True, "state": serialize_state(run)}


def cmd_map(run: Run, args: argparse.Namespace) -> dict:
    return {"ok": True, "map": serialize_map(run), "state": serialize_state(run)}


def cmd_enter(run: Run, args: argparse.Namespace) -> dict:
    # room-prior-contract.md: entering sets active_room, and nothing else may
    # start until it clears or you flee. One room at a time is the product.
    if run.game.get("active_room"):
        raise RunError(
            "room_active",
            # `room` can be null while `active_room` is set on a half-written
            # save; a refusal that raises AttributeError is reported as
            # `internal`, which is the one shape the client must never see.
            f"Room active: {(run.game.get('room') or {}).get('name', 'unknown')}"
            " — finish or flee first.",
            active_room=run.game["active_room"],
        )
    # A reward you have not answered is still the room's business. Clearing a
    # fight leaves `pending_reward` set with no active room, so this gate was
    # the only thing between "walk on" and the next clear overwriting the offer
    # — the card was gone, and no message said so. Take it or skip it; skipping
    # pays focus, so there is never a reason to leave one hanging.
    if run.game.get("pending_reward"):
        raise RunError(
            "reward_pending",
            "There is a reward waiting. Take one or skip before moving on.",
        )
    smap = run.spire_map()
    node = run._node_by_id(smap, args.node)
    if node is None:
        raise RunError("no_such_node", f"No node {args.node!r} on this map.")
    current = run.current_node(smap)
    if not mapgen.is_legal_move(smap, current, node):
        raise RunError("illegal_move", f"{args.node} is not reachable from here.")

    room = build_room(run, smap, node)
    run.game["room"] = room
    run.game["active_room"] = node.id
    run.save()
    return {"ok": True, "room": room, "state": serialize_state(run)}


def cmd_hand(run: Run, args: argparse.Namespace) -> dict:
    room = require_room(run)
    return {"ok": True, "hand": build_hand(run, room), "room": room,
            "state": serialize_state(run)}


def cmd_play(run: Run, args: argparse.Namespace) -> dict:
    room = require_room(run)
    if "energy" not in room:
        raise RunError("not_a_fight", f"{room.get('name')} is not a room you play cards in.")

    card = card_by_id(args.card)
    if card is None:
        raise RunError("no_such_card", f"No card {args.card!r} in the pool.")
    if args.card not in run.owned_card_ids():
        raise RunError("not_owned", f"{card['title']} is not in this deck.")
    if not legal_for(card, room.get("room_type", "orient")):
        raise RunError("illegal_card",
                       f"{card['title']} is not legal in a {room['room_type']} room.")
    # One play per card per turn. Without this a cost-0 card clears any room on
    # turn one — `0 > energy` is never true — and the whole "spend a limited
    # hand well" problem the game is built on stops existing. The browser demo
    # this was ported from disabled the button; the port kept the `played` list
    # and forgot to read it.
    if card["id"] in (room.get("played") or []):
        raise RunError("already_played",
                       f"{card['title']} has already been played this turn. "
                       "End the turn to draw it again.")

    cost = card["cost"]
    for effect in run.curse_effects():
        if effect.get("verb") == "first_card_tax" and not room.get("played"):
            cost += int(effect.get("amount", 0))
    if cost > room["energy"]:
        raise RunError("no_energy", f"{card['title']} costs {cost}; {room['energy']} energy left.")

    room["energy"] -= cost
    room["progress"] = min(room["clear_at"], room["progress"] + card["progress"])
    room.setdefault("played", []).append(card["id"])
    room.setdefault("log", []).append(f"Played {card['title']} (+{card['progress']}).")

    # A power stays on for the act rather than resolving into progress. Recorded
    # here so `clear_at_for` can read it in every room that follows; cleared with
    # the rest of the act state when the boss falls.
    if card.get("type") == "power" and card.get("effect"):
        powers = run.game.setdefault("powers", [])
        if card["id"] not in powers:
            powers.append(card["id"])
            room["log"].append(f"{card['title']} is running for the rest of the act.")

    # The reality bridge: a card that names a dealt skill credits a play on it,
    # so deck.json's `plays` reflects the game, not just direct invocations.
    if card.get("agent_skill"):
        deck.record_play(run.repo, card["agent_skill"])
        run.reload()

    run.save()
    return {"ok": True, "room": room, "hand": build_hand(run, room),
            "cleared_condition_met": room["progress"] >= room["clear_at"],
            "state": serialize_state(run)}


def cmd_end_turn(run: Run, args: argparse.Namespace) -> dict:
    room = require_room(run)
    if "energy" not in room:
        raise RunError("not_a_fight", f"{room.get('name')} has no turns.")
    room["turn"] = int(room.get("turn", 1)) + 1
    # Energy is SET, not added — unspent energy expires. That is what makes the
    # turn a closed problem instead of a savings account.
    room["energy"] = room["energy_max"]
    room["played"] = []
    if room.get("turn_effect"):
        room.setdefault("log", []).append(room["turn_effect"])
    run.save()
    return {"ok": True, "room": room, "hand": build_hand(run, room),
            "state": serialize_state(run)}


def cmd_acceptance(run: Run, args: argparse.Namespace) -> dict:
    room = require_room(run)
    verdict = run_acceptance(run, room)
    room.setdefault("log", []).append(f"Acceptance: {verdict['result']}.")
    room["acceptance_result"] = verdict
    run.save()
    return {"ok": True, "acceptance": verdict, "room": room,
            "state": serialize_state(run)}


def cmd_clear(run: Run, args: argparse.Namespace) -> dict:
    room = require_room(run)
    node_id = room["id"]
    resolution: list[str] = []

    # An event is cleared by *choosing*, so the choice has to arrive with the
    # clear or its consequences never happen.
    if room.get("kind") == "event":
        event = room.get("event") or {}
        choices = event.get("choices") or []
        if not args.choice:
            raise RunError(
                "choice_required",
                f"{event.get('title', 'This event')} needs a choice: "
                + ", ".join(c["id"] for c in choices),
                choices=[{"id": c["id"], "label": c["label"]} for c in choices],
            )
        choice = next((c for c in choices if c["id"] == args.choice), None)
        if choice is None:
            raise RunError("no_such_choice", f"{args.choice!r} is not a choice here.")
        blocked = choice_is_available(run, choice)
        if blocked:
            raise RunError("choice_locked", f"{choice['label']} {blocked}.")
        resolution = apply_effects(run, choice.get("effects") or [])

    if "clear_at" in room and room.get("progress", 0) < room["clear_at"] and not args.force:
        raise RunError(
            "not_cleared",
            f"{room['name']} is at {room['progress']}/{room['clear_at']}. Play more cards.",
            progress=room.get("progress", 0), clear_at=room["clear_at"],
        )

    run.game.setdefault("nodes_cleared", []).append(node_id)
    if room.get("kind") == "elite":
        run.game.setdefault("elites_cleared", []).append(node_id)
    run.game["active_room"] = None
    run.game["room"] = None

    deck.clear_room(run.repo, room_id=f"floor-{room.get('floor', 0)}")
    run.reload()

    reward = None
    if room.get("kind") in ("monster", "elite", "boss"):
        kind = {"boss": "boss", "elite": "elite"}.get(room["kind"], "normal")
        offers = roll_offers(run, kind, room.get("floor", 0))
        if offers:
            reward = {"kind": "card", "offers": offers, "skip_payout": skip_payout(run)}
            deck.bump_reward(run.repo, "offered", len(offers))
            run.reload()
    elif room.get("kind") == "treasure":
        reward = {"kind": "treasure", "offers": [room.get("offer") or {}], "skip_payout": 0}

    run.game["pending_reward"] = reward

    finished_act = room.get("kind") == "boss"
    if finished_act:
        run.game["badges"] = evaluate_badges(run)
        run.deck["act"] = run.act + 1
        run.game["nodes_cleared"] = []
        run.game["resolved"] = {}
        run.game["ramp"] = dict.fromkeys(mapgen.RAMP_BASE, 0)
        # Powers are act-scoped, which is what the card text promises.
        run.game["powers"] = []

    run.save()
    return {"ok": True, "reward": reward, "act_cleared": finished_act,
            "resolution": resolution,
            "badges": run.game.get("badges") if finished_act else [],
            # The map ships with every verb that can move you. Returning state
            # alone left the client rendering stale reachability from before the
            # room was cleared.
            "map": serialize_map(run), "state": serialize_state(run)}


def cmd_flee(run: Run, args: argparse.Namespace) -> dict:
    room = require_room(run)
    run.game["active_room"] = None
    run.game["room"] = None
    run.deck["clean_room_streak"] = 0
    if args.no_notes and "hesitation" not in (run.game.get("curses") or []):
        run.game.setdefault("curses", []).append("hesitation")
    run.save()
    return {"ok": True, "fled": room.get("name"),
            "curse_gained": "hesitation" if args.no_notes else None,
            "map": serialize_map(run), "state": serialize_state(run)}


def cmd_reward(run: Run, args: argparse.Namespace) -> dict:
    pending = run.game.get("pending_reward")
    if not pending:
        raise RunError("no_reward", "No reward is pending.")

    if args.skip:
        payout = int(pending.get("skip_payout", 0))
        run.game["focus"] = int(run.game.get("focus", 0)) + payout
        run.game["pending_reward"] = None
        deck.bump_reward(run.repo, "skipped", 1)
        run.reload()
        run.save()
        return {"ok": True, "skipped": True, "focus_gained": payout,
                "map": serialize_map(run), "state": serialize_state(run)}

    if not args.take:
        raise RunError("bad_args", "Pass --take <id> or --skip.")

    offer = next((o for o in pending.get("offers", []) if o.get("id") == args.take), None)
    if offer is None:
        raise RunError("no_such_offer", f"{args.take!r} was not offered.")

    # The cap counts cards, so it may only gate cards. A chest hands out relics
    # and potions, which never enter `hand_pool` — refusing those at twelve
    # cards demanded a trade that would not have made room for anything, and
    # made the deck limit look like a penalty rather than a constraint.
    takes_a_card = pending.get("kind") != "treasure"
    if takes_a_card and run.hand_size() >= SOFT_CAP and not args.trade:
        raise RunError(
            "at_soft_cap",
            f"Deck is at the soft cap ({SOFT_CAP}). Name a card to trade away with --trade.",
            soft_cap=SOFT_CAP,
        )
    if args.trade:
        # A trade that removes nothing is a free pass past the gate above, so
        # the result is checked — and it goes through `remove_from_deck`, which
        # reaches the pool as well as the ledger. Trading only out of the ledger
        # made the cap impossible to satisfy rather than merely leaky.
        if not remove_from_deck(run, args.trade):
            raise RunError("no_such_card",
                           f"{args.trade!r} is not in this deck, so there is nothing to trade.")

    if pending.get("kind") == "treasure":
        kind = offer.get("kind", "relic")
        if kind == "relic":
            # Unique, like every other path that grants one. `draw_from_treasure`
            # already excludes held relics, so this is belt to that brace — but
            # the offer rides in the save between the clear and the take, and a
            # relic gained in between (an event, a purchase) would otherwise
            # stack here.
            if run.has_relic(offer["ref"]):
                raise RunError("already_owned",
                               f"You already carry {offer.get('title', offer['ref'])}.")
            run.deck.setdefault("relics", []).append(offer["ref"])
        else:
            run.game.setdefault("potions", []).append(offer["ref"])
    else:
        pool = run.game.setdefault("hand_pool", run.owned_card_ids())
        if offer["id"] not in pool:
            pool.append(offer["id"])

    run.game["pending_reward"] = None
    deck.bump_reward(run.repo, "taken", 1)
    run.reload()
    run.save()
    return {"ok": True, "taken": offer, "map": serialize_map(run),
            "state": serialize_state(run)}


def cmd_campfire(run: Run, args: argparse.Namespace) -> dict:
    room = require_room(run)
    if room.get("kind") != "rest":
        raise RunError("not_a_campfire", f"{room.get('name')} is not a campfire.")

    if args.option == "prune":
        if not args.card:
            raise RunError("bad_args", "Pass --card <name> to prune.")
        # Refused rather than reported as success when nothing was removed: a
        # typo used to burn this rest site, bump `removals` — which raises the
        # removal price for the rest of the run — and advance the floor, with
        # the deck exactly as it was. A campfire is non-renewable.
        if not remove_from_deck(run, args.card):
            raise RunError("no_such_card",
                           f"{args.card!r} is not in this deck, so there is nothing to prune.")
        run.game["removals"] = int(run.game.get("removals", 0)) + 1
        detail = f"Pruned {args.card}."
    elif args.option == "smith":
        if not args.card:
            raise RunError("bad_args", "Pass --card <id> to smith.")
        # Same refusal as prune and trade: a typo must not burn the rest site.
        # Smith was the one campfire option that accepted any string at all,
        # recording an upgrade for a card nobody owns.
        if args.card not in run.owned_card_ids():
            raise RunError("no_such_card",
                           f"{args.card!r} is not in this deck, so there is nothing to smith.")
        upgraded = run.game.setdefault("upgraded", [])
        if args.card in upgraded:
            raise RunError("already_upgraded", f"{args.card} is already upgraded.")
        upgraded.append(args.card)
        detail = f"Smithed {args.card}."
    elif args.option == "dig":
        if not run.has_relic("vendored-fork"):
            raise RunError("locked", "Dig needs the Vendored Fork relic.")
        # This used to be the line above plus the string below, and nothing
        # else: the player spent a non-renewable rest site and was told they
        # had dug up a relic that was never added to anything.
        option = next(o for o in content("shop")["campfire"]["options"] if o["id"] == "dig")
        draws = option.get("draws") or {}
        rng = mapgen.floor_rng(mapgen.act_seed(run.seed, run.act) + 4,
                               int(run.game.get("removals", 0)) + int(room.get("floor", 0)))
        found = draw_from_treasure(rng, draws.get("kind"),
                                   owned=set(run.deck.get("relics") or []))
        if found is None:
            raise RunError("nothing_to_dig",
                           "You already hold every relic in the chest pool. "
                           "Spend the campfire on something that changes the run.")
        if found["kind"] == "relic":
            run.deck.setdefault("relics", []).append(found["ref"])
        else:
            run.game.setdefault("potions", []).append(found["ref"])
        detail = f"Dug up {found['ref']}."
    else:
        raise RunError("bad_args", f"Unknown campfire option {args.option!r}.")

    run.game["active_room"] = None
    run.game["room"] = None
    run.game.setdefault("nodes_cleared", []).append(room["id"])
    deck.clear_room(run.repo, room_id=f"floor-{room.get('floor', 0)}")
    run.reload()
    run.save()
    return {"ok": True, "detail": detail, "map": serialize_map(run),
            "state": serialize_state(run)}


def cmd_shop(run: Run, args: argparse.Namespace) -> dict:
    room = require_room(run)
    if room.get("kind") != "shop":
        raise RunError("not_a_shop", f"{room.get('name')} is not the merchant.")

    wares = shelf(run)

    if not args.buy:
        # `focus` used to sit at the payload root here, which is not where the
        # terminal renderer looks — it reads focus and the removal price off
        # `state`, so a browsing player was told they held ◈0 and that removal
        # was free. Every reply carries state; nothing reads a second copy.
        return {"ok": True, "wares": wares, "state": serialize_state(run)}

    chosen = next((w for w in wares if w["id"] == args.buy), None)
    if chosen is None:
        raise RunError("no_such_ware", f"{args.buy!r} is not for sale.")
    if not chosen["affordable"]:
        raise RunError("too_expensive",
                       f"{chosen['detail'].get('name', args.buy)} costs {chosen['price']} focus; "
                       f"you have {run.game.get('focus', 0)}.")

    # The merchant obeys the same cap the reward screen does. Without this the
    # shop was a way to buy past the deck limit that rewards enforce, which is
    # the tension the removal ladder exists to price.
    if chosen["kind"] == "card" and run.hand_size() >= SOFT_CAP:
        raise RunError(
            "at_soft_cap",
            f"Deck is at the soft cap ({SOFT_CAP}). Remove a card before buying another.",
            soft_cap=SOFT_CAP,
        )
    # Relics are unique everywhere else — events refuse a duplicate, chest draws
    # exclude what you hold, Dig refuses rather than hand one back. The shop was
    # the only path that would sell you a second copy of a relic you own.
    if chosen["kind"] == "relic" and run.has_relic(chosen["ref"]):
        raise RunError("already_owned",
                       f"You already carry {chosen['detail'].get('name', chosen['ref'])}.")

    run.game["focus"] = int(run.game.get("focus", 0)) - chosen["price"]
    if chosen["kind"] == "card":
        pool = run.game.setdefault("hand_pool", run.owned_card_ids())
        if chosen["ref"] not in pool:
            pool.append(chosen["ref"])
    elif chosen["kind"] == "relic":
        run.deck.setdefault("relics", []).append(chosen["ref"])
    else:
        run.game.setdefault("potions", []).append(chosen["ref"])
    run.save()
    # The buy reply carries the shelf, repriced against the focus that is left.
    # Without it the terminal lost the merchant the moment you bought something
    # — `render::shop` only runs when `wares` is present — and the client had to
    # fire a second `spire_shop_list` to redraw, which is a round trip for data
    # this reply already knows.
    return {"ok": True, "bought": chosen, "wares": shelf(run),
            "state": serialize_state(run)}


def cmd_annotate(run: Run, args: argparse.Namespace) -> dict:
    """The Slay the Spire 2 map draw tool, ported.

    StS2 shipped annotation because routing is the decision the map exists to
    support. Marking a node before committing to an edge is the same act here.
    """
    annotations = run.game.setdefault("annotations", {})
    if args.mark in (None, "", "clear"):
        annotations.pop(args.node, None)
    else:
        annotations[args.node] = args.mark
    run.save()
    # The map ships with the reply because marking a node *is* a map action, and
    # `serialize_map` already carries each node's mark. Without it the terminal
    # had nothing map-shaped to draw and fell back to the deck view, so a player
    # on Claude Code placed a mark and was shown their card list.
    return {"ok": True, "annotations": annotations,
            "map": serialize_map(run), "state": serialize_state(run)}


def cmd_badges(run: Run, args: argparse.Namespace) -> dict:
    return {"ok": True, "badges": evaluate_badges(run), "state": serialize_state(run)}


def cmd_new_run(run: Run, args: argparse.Namespace) -> dict:
    run.deck["game"] = deck.game_skeleton(seed=args.seed)
    run.deck["act"] = 1
    run.deck["floor"] = 0
    run.deck["clean_room_streak"] = 0
    # Relics are run rewards and live outside the game block, so resetting the
    # block alone carried the last climb's relics into the new one — a "fresh"
    # run that starts holding the Singing Bowl is not the same game. The game
    # block already resets curses, potions and the hand pool; this completes it.
    run.deck["relics"] = []
    # Reward counters are run-scoped too, and badges read them: a fresh climb
    # inheriting last run's skips would earn Refusenik on floor one.
    run.deck["rewards"] = {"offered": 0, "taken": 0, "skipped": 0}
    # The room log is per-climb too: without this `deck.py show` reported a long
    # history against floor 0 on a run that had not started.
    run.deck["rooms_cleared"] = []
    run.game = run.deck["game"]
    run.save()
    return {"ok": True, "state": serialize_state(run), "map": serialize_map(run)}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

# VERBS is the single declaration of the CLI: one row per subcommand, holding
# its handler, its help line and its flags. The parser and the dispatch table
# are both derived from it.
#
# They used to be two hand-maintained lists of the same fifteen verbs, thirty
# lines apart — the shape CONTRIBUTING.md warns about, and the one that lets a
# verb exist in `HANDLERS` with no way to reach it from the CLI. Deriving both
# from one row makes that unrepresentable rather than merely tested for.
VERBS: dict[str, tuple[object, str, list[tuple[str, dict]]]] = {
    "state": (cmd_state, "the whole run", []),
    "map": (cmd_map, "nodes, legal moves, annotations", []),
    "enter": (cmd_enter, "open a room", [
        ("--node", {"required": True}),
    ]),
    "hand": (cmd_hand, "legal cards for the active room", []),
    "play": (cmd_play, "play a card", [
        ("--card", {"required": True}),
    ]),
    "end-turn": (cmd_end_turn, "refill energy, apply the turn effect", []),
    "acceptance": (cmd_acceptance, "run the room's deterministic check", []),
    "clear": (cmd_clear, "finish the room", [
        ("--force", {"action": "store_true", "help": "clear without meeting the target"}),
        ("--choice", {"help": "event choice id (required in an event room)"}),
    ]),
    "flee": (cmd_flee, "abandon the room", [
        ("--no-notes", {"action": "store_true", "help": "flee without notes; gain Hesitation"}),
    ]),
    "reward": (cmd_reward, "resolve a pending offer", [
        ("--take", {}),
        ("--skip", {"action": "store_true"}),
        ("--trade", {"help": "card to remove when at the soft cap"}),
    ]),
    "campfire": (cmd_campfire, "smith / prune / dig", [
        ("--option", {"required": True, "choices": ["smith", "prune", "dig"]}),
        ("--card", {}),
    ]),
    "shop": (cmd_shop, "list or buy", [
        ("--buy", {}),
    ]),
    "annotate": (cmd_annotate, "mark a map node", [
        ("--node", {"required": True}),
        ("--mark", {"help": "a short marker, or 'clear' to remove"}),
    ]),
    "badges": (cmd_badges, "evaluate end-of-act badges", []),
    "new-run": (cmd_new_run, "start or restart a climb", [
        ("--seed", {"type": int, "default": 0}),
    ]),
}

HANDLERS = {verb: row[0] for verb, row in VERBS.items()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="spire's headless run loop (JSON in/out).")
    parser.add_argument("--path", default=".", help="target repo root (default: .)")
    sub = parser.add_subparsers(dest="command", required=True)
    for verb, (_handler, help_text, flags) in VERBS.items():
        child = sub.add_parser(verb, help=help_text)
        for flag, options in flags:
            child.add_argument(flag, **options)
    return parser


def dispatch(argv: list[str] | None = None) -> dict:
    args = build_parser().parse_args(argv)
    try:
        run = Run(args.path)
        return HANDLERS[args.command](run, args)
    except RunError as exc:
        return exc.payload()
    except Exception as exc:  # noqa: BLE001 - the client must never see a traceback
        detail = f"{type(exc).__name__}: {exc}"
        return {"ok": False, "error": {"code": "internal", "message": detail}}


def main(argv: list[str] | None = None) -> int:
    print(json.dumps(dispatch(argv), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
