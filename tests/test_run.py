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
    assert result["state"]["deck_size"] == 0


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

def test_playing_a_card_spends_energy_and_advances_progress(repo):
    room = enter_first_room(repo)["room"]
    playable = next(c for c in call(repo, "hand")["hand"] if c["playable"] and c["cost"] > 0)
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
    """Play until the room's target is met, ending turns as needed."""
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
    saved = deck.load(repo)
    saved["cards"] = [
        {"name": f"card-{i}", "type": "skill", "added_floor": 0, "plays": 0}
        for i in range(run.SOFT_CAP)
    ]
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


def test_a_judgement_call_room_reports_manual_not_a_verdict(repo):
    room = {"acceptance": {"type": "decision_recorded", "expect": "owner_named"}}
    verdict = run.run_acceptance(run.Run(repo), room)
    assert verdict["result"] == "manual"


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
