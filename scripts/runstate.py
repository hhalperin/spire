#!/usr/bin/env python3
"""The loaded save, and the refusal every verb raises instead of crashing.

`Run` is the one object every subcommand is handed. It owns the save (`deck`),
the run-scoped block inside it (`game`), and the derived map — plus the readings
that more than one rule needs and that used to be computed differently in each
place: how much energy a room grants, what a room's clear target really is after
curses and powers, and how big the deck is.

That last one is why this is a class rather than a bag of dicts. `hand_size` and
`owned_card_ids` reconcile two lists that look interchangeable and are not, and
every rule that measures the deck has to go through the same reconciliation or
they drift — which they did.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deck  # noqa: E402
import mapgen  # noqa: E402
from gamedata import card_by_id, content, object_by_id  # noqa: E402

# The room is a closed, solvable problem or it is not Slay the Spire. Three
# energy and a small hand is the property; the numbers themselves are cosmetic.
DEFAULT_ENERGY = 3
DEFAULT_HAND = 5

# deck.py already treats 12 as the soft cap in stats_summary; keep one number.
SOFT_CAP = 12


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
