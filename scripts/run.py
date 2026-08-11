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
import pathlib
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deck  # noqa: E402
import mapgen  # noqa: E402
import paths  # noqa: E402

CONTENT_DIR = pathlib.Path(__file__).resolve().parent.parent / "content"

# The room is a closed, solvable problem or it is not Slay the Spire. Three
# energy and a small hand is the property; the numbers themselves are cosmetic.
DEFAULT_ENERGY = 3
DEFAULT_HAND = 5

# deck.py already treats 12 as the soft cap in stats_summary; keep one number.
SOFT_CAP = 12

# The Singing Bowl port. Refusing a reward has to pay a number that goes up, or
# discipline never feels like a win (sts-fidelity.md, "a payout for refusal").
SKIP_PAYOUT = 1
SKIP_PAYOUT_WITH_BOWL = 2

ACT_ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV"}


# --------------------------------------------------------------------------- #
# content
# --------------------------------------------------------------------------- #

_CONTENT_CACHE: dict[str, dict] = {}


def content(name: str) -> dict:
    """Load and memoize one content/<name>.json."""
    if name not in _CONTENT_CACHE:
        with (CONTENT_DIR / f"{name}.json").open(encoding="utf-8") as fh:
            _CONTENT_CACHE[name] = json.load(fh)
    return _CONTENT_CACHE[name]


def card_by_id(card_id: str) -> dict | None:
    for card in content("cards")["cards"]:
        if card["id"] == card_id:
            return card
    return None


def object_by_id(bucket: str, obj_id: str) -> dict | None:
    for obj in content("objects")[bucket]:
        if obj["id"] == obj_id:
            return obj
    return None


# --------------------------------------------------------------------------- #
# errors
# --------------------------------------------------------------------------- #

class RunError(Exception):
    """A refusal the client can render. Never a crash."""

    def __init__(self, code: str, message: str, **extra: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra

    def payload(self) -> dict:
        body = {"code": self.code, "message": self.message}
        body.update(self.extra)
        return {"ok": False, "error": body}


# --------------------------------------------------------------------------- #
# run state
# --------------------------------------------------------------------------- #

class Run:
    """A loaded save plus its derived map. All writes go through deck.save."""

    def __init__(self, repo: str) -> None:
        self.repo = repo
        try:
            self.deck = deck.load(repo)
        except FileNotFoundError as exc:
            raise RunError(
                "no_deck",
                "No deck in this repo yet. Run /spire to start a climb.",
            ) from exc
        except json.JSONDecodeError as exc:
            raise RunError("bad_deck", f"deck.json is not valid JSON: {exc}") from exc

        if "game" not in self.deck or not isinstance(self.deck["game"], dict):
            # A save dealt before the client existed. Adopt it rather than
            # refusing it — content-schema.md promised the block was additive.
            self.deck["game"] = deck.game_skeleton()
        self.game = self.deck["game"]
        self.game.setdefault("ramp", dict.fromkeys(mapgen.RAMP_BASE, 0))
        self.game.setdefault("room", None)
        self.game.setdefault("pending_reward", None)

    # -- persistence -------------------------------------------------------- #

    def save(self) -> None:
        deck.save(self.repo, self.deck)

    def reload(self) -> None:
        """Re-read the save after a `deck.py` call, keeping our in-memory edits.

        `deck.clear_room`, `bump_reward` and `remove_card` each load the file,
        mutate it and write it back, so anything this module changed in memory
        beforehand is gone the moment we re-read. That cost a relic gained from
        an event: `apply_effects` appended it, `clear_room` wrote the old list
        over the top, and the reload handed back a deck without it — while the
        resolution text cheerfully said it had been gained.

        Rather than remember to re-apply the right fields at each of the six call
        sites, the two this module owns are carried across every time.
        """
        fresh = deck.load(self.repo)
        fresh["game"] = self.game
        fresh["relics"] = self.deck.get("relics") or fresh.get("relics") or []
        self.deck = fresh

    # -- map ---------------------------------------------------------------- #

    @property
    def act(self) -> int:
        return int(self.deck.get("act", 1))

    @property
    def seed(self) -> int:
        return int(self.game.get("map_seed", 0))

    def spire_map(self) -> mapgen.SpireMap:
        smap = mapgen.generate(self.seed, self.act, int(self.deck.get("ascension", 0)))
        # Replay every frozen resolution so re-entering a node can never reroll
        # it. The ramp counters ride in the save for the same reason.
        for node_id, outcome in (self.game.get("resolved") or {}).items():
            node = self._node_by_id(smap, node_id)
            if node is not None:
                smap._unknown[node.key] = {"node": node_id, "resolve": outcome}
        return smap

    def ramp(self) -> mapgen.Ramp:
        ramp = mapgen.Ramp()
        stored = self.game.get("ramp") or {}
        for kind in mapgen.RAMP_BASE:
            ramp.misses[kind] = int(stored.get(kind, 0))
        return ramp

    def store_ramp(self, ramp: mapgen.Ramp) -> None:
        self.game["ramp"] = dict(ramp.misses)

    @staticmethod
    def _node_by_id(smap: mapgen.SpireMap, node_id: str) -> mapgen.Node | None:
        for node in smap.nodes.values():
            if node.id == node_id:
                return node
        return None

    def current_node(self, smap: mapgen.SpireMap) -> mapgen.Node | None:
        cleared = self.game.get("nodes_cleared") or []
        if not cleared:
            return None
        return self._node_by_id(smap, cleared[-1])

    def resolved_kind(self, smap: mapgen.SpireMap, node: mapgen.Node) -> str:
        """The node's real kind, resolving `unknown` once and freezing it."""
        if node.kind != "unknown":
            return node.kind
        resolved = self.game.setdefault("resolved", {})
        if node.id in resolved:
            return resolved[node.id]
        ramp = self.ramp()
        outcome = mapgen.resolve_unknown(smap, node, ramp)["resolve"]
        self.store_ramp(ramp)
        resolved[node.id] = outcome
        return outcome

    # -- curses ------------------------------------------------------------- #

    def curse_effects(self) -> list[dict]:
        out = []
        for curse_id in self.game.get("curses") or []:
            curse = object_by_id("curses", curse_id)
            if curse and curse.get("effect"):
                out.append(curse["effect"])
        return out

    def energy_for(self, room_type: str) -> int:
        energy = int(self.game.get("energy_max", DEFAULT_ENERGY))
        for effect in self.curse_effects():
            if effect.get("verb") == "energy_tax" and effect.get("room") == room_type:
                energy -= int(effect.get("amount", 0))
        return max(1, energy)

    def power_effects(self) -> list[dict]:
        """Effects from power cards played this act.

        `c-harness` is typed `power`, costs 2, advances 0 progress and promised
        "bug rooms need one less progress to clear" in prose alone — no field
        declared it and nothing read it. It is the only power in the pool, so
        the entire card type did nothing: a rare reward that spent two of your
        three energy to change nothing at all.

        Cards now declare the effect in the same shape curses use, and it is
        read the same way. Powers are act-scoped, like the ledger says.
        """
        out = []
        for card_id in self.game.get("powers") or []:
            card = card_by_id(card_id)
            if card and card.get("effect"):
                out.append(card["effect"])
        return out

    def clear_at_for(self, room_type: str, base: int) -> int:
        target = base
        for effect in list(self.curse_effects()) + self.power_effects():
            if effect.get("verb") == "clear_at_tax" and effect.get("room") == room_type:
                target += int(effect.get("amount", 0))
        # A room still has to be worth entering; a stack of reductions must not
        # make one clear on arrival.
        return max(1, target)

    def has_relic(self, relic_id: str) -> bool:
        return relic_id in (self.deck.get("relics") or [])

    # -- deck view ---------------------------------------------------------- #

    def hand_size(self) -> int:
        """How big the deck is, for every rule that measures it.

        There are two card lists and they are not the same thing. `deck["cards"]`
        is the dealt-skill ledger — `.claude/skills/<name>/` directories written
        into the target repo. `game["hand_pool"]` is what you actually play, and
        it is the one rewards, the shop and the campfire all grow.

        Every size rule used to read the *ledger*. A fresh climb deals no skill
        rows, so it read zero forever: the soft cap never bound, the chrome
        printed `Deck 0/12` over a fifteen-card hand, and Lean Deck was awarded
        to everyone. The removal ladder that shop.json calls "the mechanism that
        gives the deck cap teeth" had nothing to bite.

        One accessor so the two can never drift apart again.
        """
        return len(self.owned_card_ids())

    def owned_card_ids(self) -> list[str]:
        """Deck cards mapped onto the playable pool.

        A dealt card is a `.claude/skills/<name>/SKILL.md`. Some map onto a pool
        card via `agent_skill`; the rest of the pool is what the run has picked
        up. `game.hand_pool` is the authoritative list once a climb has started.
        """
        pool = self.game.get("hand_pool")
        if isinstance(pool, list):
            # An *empty* pool is a state, not an absence: prune every card and
            # `hand_pool` is legitimately `[]`. Treating falsy as "not started"
            # rebuilt the starter deck from content, so the cards came back, the
            # hand size jumped, and the cap and badges read a deck the player
            # did not have. Only a missing key means "this climb has not begun".
            return [c for c in pool if card_by_id(c)]
        starter = list(content("cards")["starter"])
        pool_cards = content("cards")["cards"]
        by_skill = {c["agent_skill"]: c["id"] for c in pool_cards if c["agent_skill"]}
        for card in self.deck.get("cards") or []:
            mapped = by_skill.get(card.get("name"))
            if mapped and mapped not in starter:
                starter.append(mapped)
        return starter


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
                     "wares": content("shop")["wares"], "intents": []})
    elif kind == "rest":
        room.update({"name": "Campfire", "room_type": "orient",
                     "options": content("shop")["campfire"]["options"], "intents": []})
    elif kind == "treasure":
        room.update({"name": "Chest", "room_type": "orient",
                     "offer": roll_treasure(run, node), "intents": []})
    return room


def draw_from_treasure(rng: object, kind: str | None = None,
                       owned: set[str] | None = None) -> dict | None:
    """Weighted draw from the chest pool, optionally of one kind only.

    The returned offer carries an `id`. Every other reward path builds offers
    with one and `cmd_reward` matches `--take` against it, so a chest offer
    without an id could never be claimed — the take always failed
    `no_such_offer` and the next clear quietly overwrote the reward.

    Relics already held are excluded: they are unique by nature, and a chest
    that hands back the relic which unlocked it is not a reward. Potions stack,
    so they stay in. Returns None when nothing is left to give, which the caller
    has to answer for rather than papering over with a duplicate.
    """
    held = owned or set()
    table = [item for item in content("shop")["treasure"]
             if (kind is None or item["kind"] == kind)
             and not (item["kind"] == "relic" and item["ref"] in held)]
    if not table:
        return None
    total = sum(item["weight"] for item in table)
    roll = rng.uniform(0, total)  # type: ignore[attr-defined]
    upto = 0.0
    chosen = table[0]
    for item in table:
        upto += item["weight"]
        if roll <= upto:
            chosen = item
            break
    offer = dict(chosen)
    offer["id"] = f"t-{chosen['ref']}"
    # Carry the name and what the thing actually does. A chest that shows only
    # a pool ref makes opening it a blind click, which is the opposite of the
    # informed decision every other reward screen is built around.
    detail = object_by_id("relics" if chosen["kind"] == "relic" else "potions", chosen["ref"]) or {}
    offer["title"] = detail.get("name", chosen["ref"])
    offer["body"] = detail.get("rule") or detail.get("spent_on") or ""
    return offer


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


# --------------------------------------------------------------------------- #
# acceptance — the deterministic sensor
# --------------------------------------------------------------------------- #

def resolve_command(repo: str, symbol: str) -> str | None:
    """Map a symbolic acceptance command onto this repo's real one.

    Content never carries a shell string. `/spire:ascend` already wrote the
    repo's lint and test commands into .spire/ascension.json after resolving
    them from the class YAML, so that file is the allowlist — which satisfies
    mcp-client.md's rule that acceptance commands equal the class `commands.*`
    or are user-confirmed, without this module ever parsing YAML.
    """
    config_path = paths.ascension_path(repo)
    if not os.path.exists(config_path):
        return None
    try:
        with open(config_path, encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    value = config.get(f"{symbol}_cmd")
    return value if isinstance(value, str) and value.strip() else None


def run_acceptance(run: Run, room: dict) -> dict:
    """Execute the room's acceptance check and report honestly.

    "Honestly" is doing work here. `content/enemies.json` opens by saying every
    entry needs a checkable acceptance, and that a room clearing on human
    judgement is an event rather than a fight. So a type this function cannot
    evaluate must say *that*, not `manual` — reporting a judgement call for a
    check that was simply never built tells the player the room is one kind of
    thing when it is another, which is the same dishonesty the sensor rule on
    intents exists to prevent.
    """
    acceptance = room.get("acceptance") or {}
    kind = acceptance.get("type")

    if kind == "file_exists":
        path = acceptance.get("path")
        if not isinstance(path, str) or not path.strip():
            return {"result": "unconfigured", "detail": acceptance.get("expect", ""),
                    "reason": "This gate checks for a file but names no path."}
        target = os.path.normpath(os.path.join(run.repo, path))
        found = os.path.exists(target)
        return {"result": "pass" if found else "fail", "path": path,
                "detail": acceptance.get("expect", ""),
                "reason": f"{path} {'exists' if found else 'is not there'}."}

    if kind != "command":
        if kind:
            return {"result": "unconfigured", "detail": acceptance.get("expect", ""),
                    "reason": f"This gate declares {kind}, which the engine does not evaluate yet."}
        return {"result": "manual", "detail": acceptance.get("expect", ""),
                "reason": "This room clears on a judgement call, not a command."}

    command = resolve_command(run.repo, acceptance.get("cmd", ""))
    if command is None:
        return {"result": "unconfigured", "detail": acceptance.get("cmd", ""),
                "reason": "No command configured for this gate. Run /spire:ascend to bind one."}

    repeats = int(acceptance.get("repeat", 1))
    tail = ""
    for _ in range(max(1, repeats)):
        try:
            proc = subprocess.run(
                command, shell=True, cwd=run.repo, capture_output=True,
                text=True, timeout=300,
            )
        except subprocess.TimeoutExpired:
            return {"result": "fail", "command": command, "reason": "timed out after 300s"}
        except OSError as exc:
            return {"result": "fail", "command": command, "reason": str(exc)}
        tail = ((proc.stdout or "") + (proc.stderr or ""))[-1500:]
        if proc.returncode != 0:
            return {"result": "fail", "command": command, "exit_code": proc.returncode,
                    "log": tail}
    return {"result": "pass", "command": command, "exit_code": 0, "log": tail}


# --------------------------------------------------------------------------- #
# events
# --------------------------------------------------------------------------- #

def choice_is_available(run: Run, choice: dict) -> str | None:
    """Why this choice cannot be taken, or None if it can.

    Event gates are load-bearing: being poor or already carrying a card changes
    which choices exist for you, which is what stops an event being a free menu.
    """
    requires = choice.get("requires") or {}
    if "card" in requires and requires["card"] not in run.owned_card_ids():
        card = card_by_id(requires["card"])
        return f"needs the {card['title'] if card else requires['card']} card"
    if "focus" in requires and int(run.game.get("focus", 0)) < int(requires["focus"]):
        return f"needs ◈{requires['focus']} focus"
    return None


def apply_effects(run: Run, effects: list[dict]) -> list[str]:
    """Apply one choice's effects and describe what happened, in order.

    Every verb here is one `content-schema.md` names. An effect that names an
    object that does not exist is skipped rather than crashing the room — but
    `tests/test_run.py` refuses to let such an effect ship in the first place.
    """
    log: list[str] = []
    for effect in effects or []:
        verb = effect.get("verb")

        if verb == "add_curse":
            curse = object_by_id("curses", effect.get("id", ""))
            if curse and curse["id"] not in (run.game.get("curses") or []):
                run.game.setdefault("curses", []).append(curse["id"])
                log.append(f"Gained the {curse['name']} curse. {curse['cost']}")

        elif verb == "remove_curse":
            carried = run.game.get("curses") or []
            target = effect.get("id")
            drop = carried[0] if target == "any" and carried else (
                target if target in carried else None)
            if drop:
                carried.remove(drop)
                removed = object_by_id("curses", drop)
                log.append(f"Shed the {removed['name'] if removed else drop} curse.")

        elif verb == "gain_relic":
            relic = object_by_id("relics", effect.get("id", ""))
            if relic and relic["id"] not in (run.deck.get("relics") or []):
                run.deck.setdefault("relics", []).append(relic["id"])
                log.append(f"Gained the {relic['name']} relic.")

        elif verb == "gain_card":
            pool = run.game.setdefault("hand_pool", run.owned_card_ids())
            rarity = effect.get("rarity")
            rng = mapgen.floor_rng(mapgen.act_seed(run.seed, run.act) + 4,
                                   int(run.deck.get("floor", 0)))
            options = [c for c in content("cards")["cards"]
                       if c["id"] not in pool and (not rarity or c["rarity"] == rarity)]
            # Rewards, the shop and trades all refuse past the cap; an event
            # that quietly pushed you over was the last way around it. There is
            # nobody to prompt for a trade mid-effect, so the gift is declined
            # and said so rather than silently dropped.
            if len(pool) >= SOFT_CAP:
                log.append(f"No room for another card (deck is at {SOFT_CAP}).")
            elif options:
                pick = options[rng.randrange(len(options))]
                pool.append(pick["id"])
                log.append(f"Gained {pick['title']}.")

        elif verb == "lose_card":
            pool = run.game.setdefault("hand_pool", run.owned_card_ids())
            if effect.get("id") in pool:
                pool.remove(effect["id"])
                log.append(f"Lost {effect['id']}.")

        elif verb == "gain_focus":
            amount = int(effect.get("amount", 0))
            run.game["focus"] = int(run.game.get("focus", 0)) + amount
            log.append(f"Gained ◈{amount} focus.")

        elif verb == "spend_focus":
            amount = int(effect.get("amount", 0))
            run.game["focus"] = max(0, int(run.game.get("focus", 0)) - amount)
            log.append(f"Spent ◈{amount} focus.")

        elif verb == "require_card":
            # "Spent" has to mean spent. This only wrote the text, so a choice
            # gated on owning a card could be taken again and again with the
            # card still in hand — the one event verb whose message and effect
            # disagreed.
            card = card_by_id(effect.get("id", ""))
            pool = run.game.setdefault("hand_pool", run.owned_card_ids())
            if effect.get("id") in pool:
                pool.remove(effect["id"])
            log.append(f"Spent {card['title'] if card else effect.get('id')}.")

        elif verb == "bump_prior":
            priors = run.game.setdefault("prior_bump", {})
            room = effect.get("room", "")
            priors[room] = round(priors.get(room, 0.0) + float(effect.get("delta", 0)), 4)
            direction = "rises" if float(effect.get("delta", 0)) > 0 else "falls"
            log.append(f"{room.capitalize()} pressure {direction} for the act.")

        elif verb == "log_room":
            log.append("Logged and left.")

    return log


# --------------------------------------------------------------------------- #
# rewards
# --------------------------------------------------------------------------- #

def roll_offers(run: Run, kind: str, floor: int) -> list[dict]:
    """Up to three cards, never duplicates, each rarity rolled independently.

    Two things stop a boss quietly handing out commons.

    The pity offset only applies where the roll is actually uncertain. The boss
    table sets `rare: 1.0`, and subtracting the offset from a certainty made a
    5% gap against `random()`'s [0, 1) — so 47 of 400 seeds produced a non-rare
    boss offer from a table that says every offer is rare.

    And a guaranteed rarity is honoured or the offer is not made. The fallback
    to "any rarity" exists so a fight always offers *something*, but applying it
    to a guarantee turned "three rares" into "two rares and whatever is left"
    on every seed, because the pool holds fewer rares than a boss offers.
    Offering two is honest; offering a common labelled as a boss reward is not.
    """
    rates = content("shop")["reward_rates"]
    table = rates.get(kind, rates["normal"])
    offset = float(run.game.get("rare_offset", rates["offset_start"]))
    rng = mapgen.floor_rng(mapgen.act_seed(run.seed, run.act) + 3, floor)

    pool = content("cards")["cards"]
    owned = set(run.owned_card_ids())
    offers: list[dict] = []
    taken: set[str] = set()
    guaranteed = table["rare"] >= 1.0

    for _ in range(3):
        roll = rng.random()
        if guaranteed or roll < max(0.0, table["rare"] + offset):
            rarity = "rare"
            offset = rates["offset_start"]
        elif roll < table["rare"] + table["uncommon"]:
            rarity = "uncommon"
        else:
            rarity = "common"
            offset += rates["offset_step"]
        candidates = [c for c in pool
                      if c["rarity"] == rarity and c["id"] not in owned and c["id"] not in taken]
        if not candidates and not guaranteed:
            candidates = [c for c in pool if c["id"] not in owned and c["id"] not in taken]
        if not candidates:
            break
        pick = candidates[rng.randrange(len(candidates))]
        taken.add(pick["id"])
        offers.append({"id": pick["id"], "title": pick["title"], "rarity": pick["rarity"],
                       "cost": pick["cost"], "type": pick["type"], "body": pick["body"],
                       "rooms": pick["rooms"]})

    run.game["rare_offset"] = offset
    return offers


def remove_from_deck(run: Run, name: str) -> bool:
    """Take one card out of the run, whichever list it lives in.

    Prune and trade are the same operation — "remove a card I hold" — and were
    written twice, against different lists. Prune reached the pool and the
    ledger; trade reached only the ledger. Once the soft cap started counting
    the pool (as it always should have), that asymmetry became a dead end: at
    the cap the only way to take a card is to trade one away, and every card a
    normal player holds lives in the pool, so every trade they could name was
    refused. One helper, both call sites, so they cannot drift again.

    Returns False when the name is in neither list, which both callers turn into
    a refusal rather than a rest site or a reward spent on nothing.
    """
    pool = run.game.setdefault("hand_pool", run.owned_card_ids())
    if name in pool:
        pool.remove(name)
        return True
    if deck.remove_card(run.repo, name):
        # A dealt skill maps onto a pool card via `agent_skill`; dropping the
        # directory without dropping that id left the card in hand, still drawn.
        mapped = next((c["id"] for c in content("cards")["cards"]
                       if c.get("agent_skill") == name), None)
        run.reload()
        pool = run.game.setdefault("hand_pool", run.owned_card_ids())
        if mapped and mapped in pool:
            pool.remove(mapped)
        return True
    return False


def skip_payout(run: Run) -> int:
    return SKIP_PAYOUT_WITH_BOWL if run.has_relic("singing-bowl") else SKIP_PAYOUT


# --------------------------------------------------------------------------- #
# badges
# --------------------------------------------------------------------------- #

def evaluate_badges(run: Run) -> list[dict]:
    """Slay the Spire 2's end-of-run badges, pointed at refusal.

    Each test is a pure read of the save, so a badge can never be granted for
    anything the player did not actually do.
    """
    rewards = run.deck.get("rewards") or {}
    facts = {
        "skipped": int(rewards.get("skipped", 0)),
        "taken": int(rewards.get("taken", 0)),
        # Lean Deck asks how small the hand you play is, so it counts the pool.
        # Counting the dealt-skill ledger read zero on a normal save and handed
        # the badge to every run regardless of how bloated the deck got.
        "cards": run.hand_size(),
        "elites": len([n for n in (run.game.get("elites_cleared") or [])]),
        "streak": int(run.deck.get("clean_room_streak", 0)),
        "annotations": len(run.game.get("annotations") or {}),
        "curses": len(run.game.get("curses") or []),
        "ascension": int(run.deck.get("ascension", 0)),
    }
    earned = []
    for badge in content("objects")["badges"]["definitions"]:
        field, op, raw = badge["test"].split()
        left = facts.get(field, 0)
        right = int(raw)
        hit = (left >= right) if op == ">=" else (left <= right) if op == "<=" else (left == right)
        if hit:
            earned.append({
                "id": badge["id"],
                "name": badge["name"],
                "blurb": badge["blurb"].format(**facts),
            })
    return earned


# --------------------------------------------------------------------------- #
# serialization
# --------------------------------------------------------------------------- #

def act_label(act: int) -> str:
    if act in ACT_ROMAN:
        return f"Act {ACT_ROMAN[act]}"
    return f"Act {act} · endless"


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
    }


def serialize_state(run: Run) -> dict:
    cards = run.deck.get("cards") or []
    room = run.game.get("room")
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
        "deck_size": run.hand_size(),
        "soft_cap": SOFT_CAP,
        # ">=", not ">": the gate refuses at exactly SOFT_CAP, so a flag meaning
        # "the cap binds now" has to agree with it. At 12/12 the engine refused
        # the take while the client stayed silent, because the two used
        # different comparisons for the same question.
        "over_soft_cap": run.hand_size() >= SOFT_CAP,
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
            for card in (card_by_id(cid) for cid in run.owned_card_ids())
            if card
        ],
    }


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

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="spire's headless run loop (JSON in/out).")
    parser.add_argument("--path", default=".", help="target repo root (default: .)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("state", help="the whole run")
    sub.add_parser("map", help="nodes, legal moves, annotations")
    sub.add_parser("hand", help="legal cards for the active room")
    sub.add_parser("end-turn", help="refill energy, apply the turn effect")
    sub.add_parser("acceptance", help="run the room's deterministic check")
    sub.add_parser("badges", help="evaluate end-of-act badges")

    p_enter = sub.add_parser("enter", help="open a room")
    p_enter.add_argument("--node", required=True)

    p_play = sub.add_parser("play", help="play a card")
    p_play.add_argument("--card", required=True)

    p_clear = sub.add_parser("clear", help="finish the room")
    p_clear.add_argument("--force", action="store_true", help="clear without meeting the target")
    p_clear.add_argument("--choice", help="event choice id (required in an event room)")

    p_flee = sub.add_parser("flee", help="abandon the room")
    p_flee.add_argument("--no-notes", action="store_true",
                        help="flee without notes; gain Hesitation")

    p_reward = sub.add_parser("reward", help="resolve a pending offer")
    p_reward.add_argument("--take")
    p_reward.add_argument("--skip", action="store_true")
    p_reward.add_argument("--trade", help="card to remove when at the soft cap")

    p_camp = sub.add_parser("campfire", help="smith / prune / dig")
    p_camp.add_argument("--option", required=True, choices=["smith", "prune", "dig"])
    p_camp.add_argument("--card")

    p_shop = sub.add_parser("shop", help="list or buy")
    p_shop.add_argument("--buy")

    p_note = sub.add_parser("annotate", help="mark a map node")
    p_note.add_argument("--node", required=True)
    p_note.add_argument("--mark", help="a short marker, or 'clear' to remove")

    p_new = sub.add_parser("new-run", help="start or restart a climb")
    p_new.add_argument("--seed", type=int, default=0)

    return parser


HANDLERS = {
    "state": cmd_state,
    "map": cmd_map,
    "enter": cmd_enter,
    "hand": cmd_hand,
    "play": cmd_play,
    "end-turn": cmd_end_turn,
    "acceptance": cmd_acceptance,
    "clear": cmd_clear,
    "flee": cmd_flee,
    "reward": cmd_reward,
    "campfire": cmd_campfire,
    "shop": cmd_shop,
    "annotate": cmd_annotate,
    "badges": cmd_badges,
    "new-run": cmd_new_run,
}


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
