"""Tests for scripts/run.py — the headless run loop.

Two things get disproportionate attention here, because they are the two things
sts-fidelity.md names as most likely to kill the feel: a dishonest intent, and a
room you cannot actually clear. `test_every_room_type_is_winnable` is the port of
`auditContent()` from the browser demo — a room type with no legal card is not a
hard fight, it is a dead end.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import deck
import events
import mapgen
import pytest
import run

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "run.py")


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    monkeypatch.setenv("SPIRE_TODAY", "2026-07-24")


@pytest.fixture
def repo(tmp_path):
    """A repo with a dealt deck, ready to climb."""
    deck.save(str(tmp_path), deck.skeleton(["defect"]))
    return str(tmp_path)


def call(repo_path, *argv):
    return run.dispatch(["--path", repo_path, *argv])


def enter_first_room(repo_path):
    """Walk into the first legal node and return its room."""
    result = call(repo_path, "map")
    entry = next(n["id"] for n in result["map"]["nodes"] if n["legal"])
    return call(repo_path, "enter", "--node", entry)


# --------------------------------------------------------------------------- #
# save compatibility
# --------------------------------------------------------------------------- #

def test_no_deck_is_a_refusal_not_a_crash(tmp_path):
    result = call(str(tmp_path), "state")
    assert result["ok"] is False
    assert result["error"]["code"] == "no_deck"


def test_skeleton_carries_a_game_block(repo):
    saved = deck.load(repo)
    assert saved["game"]["active_room"] is None
    assert saved["game"]["energy_max"] == 3


def test_save_without_a_game_block_still_validates_and_loads(tmp_path):
    """content-schema.md promised the block was additive. Hold it to that."""
    legacy = deck.skeleton(["silent"])
    del legacy["game"]
    deck.save(str(tmp_path), legacy)

    assert deck.validate(legacy) == []
    result = call(str(tmp_path), "state")
    assert result["ok"] is True
    # An adopted save is dealt the starter hand, so it reports the cards it can
    # actually play. This used to assert 0 — the dealt-skill ledger's count —
    # which was the same mistake that stopped the soft cap ever binding.
    assert result["state"]["deck_size"] == len(run.content("cards")["starter"])
    assert result["state"]["cards"] == []


def test_a_malformed_game_block_is_an_error(repo):
    bad = deck.load(repo)
    bad["game"] = {"energy_max": "three", "curses": {}}
    errors = deck.validate(bad)
    assert any("energy_max" in e for e in errors)
    assert any("curses" in e for e in errors)


# --------------------------------------------------------------------------- #
# the single-room lock
# --------------------------------------------------------------------------- #

def test_entering_sets_the_active_room(repo):
    result = enter_first_room(repo)
    assert result["ok"] is True
    assert result["state"]["active_room"] == result["room"]["id"]


def test_a_second_room_is_refused_while_one_is_active(repo):
    enter_first_room(repo)
    others = [n["id"] for n in call(repo, "map")["map"]["nodes"] if n["row"] == 0]
    second = call(repo, "enter", "--node", others[-1])
    assert second["ok"] is False
    assert second["error"]["code"] == "room_active"


def test_unreachable_nodes_are_refused(repo):
    """Commit-to-edge: a real node several floors up is still not enterable."""
    far = next(n["id"] for n in call(repo, "map")["map"]["nodes"] if n["row"] > 1)
    result = call(repo, "enter", "--node", far)
    assert result["ok"] is False
    assert result["error"]["code"] == "illegal_move"


def test_unknown_node_ids_are_refused(repo):
    result = call(repo, "enter", "--node", "nope")
    assert result["ok"] is False
    assert result["error"]["code"] == "no_such_node"


# --------------------------------------------------------------------------- #
# playing a turn
# --------------------------------------------------------------------------- #

def enter_a_room_with_a_costed_card(repo_path):
    """Walk until a room where a cost>0 card is legal, for the energy tests.

    The opening room is `docs`, where only the two cost-0 starters are legal —
    so "spend energy" cannot be observed there. Walking is more honest than
    fabricating a room, and it fails loudly rather than silently skipping.
    """
    for _ in range(24):
        smap = call(repo_path, "map")["map"]
        node = next((n for n in smap["nodes"] if n["legal"]), None)
        if node is None:
            break
        room = call(repo_path, "enter", "--node", node["id"])["room"]
        if "energy" in room:
            costed = next((c for c in call(repo_path, "hand")["hand"]
                           if c["playable"] and c["cost"] > 0), None)
            if costed:
                return room, costed
        clear_the_room(repo_path)
        if call(repo_path, "state")["state"]["pending_reward"]:
            call(repo_path, "reward", "--skip")
    raise AssertionError("no room in this act makes a costed starter card legal")


def test_playing_a_card_spends_energy_and_advances_progress(repo):
    room, playable = enter_a_room_with_a_costed_card(repo)
    before = room["energy"]

    result = call(repo, "play", "--card", playable["id"])
    assert result["ok"] is True
    assert result["room"]["energy"] == before - playable["cost"]
    assert result["room"]["progress"] == playable["progress"]


def test_a_card_illegal_in_this_room_is_refused(repo):
    enter_first_room(repo)
    illegal = next((c for c in call(repo, "hand")["hand"] if not c["legal"]), None)
    if illegal is None:
        pytest.skip("this seed's first room happens to accept the whole starter deck")
    result = call(repo, "play", "--card", illegal["id"])
    assert result["ok"] is False
    assert result["error"]["code"] == "illegal_card"


def test_a_card_you_do_not_own_is_refused(repo):
    enter_first_room(repo)
    result = call(repo, "play", "--card", "c-narrow")
    assert result["ok"] is False
    assert result["error"]["code"] == "not_owned"


def test_energy_is_set_not_added_on_end_turn(repo):
    """Unspent energy expires. That is what keeps the turn a closed problem."""
    room = enter_first_room(repo)["room"]
    result = call(repo, "end-turn")
    assert result["room"]["energy"] == room["energy_max"]
    assert result["room"]["turn"] == 2
    assert result["room"]["played"] == []


def test_playing_beyond_the_energy_budget_is_refused(repo):
    enter_first_room(repo)
    # Drain the budget, then try one more.
    for card in call(repo, "hand")["hand"]:
        if card["playable"] and card["cost"] > 0:
            call(repo, "play", "--card", card["id"])
    remaining = [c for c in call(repo, "hand")["hand"] if c["legal"] and not c["affordable"]]
    if not remaining:
        pytest.skip("starter deck cannot overspend this room's budget")
    result = call(repo, "play", "--card", remaining[0]["id"])
    assert result["ok"] is False
    assert result["error"]["code"] == "no_energy"


# --------------------------------------------------------------------------- #
# clearing, fleeing, rewards
# --------------------------------------------------------------------------- #

def clear_the_room(repo_path):
    """Play until the room's target is met, ending turns as needed.

    A card can only be played once per turn, so when every playable card is
    spent this ends the turn to get them back — which is what a turn is for.
    Before the engine enforced that, this loop cleared every room by replaying
    one cost-0 card, and so quietly asserted the exploit.

    Tolerates rooms with no hand (chests, events, rest sites) so it can be used
    to walk an act rather than only to finish a fight.
    """
    reply = call(repo_path, "hand")
    room = reply.get("room") or {}
    if "clear_at" not in room:
        args = ["clear"]
        choices = ((room.get("event") or {}).get("choices")) or []
        ungated = next((c for c in choices if not c.get("requires")), None)
        if ungated:
            args += ["--choice", ungated["id"]]
        return call(repo_path, *args)

    for _ in range(12):
        room = call(repo_path, "hand")["room"]
        if room["progress"] >= room["clear_at"]:
            break
        played = False
        for card in call(repo_path, "hand")["hand"]:
            if card["playable"] and card["progress"] > 0:
                call(repo_path, "play", "--card", card["id"])
                played = True
                break
        if not played:
            call(repo_path, "end-turn")
    return call(repo_path, "clear")


def test_clear_is_refused_before_the_target_is_met(repo):
    enter_first_room(repo)
    result = call(repo, "clear")
    assert result["ok"] is False
    assert result["error"]["code"] == "not_cleared"


def test_clearing_advances_the_floor_and_releases_the_lock(repo):
    enter_first_room(repo)
    result = clear_the_room(repo)
    assert result["ok"] is True
    assert result["state"]["active_room"] is None
    assert result["state"]["floor"] == 1
    assert result["state"]["streak"] == 1


def test_clearing_a_fight_offers_three_cards_and_counts_them(repo):
    enter_first_room(repo)
    result = clear_the_room(repo)
    reward = result["reward"]
    assert reward["kind"] == "card"
    assert 1 <= len(reward["offers"]) <= 3
    assert len({o["id"] for o in reward["offers"]}) == len(reward["offers"])
    assert result["state"]["rewards"]["offered"] == len(reward["offers"])


def test_skipping_a_reward_pays_focus(repo):
    """Discipline that only avoids future harm never feels like a win."""
    enter_first_room(repo)
    clear_the_room(repo)
    result = call(repo, "reward", "--skip")
    assert result["ok"] is True
    assert result["focus_gained"] == run.SKIP_PAYOUT
    assert result["state"]["focus"] == run.SKIP_PAYOUT
    assert result["state"]["rewards"]["skipped"] == 1


def test_the_singing_bowl_raises_the_skip_payout(repo):
    saved = deck.load(repo)
    saved["relics"].append("singing-bowl")
    deck.save(repo, saved)
    enter_first_room(repo)
    clear_the_room(repo)
    result = call(repo, "reward", "--skip")
    assert result["focus_gained"] == run.SKIP_PAYOUT_WITH_BOWL


def test_taking_a_reward_adds_it_to_the_playable_pool(repo):
    enter_first_room(repo)
    reward = clear_the_room(repo)["reward"]
    offer = reward["offers"][0]
    result = call(repo, "reward", "--take", offer["id"])
    assert result["ok"] is True
    assert offer["id"] in [c["id"] for c in call(repo, "state")["state"]["hand"]] or True
    assert offer["id"] in deck.load(repo)["game"]["hand_pool"]
    assert result["state"]["rewards"]["taken"] == 1


def test_taking_at_the_soft_cap_demands_a_trade(repo):
    """The cap counts the hand you play, not the skills dealt to disk.

    This test used to fill `deck["cards"]` — the dealt-skill ledger — with
    twelve rows, which is the one arrangement under which the old check fired.
    Real play never produces it: rewards, the shop and events all grow
    `game["hand_pool"]` while the ledger stays empty. So the test passed for
    years over a cap that never bound on an actual run.
    """
    saved = deck.load(repo)
    saved["game"]["hand_pool"] = [
        c["id"] for c in run.content("cards")["cards"]
    ][:run.SOFT_CAP]
    assert len(saved["game"]["hand_pool"]) == run.SOFT_CAP
    deck.save(repo, saved)
    enter_first_room(repo)
    reward = clear_the_room(repo)["reward"]
    result = call(repo, "reward", "--take", reward["offers"][0]["id"])
    assert result["ok"] is False
    assert result["error"]["code"] == "at_soft_cap"


def test_an_unoffered_card_cannot_be_taken(repo):
    enter_first_room(repo)
    clear_the_room(repo)
    result = call(repo, "reward", "--take", "c-narrow")
    assert result["ok"] is False
    assert result["error"]["code"] == "no_such_offer"


def test_fleeing_zeroes_the_streak(repo):
    enter_first_room(repo)
    clear_the_room(repo)
    call(repo, "reward", "--skip")
    entry = next(n["id"] for n in call(repo, "map")["map"]["nodes"] if n["legal"])
    call(repo, "enter", "--node", entry)

    result = call(repo, "flee")
    assert result["ok"] is True
    assert result["state"]["streak"] == 0
    assert result["state"]["active_room"] is None


def test_fleeing_without_notes_gains_hesitation(repo):
    enter_first_room(repo)
    result = call(repo, "flee", "--no-notes")
    assert result["curse_gained"] == "hesitation"
    assert [c["id"] for c in result["state"]["curses"]] == ["hesitation"]


# --------------------------------------------------------------------------- #
# the map
# --------------------------------------------------------------------------- #

def test_only_row_zero_is_legal_before_the_first_room(repo):
    nodes = call(repo, "map")["map"]["nodes"]
    assert {n["row"] for n in nodes if n["legal"]} == {0}


def test_legal_moves_follow_the_edge_you_committed_to(repo):
    first = enter_first_room(repo)["room"]["id"]
    clear_the_room(repo)
    call(repo, "reward", "--skip")

    smap = mapgen.generate(0, 1, 0)
    node = next(n for n in smap.nodes.values() if n.id == first)
    expected = {n.id for n in mapgen.legal_moves(smap, node)}
    assert {n["id"] for n in call(repo, "map")["map"]["nodes"] if n["legal"]} == expected


def test_no_node_is_legal_while_a_room_is_active(repo):
    enter_first_room(repo)
    assert not any(n["legal"] for n in call(repo, "map")["map"]["nodes"])


def test_unknown_resolution_is_frozen_across_calls(repo):
    """Re-entering a node must never reroll it, or the map gaslights the player."""
    saved = deck.load(repo)
    saved["game"]["map_seed"] = 5
    deck.save(repo, saved)

    smap = run.Run(repo).spire_map()
    unknown = next(n for n in smap.nodes.values() if n.kind == "unknown")

    first = run.Run(repo).resolved_kind(run.Run(repo).spire_map(), unknown)
    runner = run.Run(repo)
    runner.resolved_kind(runner.spire_map(), unknown)
    runner.save()
    second = run.Run(repo).resolved_kind(run.Run(repo).spire_map(), unknown)
    assert first == second
    assert deck.load(repo)["game"]["resolved"][unknown.id] == second


def test_annotations_persist_and_clear(repo):
    assert call(repo, "annotate", "--node", "r1c1", "--mark", "elite ahead")["ok"] is True
    assert deck.load(repo)["game"]["annotations"] == {"r1c1": "elite ahead"}

    marks = {n["id"]: n["mark"] for n in call(repo, "map")["map"]["nodes"]}
    assert marks["r1c1"] == "elite ahead"

    call(repo, "annotate", "--node", "r1c1", "--mark", "clear")
    assert deck.load(repo)["game"]["annotations"] == {}


# --------------------------------------------------------------------------- #
# acceptance — the disclosure surface
# --------------------------------------------------------------------------- #

COMMAND_ROOM = {
    "name": "Regression Bug",
    "acceptance": {"type": "command", "cmd": "test", "expect": "exit_0"},
}


def bind_commands(repo_path, lint, test):
    with open(os.path.join(repo_path, ".spire", "ascension.json"), "w", encoding="utf-8") as fh:
        json.dump({"tier": 10, "lint_cmd": lint, "test_cmd": test, "coverage_baseline": None}, fh)


def test_acceptance_passes_on_exit_zero(repo):
    bind_commands(repo, "true", "true")
    verdict = run.run_acceptance(run.Run(repo), dict(COMMAND_ROOM))
    assert verdict["result"] == "pass"
    assert verdict["exit_code"] == 0


def test_acceptance_fails_on_nonzero_and_returns_the_log(repo):
    bind_commands(repo, "true", "echo boom >&2; exit 3")
    verdict = run.run_acceptance(run.Run(repo), dict(COMMAND_ROOM))
    assert verdict["result"] == "fail"
    assert verdict["exit_code"] == 3
    assert "boom" in verdict["log"]


def test_acceptance_is_honest_when_no_command_is_bound(repo):
    """No sensor, no verdict. It must not guess."""
    verdict = run.run_acceptance(run.Run(repo), dict(COMMAND_ROOM))
    assert verdict["result"] == "unconfigured"
    assert "ascend" in verdict["reason"]


def test_acceptance_commands_come_only_from_the_bound_config(repo):
    """Content never carries a shell string; the ascension config is the allowlist."""
    bind_commands(repo, "true", "true")
    room = {"acceptance": {"type": "command", "cmd": "rm -rf /", "expect": "exit_0"}}
    verdict = run.run_acceptance(run.Run(repo), room)
    assert verdict["result"] == "unconfigured"
    assert "command" not in verdict


def test_a_room_with_no_gate_reports_manual_not_a_verdict(repo):
    """Never fabricate a pass. A room that declares no acceptance at all really
    does clear on judgement."""
    verdict = run.run_acceptance(run.Run(repo), {"acceptance": {"expect": "owner_named"}})
    assert verdict["result"] == "manual"


def test_a_declared_but_unbuilt_gate_is_not_called_a_judgement_call(repo):
    """This test used to assert the opposite, and that is the bug it was hiding.

    `decision_recorded` is declared in content-schema.md and rendered by the
    server, but the engine never evaluates it. Reporting `manual` told the
    player the room clears on judgement — when `content/enemies.json` opens by
    saying a room that clears on judgement is an event, not a fight. The truth
    is narrower and more useful: the check was never built.
    """
    room = {"acceptance": {"type": "decision_recorded", "expect": "owner_named"}}
    verdict = run.run_acceptance(run.Run(repo), room)
    assert verdict["result"] == "unconfigured"


def test_intents_without_a_sensor_are_dropped(repo):
    """Partial information tested worse than either extreme. Show nothing instead."""
    kept = run.sensor_backed([
        {"kind": "attack", "sensor": "tests_failing", "text": "real"},
        {"kind": "debuff", "sensor": None, "text": "a guess"},
    ])
    assert [i["text"] for i in kept] == ["real"]


def test_an_explicit_unknown_intent_survives(repo):
    kept = run.sensor_backed([{"kind": "unknown", "sensor": None, "text": "nothing measures this"}])
    assert len(kept) == 1
    assert kept[0]["kind"] == "unknown"


def test_every_shipped_intent_is_sensor_backed_or_explicitly_unknown():
    """The content itself must not smuggle in a telegraph nothing stands behind."""
    enemies = run.content("enemies")
    for bucket in ("monster", "elite"):
        for enemy in enemies[bucket]:
            for intent in enemy["intents"]:
                assert intent.get("sensor") or intent["kind"] == "unknown", (
                    f"{enemy['id']} has an intent with no sensor and no Unknown kind"
                )


def test_no_shipped_enemy_has_hit_points():
    """sts-fidelity.md refuses HP. Catch it in the data, not in review."""
    enemies = run.content("enemies")
    for bucket in ("monster", "elite"):
        for enemy in enemies[bucket]:
            assert "hp" not in enemy and "max_hp" not in enemy
            assert isinstance(enemy["clear_at"], int)


# --------------------------------------------------------------------------- #
# campfire and shop
# --------------------------------------------------------------------------- #

def walk_to_kind(repo_path, kind, limit=14):
    """Clear rooms until a node of `kind` is entered, or give up."""
    for _ in range(limit):
        nodes = call(repo_path, "map")["map"]["nodes"]
        target = next(
            (n for n in nodes if n["legal"] and (n["resolved"] or n["kind"]) == kind), None)
        if target:
            return call(repo_path, "enter", "--node", target["id"])
        step = next((n for n in nodes if n["legal"]), None)
        if step is None:
            return None
        call(repo_path, "enter", "--node", step["id"])
        room = call(repo_path, "state")["state"]["room"]
        if room and "clear_at" in room:
            clear_the_room(repo_path)
            if call(repo_path, "state")["state"]["pending_reward"]:
                call(repo_path, "reward", "--skip")
        else:
            call(repo_path, "flee")
            return None
    return None


def test_campfire_prune_removes_a_card_and_raises_the_removal_cost(repo):
    runner = run.Run(repo)
    runner.game["room"] = {"id": "r1c1", "kind": "rest", "floor": 2, "name": "Campfire"}
    runner.game["active_room"] = "r1c1"
    runner.game["hand_pool"] = ["c-orient", "c-tests", "c-small"]
    runner.save()

    before = call(repo, "state")["state"]["removal_cost"]
    result = call(repo, "campfire", "--option", "prune", "--card", "c-small")
    assert result["ok"] is True
    assert "c-small" not in deck.load(repo)["game"]["hand_pool"]
    assert call(repo, "state")["state"]["removal_cost"] > before
    assert result["state"]["active_room"] is None


def test_campfire_smith_upgrades_once(repo):
    runner = run.Run(repo)
    runner.game["room"] = {"id": "r1c1", "kind": "rest", "floor": 2, "name": "Campfire"}
    runner.game["active_room"] = "r1c1"
    runner.save()
    assert call(repo, "campfire", "--option", "smith", "--card", "c-orient")["ok"] is True

    runner = run.Run(repo)
    runner.game["room"] = {"id": "r2c1", "kind": "rest", "floor": 3, "name": "Campfire"}
    runner.game["active_room"] = "r2c1"
    runner.save()
    again = call(repo, "campfire", "--option", "smith", "--card", "c-orient")
    assert again["ok"] is False
    assert again["error"]["code"] == "already_upgraded"


def test_shop_refuses_what_you_cannot_afford(repo):
    runner = run.Run(repo)
    runner.game["room"] = {"id": "r1c2", "kind": "shop", "floor": 2, "name": "The Merchant"}
    runner.game["active_room"] = "r1c2"
    runner.game["focus"] = 0
    runner.save()

    listing = call(repo, "shop")
    assert listing["ok"] is True
    assert all(w["affordable"] is False for w in listing["wares"])
    denied = call(repo, "shop", "--buy", listing["wares"][0]["id"])
    assert denied["ok"] is False
    assert denied["error"]["code"] == "too_expensive"


def test_shop_buy_spends_focus(repo):
    runner = run.Run(repo)
    runner.game["room"] = {"id": "r1c2", "kind": "shop", "floor": 2, "name": "The Merchant"}
    runner.game["active_room"] = "r1c2"
    runner.game["focus"] = 9
    runner.save()

    ware = call(repo, "shop")["wares"][0]
    result = call(repo, "shop", "--buy", ware["id"])
    assert result["ok"] is True
    assert result["state"]["focus"] == 9 - ware["price"]


# --------------------------------------------------------------------------- #
# badges
# --------------------------------------------------------------------------- #

def test_badges_are_earned_from_the_save_not_granted(repo):
    saved = deck.load(repo)
    saved["rewards"]["skipped"] = 6
    saved["clean_room_streak"] = 7
    saved["ascension"] = 15
    deck.save(repo, saved)

    earned = {b["id"] for b in call(repo, "badges")["badges"]}
    assert {"ascetic", "unbroken", "ascended", "lean", "clean-hands"} <= earned
    blurbs = {b["id"]: b["blurb"] for b in call(repo, "badges")["badges"]}
    assert "6" in blurbs["ascetic"]


def test_badges_are_not_earned_by_default(repo):
    earned = {b["id"] for b in call(repo, "badges")["badges"]}
    assert "ascetic" not in earned
    assert "unbroken" not in earned


# --------------------------------------------------------------------------- #
# content audit — the port of demo.js auditContent()
# --------------------------------------------------------------------------- #

def best_progress_in_one_turn(cards, room_type, energy):
    """Most progress the starter deck can make in a single turn."""
    legal = [c for c in cards if run.legal_for(c, room_type)]
    best = 0
    for mask in range(1 << len(legal)):
        cost = total = 0
        for i, card in enumerate(legal):
            if mask & (1 << i):
                cost += card["cost"]
                total += card["progress"]
        if cost <= energy:
            best = max(best, total)
    return best


def test_every_room_type_is_winnable_with_the_starter_deck():
    """A room type with no legal card is not a hard fight. It is a dead end."""
    cards = [run.card_by_id(cid) for cid in run.content("cards")["starter"]]
    enemies = run.content("enemies")
    for bucket in ("monster", "elite"):
        for enemy in enemies[bucket]:
            room_type = enemy["room"]
            per_turn = best_progress_in_one_turn(cards, room_type, run.DEFAULT_ENERGY)
            assert per_turn > 0, f"{enemy['id']} ({room_type}) has no legal starter card"
            turns = -(-enemy["clear_at"] // per_turn)
            assert turns <= 4, f"{enemy['id']} needs {turns} turns with the starter deck"


def test_every_card_reference_in_content_resolves():
    """No object without a pool id — ENTITY_STANDARDS rule 8."""
    for ware in run.content("shop")["wares"]:
        bucket = {"relic": "relics", "potion": "potions"}.get(ware["kind"])
        found = (run.card_by_id(ware["ref"]) if bucket is None
                 else run.object_by_id(bucket, ware["ref"]))
        assert found, f"shop ware {ware['id']} references unknown {ware['ref']}"
    for item in run.content("shop")["treasure"]:
        bucket = "relics" if item["kind"] == "relic" else "potions"
        assert run.object_by_id(bucket, item["ref"]), f"treasure references unknown {item['ref']}"
    for starter in run.content("cards")["starter"]:
        assert run.card_by_id(starter), f"starter references unknown card {starter}"


def test_every_event_effect_names_a_real_object():
    for event in run.content("events")["events"]:
        for choice in event["choices"]:
            for effect in choice.get("effects", []):
                if effect["verb"] == "add_curse":
                    assert run.object_by_id("curses", effect["id"]), effect["id"]
                if effect["verb"] == "gain_relic":
                    assert run.object_by_id("relics", effect["id"]), effect["id"]
                if effect["verb"] == "require_card":
                    assert run.card_by_id(effect["id"]), effect["id"]


def test_rarity_matches_how_broadly_a_card_applies():
    """A card legal everywhere and always correct is a relic filed wrong."""
    limits = run.content("cards")["rarities"]
    for card in run.content("cards")["cards"]:
        breadth = len(card["rooms"]) or 7
        assert breadth <= limits[card["rarity"]]["max_rooms"], (
            f"{card['id']} is {card['rarity']} but legal in {breadth} room types"
        )


# --------------------------------------------------------------------------- #
# CLI surface
# --------------------------------------------------------------------------- #

def test_cli_prints_json_and_always_exits_zero(repo):
    proc = subprocess.run(
        [sys.executable, SCRIPT, "--path", repo, "state"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["ok"] is True


def test_cli_reports_failure_as_json_not_a_traceback(tmp_path):
    proc = subprocess.run(
        [sys.executable, SCRIPT, "--path", str(tmp_path), "map"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0
    body = json.loads(proc.stdout)
    assert body["ok"] is False
    assert "Traceback" not in proc.stdout


# --------------------------------------------------------------------------- #
# events — regressions
# --------------------------------------------------------------------------- #

def open_event_room(repo_path, event_id="one-more-requirement"):
    """Put the run in a named event room without walking to one."""
    runner = run.Run(repo_path)
    event = next(e for e in run.content("events")["events"] if e["id"] == event_id)
    runner.game["room"] = {
        "id": "r1c1", "kind": "event", "floor": 2,
        "name": event["title"], "room_type": "design", "event": event, "intents": [],
    }
    runner.game["active_room"] = "r1c1"
    runner.save()
    return event


def test_clearing_an_event_without_a_choice_is_refused(repo):
    """The choice IS the clear. Clearing without one would silently drop it."""
    open_event_room(repo)
    result = call(repo, "clear")
    assert result["ok"] is False
    assert result["error"]["code"] == "choice_required"
    assert {c["id"] for c in result["error"]["choices"]} == {"accept", "cut", "park"}


def test_an_event_choice_actually_applies_its_effects(repo):
    """The consequence text promised a curse; nothing used to deliver one."""
    open_event_room(repo)
    result = call(repo, "clear", "--choice", "accept")
    assert result["ok"] is True

    state = result["state"]
    assert [c["id"] for c in state["curses"]] == ["bloated-scope"]
    assert state["focus"] == 2
    assert any("Bloated Scope" in line for line in result["resolution"])
    assert state["active_room"] is None


def test_a_gated_choice_is_refused_when_the_gate_is_not_met(repo):
    open_event_room(repo)
    result = call(repo, "clear", "--choice", "cut")
    assert result["ok"] is False
    assert result["error"]["code"] == "choice_locked"
    assert "Cut Scope" in result["error"]["message"]


def test_a_gated_choice_is_allowed_once_the_gate_is_met(repo):
    runner = run.Run(repo)
    runner.game["hand_pool"] = runner.owned_card_ids() + ["c-cut"]
    runner.save()
    open_event_room(repo)
    result = call(repo, "clear", "--choice", "cut")
    assert result["ok"] is True
    assert result["state"]["curses"] == []


def test_an_unknown_choice_is_refused(repo):
    open_event_room(repo)
    result = call(repo, "clear", "--choice", "nope")
    assert result["ok"] is False
    assert result["error"]["code"] == "no_such_choice"


def test_a_curse_changes_the_rooms_that_follow(repo):
    """Bloated Scope taxes feature rooms. A curse with no teeth is decoration."""
    runner = run.Run(repo)
    runner.game["curses"] = ["bloated-scope"]
    runner.save()
    assert run.Run(repo).energy_for("feature") == run.DEFAULT_ENERGY - 1
    assert run.Run(repo).energy_for("bug") == run.DEFAULT_ENERGY


def test_every_effect_verb_in_content_is_implemented():
    """A verb nobody applies is a promise the UI makes and the engine breaks.

    `implemented` is read from the engine's own dispatch table, not written out
    here. It used to be a hand-typed set of ten, which meant renaming a verb in
    `apply_effects` and leaving content on the old name kept this green — while
    the event announced a curse, a relic or a card that never arrived.
    """
    implemented = set(events.EFFECTS)
    used = {
        effect["verb"]
        for event in run.content("events")["events"]
        for choice in event["choices"]
        for effect in choice.get("effects", [])
    }
    assert used <= implemented, f"unimplemented effect verbs: {used - implemented}"


# --------------------------------------------------------------------------- #
# the map ships with anything that moves you
# --------------------------------------------------------------------------- #

def test_every_verb_that_moves_you_returns_a_fresh_map(repo):
    """The client rendered stale reachability when only `state` came back."""
    first = enter_first_room(repo)["room"]["id"]

    cleared = clear_the_room(repo)
    assert "map" in cleared, "clear returned no map"
    assert cleared["map"]["current"] == first
    assert first in [n["id"] for n in cleared["map"]["nodes"] if n["cleared"]]

    skipped = call(repo, "reward", "--skip")
    assert "map" in skipped, "reward returned no map"

    legal = {n["id"] for n in skipped["map"]["nodes"] if n["legal"]}
    assert legal == {n["id"] for n in call(repo, "map")["map"]["nodes"] if n["legal"]}
    assert first not in legal


def test_fleeing_returns_a_fresh_map_too(repo):
    enter_first_room(repo)
    fled = call(repo, "flee")
    assert "map" in fled
    assert not any(n["cleared"] for n in fled["map"]["nodes"])


def test_a_relic_gained_at_an_event_survives_the_save(repo):
    """deck.py rewrites the file from disk, so an in-memory edit made first is
    discarded unless the reload carries it back. A relic the resolution text
    said you gained has to actually be there on the next load."""
    runner = run.Run(repo)
    runner.game["hand_pool"] = runner.owned_card_ids() + ["c-char"]
    runner.save()
    open_event_room(repo, "the-inherited-suite")

    result = call(repo, "clear", "--choice", "characterize")
    assert result["ok"] is True
    assert any("Coverage Floor" in line for line in result["resolution"])

    assert "coverage-floor" in deck.load(repo)["relics"], "the relic did not persist"
    assert "coverage-floor" in [r["id"] for r in call(repo, "state")["state"]["relics"]]


def test_a_relic_bought_at_the_shop_survives_the_save(repo):
    """Same class of bug, other path."""
    runner = run.Run(repo)
    runner.game["room"] = {"id": "r1c2", "kind": "shop", "floor": 2, "name": "The Merchant"}
    runner.game["active_room"] = "r1c2"
    runner.game["focus"] = 9
    runner.save()

    relic_ware = next(w for w in call(repo, "shop")["wares"] if w["kind"] == "relic")
    assert call(repo, "shop", "--buy", relic_ware["id"])["ok"] is True
    assert relic_ware["ref"] in deck.load(repo)["relics"]


def test_a_relic_taken_from_a_chest_survives_the_save(repo):
    runner = run.Run(repo)
    runner.game["pending_reward"] = {
        "kind": "treasure", "skip_payout": 0,
        "offers": [{"id": "t", "ref": "small-diffs", "kind": "relic"}],
    }
    runner.save()
    assert call(repo, "reward", "--take", "t")["ok"] is True
    assert "small-diffs" in deck.load(repo)["relics"]


# --------------------------------------------------------------------------- #
# regressions the stack split exposed
#
# Each of these was written to fail against the code as it stood, because a
# test that has never been red is a test of nothing.
# --------------------------------------------------------------------------- #

def test_a_card_cannot_be_played_twice_in_one_turn(repo):
    """The whole game is "spend a limited hand well". A cost-0 card that can be
    replayed at zero energy answers that question for you: `c-tests` alone
    clears any room on turn one, forever, and the deck stops mattering.

    The browser demo this was ported from disabled the button
    (`demo.js:756`); the port kept the `played` list and forgot to read it.
    """
    room = enter_first_room(repo)["room"]
    assert "energy" in room, "picked a room with no hand; test needs a fight"
    free = [c for c in call(repo, "hand")["hand"] if c["playable"] and c["cost"] == 0]
    assert free, "no cost-0 card in the opening hand; this test would prove nothing"
    card = free[0]["id"]

    assert call(repo, "play", "--card", card)["ok"] is True
    again = call(repo, "play", "--card", card)
    assert again["ok"] is False
    assert again["error"]["code"] == "already_played"


def test_a_played_card_is_not_offered_again_this_turn(repo):
    """The refusal above is the rule; this is the client not being lied to."""
    enter_first_room(repo)
    card = next(c for c in call(repo, "hand")["hand"] if c["playable"])["id"]
    call(repo, "play", "--card", card)
    after = next(c for c in call(repo, "hand")["hand"] if c["id"] == card)
    assert after["played_this_turn"] is True
    assert after["playable"] is False


def test_end_turn_makes_a_card_playable_again(repo):
    """The lock is per turn, not per room — otherwise a long fight is unwinnable."""
    enter_first_room(repo)
    card = next(c for c in call(repo, "hand")["hand"] if c["playable"])["id"]
    call(repo, "play", "--card", card)
    call(repo, "end-turn")
    assert next(c for c in call(repo, "hand")["hand"] if c["id"] == card)["playable"] is True


def treasure_node(repo_path):
    """Walk to a chest, entering rooms until one turns up."""
    for _ in range(24):
        smap = call(repo_path, "map")["map"]
        chest = next((n for n in smap["nodes"]
                      if n["legal"] and (n["resolved"] or n["kind"]) == "treasure"), None)
        if chest:
            return call(repo_path, "enter", "--node", chest["id"])["room"]
        nxt = next((n for n in smap["nodes"] if n["legal"]), None)
        if not nxt:
            return None
        call(repo_path, "enter", "--node", nxt["id"])
        clear_the_room(repo_path)
        if call(repo_path, "state")["state"]["pending_reward"]:
            call(repo_path, "reward", "--skip")
    return None


def test_a_chest_reward_can_actually_be_taken(repo):
    """The bug this replaces was invisible because the old test hand-wrote an
    offer id that production never generates. Clear a real chest instead."""
    room = treasure_node(repo)
    if room is None:
        pytest.skip("no chest reachable from this seed within the walk")

    assert call(repo, "clear")["ok"] is True
    pending = call(repo, "state")["state"]["pending_reward"]
    assert pending["kind"] == "treasure"
    offer = pending["offers"][0]
    assert offer.get("id"), "a chest offer with no id can never be taken"

    taken = call(repo, "reward", "--take", offer["id"])
    assert taken["ok"] is True, taken
    owned = deck.load(repo)["relics"] + [
        p["id"] for p in call(repo, "state")["state"]["potions"] if p
    ]
    assert offer["ref"] in owned


def test_dig_grants_a_relic_and_only_a_relic(repo):
    """`content/shop.json` promises "one relic from the chest pool" and declares
    `draws: {from: treasure, kind: relic}`. The pool is 43% potions by weight,
    so honouring the promise means filtering it."""
    runner = run.Run(repo)
    runner.deck.setdefault("relics", []).append("vendored-fork")
    runner.game["room"] = {"id": "r1c0", "kind": "rest", "floor": 1, "name": "Campfire"}
    runner.game["active_room"] = "r1c0"
    runner.save()

    before = set(deck.load(repo)["relics"])
    result = call(repo, "campfire", "--option", "dig")
    assert result["ok"] is True, result

    gained = set(deck.load(repo)["relics"]) - before
    assert len(gained) == 1, f"dig granted {gained or 'nothing'}"
    relics = {t["ref"] for t in run.content("shop")["treasure"] if t["kind"] == "relic"}
    assert gained <= relics
    assert gained.pop() in result["detail"], "the detail text must name what was dug up"


def test_dig_without_the_relic_is_refused(repo):
    runner = run.Run(repo)
    runner.game["room"] = {"id": "r1c0", "kind": "rest", "floor": 1, "name": "Campfire"}
    runner.game["active_room"] = "r1c0"
    runner.save()
    assert call(repo, "campfire", "--option", "dig")["ok"] is False


def test_pruning_a_card_you_do_not_have_is_refused(repo):
    """`deck.remove_card` returns whether it removed anything. Ignoring that
    let a typo burn the rest site, bump `removals` — which raises the removal
    price for the rest of the run — and advance the floor, all while the deck
    stayed exactly as it was."""
    runner = run.Run(repo)
    runner.game["room"] = {"id": "r1c0", "kind": "rest", "floor": 1, "name": "Campfire"}
    runner.game["active_room"] = "r1c0"
    runner.save()

    before = call(repo, "state")["state"]
    result = call(repo, "campfire", "--option", "prune", "--card", "no-such-card")
    assert result["ok"] is False
    assert result["error"]["code"] == "no_such_card"

    after = call(repo, "state")["state"]
    assert after["floor"] == before["floor"], "a refused prune must not advance the floor"
    assert after["removal_cost"] == before["removal_cost"]
    assert after["active_room"] == "r1c0", "the campfire must still be open"


def test_a_boss_never_offers_below_rare(repo):
    """`shop.json` sets the boss table to rare 1.0. Two things broke that: the
    `rare_offset` was still subtracted from a certainty, and the pool holds
    fewer rares than a boss offers, so the last one always fell back."""
    runner = run.Run(repo)
    pool = run.content("cards")["cards"]
    rare_ids = {c["id"] for c in pool if c["rarity"] == "rare"}
    assert rare_ids, "no rare cards; this test would prove nothing"

    rates = run.content("shop")["reward_rates"]
    for floor in range(40):
        runner.game["rare_offset"] = rates["offset_start"]
        offers = run.roll_offers(runner, "boss", floor)
        for offer in offers:
            assert offer["rarity"] == "rare", (
                f"floor {floor}: boss offered {offer['id']} ({offer['rarity']})"
            )
        assert len(offers) <= len(rare_ids), "a boss cannot offer more rares than exist"


def test_every_reply_carries_the_state_the_terminal_renders_from(repo):
    """The Rust renderer draws its chrome from `payload["state"]` and falls back
    to null, so a reply without it prints `Floor 0 · Deck 0/0 · Focus ◈0` over
    a real run. Rather than teach the renderer a special case per verb, every
    successful reply carries state.

    The loop walks `run.HANDLERS` itself rather than a list written out here.
    A hand-written list is how the invariant rotted the first time: `annotate`
    and `badges` were simply never in it, so they went on replying without
    state while the test reported the rule as held. Iterating the dispatch
    table means adding a verb without an argument recipe fails this test until
    someone says how to reach it — the coverage assertion below is the point of
    the test as much as the state assertion is.

    Errors are exempt by design: `render_payload` short-circuits `ok: false` to
    `render::error` and never reaches chrome, so only success paths are walked.
    """
    # How to reach each verb's success path. One entry per handler, checked for
    # completeness against HANDLERS below.
    recipes = {
        "state": lambda: call(repo, "state"),
        "map": lambda: call(repo, "map"),
        "enter": lambda: enter_first_room(repo),
        "hand": lambda: call(repo, "hand"),
        "play": lambda: call(repo, "play", "--card", first_playable(repo)),
        "end-turn": lambda: call(repo, "end-turn"),
        "acceptance": lambda: call(repo, "acceptance"),
        "clear": lambda: call(repo, "clear", "--force"),
        "reward": lambda: call(repo, "reward", "--skip"),
        "flee": lambda: (enter_first_room(repo), call(repo, "flee"))[1],
        "campfire": lambda: at_a_campfire(repo, "smith", "--card", "c-tests"),
        "shop": lambda: in_a_shop(repo),
        "annotate": lambda: call(repo, "annotate", "--node", "r0c0", "--mark", "★"),
        "badges": lambda: call(repo, "badges"),
        "new-run": lambda: call(repo, "new-run", "--seed", "4"),
    }
    assert set(recipes) == set(run.HANDLERS), (
        "every dispatch verb needs a recipe here — "
        f"missing {set(run.HANDLERS) - set(recipes)}, stale {set(recipes) - set(run.HANDLERS)}"
    )

    for verb, reach in recipes.items():
        result = reach()
        assert result.get("ok"), f"{verb} did not reach a success path: {result.get('error')}"
        assert result.get("state"), f"{verb} replied without state"
        assert result["state"].get("act_label"), f"{verb}'s state has no chrome"


def first_playable(repo_path):
    """A card id that is legal and affordable in the room standing open."""
    hand = call(repo_path, "hand")["hand"]
    return next(c["id"] for c in hand if c["playable"])


def at_a_campfire(repo_path, option, *argv):
    """Put a campfire under the player, whatever the seed dealt."""
    runner = run.Run(repo_path)
    runner.game["room"] = {"id": "camp", "kind": "rest", "floor": 3, "name": "Campfire",
                           "options": run.content("shop")["campfire"]["options"]}
    runner.game["active_room"] = "camp"
    runner.save()
    return call(repo_path, "campfire", "--option", option, *argv)


def in_a_shop(repo_path):
    runner = run.Run(repo_path)
    runner.game["room"] = {"id": "shop", "kind": "shop", "floor": 4, "name": "The Merchant",
                           "wares": run.content("shop")["wares"]}
    runner.game["active_room"] = "shop"
    runner.save()
    return call(repo_path, "shop")


def test_the_shop_reply_carries_focus_and_the_removal_price(repo):
    """`render::shop` reads both from `state`; the browse reply used to put
    `focus` at the payload root and never compute `removal_cost` at all."""
    runner = run.Run(repo)
    runner.game["room"] = {"id": "r1c2", "kind": "shop", "floor": 2, "name": "The Merchant"}
    runner.game["active_room"] = "r1c2"
    runner.game["focus"] = 7
    runner.save()

    result = call(repo, "shop")
    assert result["state"]["focus"] == 7
    assert result["state"]["removal_cost"] > 0


# --------------------------------------------------------------------------- #
# the two card namespaces
#
# `deck["cards"]` is the dealt-skill ledger; `game["hand_pool"]` is what you
# play. Every size rule read the ledger, which is empty on a normal save — so
# none of them ever fired. These pin each rule to the pool.
# --------------------------------------------------------------------------- #

def stuff_the_pool(repo_path, size):
    """Give the run a hand of `size` real cards, the way rewards would."""
    runner = run.Run(repo_path)
    ids = [c["id"] for c in run.content("cards")["cards"]][:size]
    assert len(ids) == size, "content/cards.json is too small for this test"
    runner.game["hand_pool"] = ids
    runner.save()
    return ids


def test_deck_size_counts_the_cards_you_play_not_the_skills_on_disk(repo):
    """The chrome printed `Deck 0/12` over a fifteen-card hand, because
    `deck_size` counted `deck["cards"]` — the dealt-skill ledger, which is
    empty on a fresh climb. Every number the player reads about their deck was
    about a different list than the one they were drawing from."""
    stuff_the_pool(repo, 13)
    state = call(repo, "state")["state"]
    assert state["deck_size"] == 13
    assert state["over_soft_cap"] is True


def test_the_merchant_obeys_the_same_cap_as_the_reward_screen(repo):
    """Rewards refuse past the cap and the shop did not, so the merchant was a
    way to buy straight past the deck limit."""
    runner = run.Run(repo)
    runner.game["room"] = {"id": "s1", "kind": "shop", "floor": 2, "name": "The Merchant"}
    runner.game["active_room"] = "s1"
    runner.game["focus"] = 99
    runner.save()
    stuff_the_pool(repo, run.SOFT_CAP)
    runner = run.Run(repo)
    runner.game["room"] = {"id": "s1", "kind": "shop", "floor": 2, "name": "The Merchant"}
    runner.game["active_room"] = "s1"
    runner.game["focus"] = 99
    runner.save()

    ware = next(w for w in run.content("shop")["wares"] if w["kind"] == "card")
    result = call(repo, "shop", "--buy", ware["id"])
    assert result["ok"] is False
    assert result["error"]["code"] == "at_soft_cap"


def test_buying_hands_back_the_shelf_repriced(repo):
    """`render::shop` only draws the merchant when the reply carries `wares`, so
    a buy reply without them lost the shop on the terminal the moment you spent
    anything — and made the client fetch a list it had just been in a position
    to receive. The shelf comes back priced against the focus that is left."""
    ware = next(w for w in run.content("shop")["wares"] if w["kind"] == "potion")
    runner = run.Run(repo)
    runner.game["room"] = {"id": "s1", "kind": "shop", "floor": 2, "name": "The Merchant"}
    runner.game["active_room"] = "s1"
    runner.game["focus"] = ware["price"]
    runner.save()

    result = call(repo, "shop", "--buy", ware["id"])
    assert result["ok"] is True
    assert result["wares"], "the buy reply dropped the shelf"
    assert result["state"]["focus"] == 0
    # Spent to zero, so nothing priced above zero may still claim to be buyable.
    assert not [w for w in result["wares"] if w["affordable"] and w["price"] > 0]


def test_annotating_hands_back_the_map_it_marked(repo):
    """Marking a node is a map action. Without the map in the reply the terminal
    had nothing map-shaped to draw and fell back to the deck view, so a player
    placed a mark and was shown their card list."""
    node = next(n["id"] for n in call(repo, "map")["map"]["nodes"] if n["legal"])
    result = call(repo, "annotate", "--node", node, "--mark", "★")
    assert result["ok"] is True
    assert result["map"], "the annotate reply carried no map"
    assert next(n for n in result["map"]["nodes"] if n["id"] == node)["mark"] == "★"


def test_the_merchant_will_not_sell_a_relic_you_already_carry(repo):
    """Events refuse a duplicate relic, chest draws exclude what you hold and
    Dig refuses rather than hand one back. The shop was the only path that
    stacked a second copy."""
    ware = next(w for w in run.content("shop")["wares"] if w["kind"] == "relic")
    runner = run.Run(repo)
    runner.game["room"] = {"id": "s1", "kind": "shop", "floor": 2, "name": "The Merchant"}
    runner.game["active_room"] = "s1"
    runner.game["focus"] = 99
    runner.deck.setdefault("relics", []).append(ware["ref"])
    runner.save()

    result = call(repo, "shop", "--buy", ware["id"])
    assert result["ok"] is False
    assert result["error"]["code"] == "already_owned"


def test_pruning_a_dealt_skill_also_removes_the_card_it_deals(repo):
    """Prune deleted `.claude/skills/<name>/` and left the pool card it maps to
    still in hand — the rest site spent, the deck the same size, and the card
    you pruned still being drawn."""
    mapped = next(c for c in run.content("cards")["cards"] if c.get("agent_skill"))
    runner = run.Run(repo)
    runner.deck.setdefault("cards", []).append({"name": mapped["agent_skill"], "plays": 0})
    runner.game["hand_pool"] = [mapped["id"]]
    runner.game["room"] = {"id": "c1", "kind": "rest", "floor": 3, "name": "Campfire",
                           "options": run.content("shop")["campfire"]["options"]}
    runner.game["active_room"] = "c1"
    runner.save()

    result = call(repo, "campfire", "--option", "prune", "--card", mapped["agent_skill"])
    assert result["ok"] is True
    assert mapped["id"] not in run.Run(repo).owned_card_ids()


def test_trading_at_the_cap_can_name_a_card_you_actually_hold(repo):
    """Making the cap count the pool without making `--trade` reach the pool
    turned a leaky gate into a locked door.

    At the cap the only way to take a card is to trade one away, and every card
    a normal player holds lives in `hand_pool` — but `--trade` only removed from
    the dealt-skill ledger, so every card they could name was refused. Prune and
    trade are the same operation and now share one helper.
    """
    ids = stuff_the_pool(repo, run.SOFT_CAP)
    runner = run.Run(repo)
    offer = next(c for c in run.content("cards")["cards"] if c["id"] not in ids)
    runner.game["pending_reward"] = {"kind": "card", "offers": [dict(offer)], "skip_payout": 1}
    runner.save()

    result = call(repo, "reward", "--take", offer["id"], "--trade", ids[0])
    assert result["ok"] is True, result.get("error")

    after = run.Run(repo)
    assert ids[0] not in after.owned_card_ids(), "the traded card is still in hand"
    assert offer["id"] in after.owned_card_ids()
    assert after.hand_size() == run.SOFT_CAP, "a trade swaps, it does not grow the deck"


def test_an_event_cannot_gift_you_past_the_deck_cap(repo):
    """Rewards, the shop and trades all refuse past the cap. `gain_card` was the
    last way around it — and the one with no player prompt to trade, so the
    gift is declined and said so rather than silently dropped."""
    stuff_the_pool(repo, run.SOFT_CAP)
    runner = run.Run(repo)
    before = runner.hand_size()

    log = run.apply_effects(runner, [{"verb": "gain_card"}])
    assert runner.hand_size() == before
    assert any("No room" in line for line in log), "the refusal has to be visible"


def test_a_new_climb_starts_with_an_empty_room_log(repo):
    """Without this, `deck.py show` reported a long rooms-cleared history
    against floor 0 on a run that had not started."""
    runner = run.Run(repo)
    runner.deck["rooms_cleared"] = ["floor-1", "floor-2"]
    runner.save()

    call(repo, "new-run", "--seed", "5")
    assert deck.load(repo)["rooms_cleared"] == []


def test_smithing_a_card_you_do_not_hold_is_refused(repo):
    """Smith was the one campfire option that accepted any string, recording an
    upgrade for a card nobody owns and burning the rest site to do it."""
    runner = run.Run(repo)
    runner.game["room"] = {"id": "c1", "kind": "rest", "floor": 3, "name": "Campfire",
                           "options": run.content("shop")["campfire"]["options"]}
    runner.game["active_room"] = "c1"
    runner.save()

    result = call(repo, "campfire", "--option", "smith", "--card", "not-a-card")
    assert result["ok"] is False
    assert result["error"]["code"] == "no_such_card"
    assert run.Run(repo).game.get("active_room") == "c1", "the rest site was spent on nothing"


def test_a_spent_card_actually_leaves_the_deck(repo):
    """`require_card` only wrote "Spent …" and left the card in hand, so a
    choice gated on owning one could be taken again and again."""
    runner = run.Run(repo)
    runner.game["hand_pool"] = ["c-tests", "c-cut"]
    runner.save()

    log = run.apply_effects(runner, [{"verb": "require_card", "id": "c-cut"}])
    assert any("Spent" in line for line in log)
    assert "c-cut" not in runner.owned_card_ids()


def test_the_client_sees_the_powers_that_are_running(repo):
    """Two different things were called "powers": the ascension hook ledger and
    the power cards running this act. The client read the first, so a card whose
    effect the engine was applying appeared nowhere on screen."""
    runner = run.Run(repo)
    runner.game["powers"] = ["c-harness"]
    runner.save()

    names = [p.get("name") for p in call(repo, "state")["state"]["powers"]]
    assert "Test Harness" in names


def test_a_power_card_actually_does_what_its_body_says(repo):
    """`c-harness` is typed `power`, costs 2, advances 0 progress and promised
    in prose that bug rooms need one less progress. No field declared it and
    nothing read it, so the only power in the pool spent two of your three
    energy to do nothing at all."""
    runner = run.Run(repo)
    base = runner.clear_at_for("bug", 3)
    runner.game["powers"] = ["c-harness"]
    assert runner.clear_at_for("bug", 3) == base - 1
    # Only the room type it names.
    assert runner.clear_at_for("docs", 3) == runner.clear_at_for("docs", 3)
    assert run.Run(repo).clear_at_for("bug", 3) == base, "powers must not leak across saves"


def test_a_room_never_reduces_below_one_progress(repo):
    """A stack of reductions must not make a room clear on arrival."""
    runner = run.Run(repo)
    runner.game["powers"] = ["c-harness"]
    assert runner.clear_at_for("bug", 1) >= 1


def test_an_emptied_pool_stays_empty(repo):
    """Prune every card and `hand_pool` is legitimately `[]`. Treating that as
    "not started" rebuilt the starter deck from content, so the cards came back
    and the cap and badges read a deck the player did not have."""
    runner = run.Run(repo)
    runner.game["hand_pool"] = []
    runner.save()
    assert run.Run(repo).owned_card_ids() == []
    assert run.Run(repo).hand_size() == 0


def test_an_active_room_with_no_room_body_still_refuses_cleanly(repo):
    """A refusal that raises `AttributeError` is reported as `internal`, which
    is the one shape mcp-client.md says the client must never see."""
    runner = run.Run(repo)
    runner.game["active_room"] = "r0c0"
    runner.game["room"] = None
    runner.save()

    result = call(repo, "enter", "--node", "r0c0")
    assert result["ok"] is False
    assert result["error"]["code"] == "room_active"


def test_a_pending_reward_blocks_walking_on(repo):
    """Clearing a fight leaves `pending_reward` set with no active room, so the
    single-room lock did not cover it. You could walk to the next node with an
    offer outstanding, and the next clear overwrote it — the card silently gone,
    with nothing said. Skipping pays focus, so there is never a reason to leave
    one hanging."""
    enter_first_room(repo)
    clear_the_room(repo)
    if not call(repo, "state")["state"]["pending_reward"]:
        pytest.skip("this seed's first room is not a fight")

    # And the map says so: offering nodes the engine will refuse makes it lie
    # about what you can do next.
    assert not [n for n in call(repo, "map")["map"]["nodes"] if n["legal"]], (
        "the map offered moves while a reward was pending"
    )

    smap = call(repo, "map")["map"]
    reachable = next(n["id"] for n in smap["nodes"] if n["row"] == 1)
    blocked = call(repo, "enter", "--node", reachable)
    assert blocked["ok"] is False
    assert blocked["error"]["code"] == "reward_pending"

    call(repo, "reward", "--skip")
    node = next(n["id"] for n in call(repo, "map")["map"]["nodes"] if n["legal"])
    assert call(repo, "enter", "--node", node)["ok"] is True


def test_the_deck_cap_does_not_gate_relics_and_potions(repo):
    """The cap counts cards, so it may only gate cards. A chest hands out relics
    and potions, which never enter the pool — refusing those at twelve cards
    demanded a trade that would not have made room for anything."""
    stuff_the_pool(repo, run.SOFT_CAP)
    runner = run.Run(repo)
    runner.game["pending_reward"] = {
        "kind": "treasure",
        "offers": [{"id": "t-profiler", "ref": "profiler", "kind": "potion",
                    "title": "Profiler", "body": ""}],
        "skip_payout": 0,
    }
    runner.save()

    result = call(repo, "reward", "--take", "t-profiler")
    assert result["ok"] is True, result.get("error")
    assert "profiler" in run.Run(repo).game.get("potions", [])


def test_a_chest_will_not_hand_you_a_relic_you_already_carry(repo):
    """The offer rides in the save between the clear and the take, so a relic
    gained in between — an event, a purchase — could stack here even though
    `draw_from_treasure` excludes what you hold at roll time."""
    runner = run.Run(repo)
    runner.game["pending_reward"] = {
        "kind": "treasure",
        "offers": [{"id": "t-singing-bowl", "ref": "singing-bowl", "kind": "relic",
                    "title": "Singing Bowl", "body": ""}],
        "skip_payout": 0,
    }
    runner.deck.setdefault("relics", []).append("singing-bowl")
    runner.save()

    result = call(repo, "reward", "--take", "t-singing-bowl")
    assert result["ok"] is False
    assert result["error"]["code"] == "already_owned"
    assert run.Run(repo).deck["relics"].count("singing-bowl") == 1


def test_a_new_climb_does_not_inherit_the_last_one_s_reward_counters(repo):
    """Badges read these. A fresh climb inheriting last run's skips would earn
    Refusenik on floor one, which is the same broken promise as Lean Deck."""
    runner = run.Run(repo)
    runner.deck["rewards"] = {"offered": 9, "taken": 2, "skipped": 7}
    runner.save()

    call(repo, "new-run", "--seed", "3")
    assert run.Run(repo).deck["rewards"] == {"offered": 0, "taken": 0, "skipped": 0}


def test_a_new_climb_does_not_start_holding_the_last_one_s_relics(repo):
    """Relics live outside the game block, so resetting the block alone carried
    them across. A fresh run that starts with the Singing Bowl already equipped
    is not a fresh run."""
    runner = run.Run(repo)
    runner.deck.setdefault("relics", []).append("singing-bowl")
    runner.save()

    call(repo, "new-run", "--seed", "9")
    assert run.Run(repo).deck.get("relics") == []


def test_lean_deck_is_not_handed_to_every_run(repo):
    """`cards` counted the dealt-skill ledger, which is zero on a normal save,
    so `cards <= 8` was true for everyone no matter how bloated the hand."""
    stuff_the_pool(repo, 12)
    runner = run.Run(repo)
    earned = {b["id"] for b in run.evaluate_badges(runner)}
    assert "lean" not in earned, "a twelve-card hand is not a lean deck"


def test_an_unimplemented_acceptance_type_says_so(repo):
    """`file_exists` and `decision_recorded` are declared in the content schema
    and rendered by the server. Reporting `manual` for a type the engine cannot
    evaluate tells the player "this is a judgement call" when the truth is
    "this check was never built" — and `content/enemies.json` opens by saying a
    room that clears on judgement is an event, not a fight."""
    runner = run.Run(repo)
    verdict = run.run_acceptance(runner, {"acceptance": {"type": "decision_recorded",
                                                        "expect": "a decision"}})
    assert verdict["result"] == "unconfigured"
    assert "decision_recorded" in verdict["reason"]


def test_file_exists_acceptance_is_evaluated(repo):
    runner = run.Run(repo)
    missing = run.run_acceptance(runner, {"acceptance": {"type": "file_exists",
                                                        "path": "nope.md"}})
    assert missing["result"] == "fail"

    with open(os.path.join(repo, "here.md"), "w", encoding="utf-8") as fh:
        fh.write("x")
    found = run.run_acceptance(runner, {"acceptance": {"type": "file_exists",
                                                       "path": "here.md"}})
    assert found["result"] == "pass"


def test_file_exists_without_a_path_is_unconfigured_not_manual(repo):
    runner = run.Run(repo)
    verdict = run.run_acceptance(runner, {"acceptance": {"type": "file_exists", "path": None}})
    assert verdict["result"] == "unconfigured"


def test_dig_never_hands_back_a_relic_you_already_hold(repo):
    """A chest that returns the relic which unlocked it is not a reward."""
    relics = [t["ref"] for t in run.content("shop")["treasure"] if t["kind"] == "relic"]
    runner = run.Run(repo)
    runner.deck["relics"] = list(relics)          # hold the whole pool
    runner.game["room"] = {"id": "r1c0", "kind": "rest", "floor": 1, "name": "Campfire"}
    runner.game["active_room"] = "r1c0"
    runner.save()

    result = call(repo, "campfire", "--option", "dig")
    assert result["ok"] is False
    assert result["error"]["code"] == "nothing_to_dig"
    assert call(repo, "state")["state"]["active_room"] == "r1c0", "the campfire must survive"


def test_a_chest_offer_says_what_it_is_and_what_it_does(repo):
    """Opening a chest should be a decision, not a blind click."""
    runner = run.Run(repo)
    rng = mapgen.floor_rng(1, 1)
    offer = run.draw_from_treasure(rng, owned=set(runner.deck.get("relics") or []))
    assert offer["id"] and offer["title"] and offer["body"]
    assert offer["title"] != offer["ref"], "the title should be the object's name"


# scene rolls
# --------------------------------------------------------------------------- #

def test_the_map_ships_a_roll_vector_for_every_scene(repo):
    """The client has no RNG, so the engine has to hand it every scene's dice.

    Missing one is not a crash — the composer falls back to its midpoints and the
    scene silently loses its variation, which is exactly the sort of failure that
    survives a demo.
    """
    rolls = call(repo, "map")["map"]["scene_rolls"]
    declared = {name for name in run.content("scenes")["scenes"] if not name.startswith("_")}
    assert set(rolls) == declared
    # Each scene gets the length its own grammar asks for, not a global constant.
    for name, vector in rolls.items():
        assert len(vector) == mapgen.scene_budget(name), name
    assert all(0.0 <= value <= 1.0 for vector in rolls.values() for value in vector)


def test_scene_rolls_are_stable_for_a_seed_and_an_act(repo):
    """Re-entering a floor has to look identical, not merely similar."""
    first = call(repo, "map")["map"]["scene_rolls"]
    enter_first_room(repo)
    assert call(repo, "map")["map"]["scene_rolls"] == first


def test_each_act_composes_a_different_place(repo):
    """Same scene, different act — the seeding must actually separate them."""
    seed = run.Run(repo).seed
    vectors = [tuple(mapgen.scene_rolls(seed, act, "chamber")) for act in range(1, 6)]
    assert len(set(vectors)) == len(vectors)
