#!/usr/bin/env python3
"""Event choices: which ones you may take, and what taking one does.

An event is the one room that clears by deciding rather than by playing, so the
whole room lives in these two functions — the gate that makes a choice cost
something you might not have, and the verbs that spend it.

Every verb here is one `content-schema.md` names, and each is applied against
the save rather than described. That distinction is load-bearing: `require_card`
shipped writing only its log line, so a choice gated on holding a card could be
taken forever with the card still in hand.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mapgen  # noqa: E402
from gamedata import card_by_id, content, object_by_id  # noqa: E402
from runstate import SOFT_CAP, Run  # noqa: E402


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


# --------------------------------------------------------------------------- #
# effect verbs
# --------------------------------------------------------------------------- #
#
# One function per verb, each returning the line the player is shown — or None
# where nothing happened, because "you already carry that relic" should not
# announce a gift that was not given.
#
# These were an eighty-line if/elif chain, which made the set of implemented
# verbs something only a reader could enumerate. `tests/test_run.py` therefore
# checked content against a *hand-written* roster of ten, and so would have
# stayed green if a verb here were renamed while content still used the old
# name — the engine silently doing nothing while the event text said otherwise.
# EFFECTS below is that roster, and the test now reads it from here.


def _add_curse(run: Run, effect: dict) -> str | None:
    curse = object_by_id("curses", effect.get("id", ""))
    if curse and curse["id"] not in (run.game.get("curses") or []):
        run.game.setdefault("curses", []).append(curse["id"])
        return f"Gained the {curse['name']} curse. {curse['cost']}"
    return None


def _remove_curse(run: Run, effect: dict) -> str | None:
    carried = run.game.get("curses") or []
    target = effect.get("id")
    drop = carried[0] if target == "any" and carried else (
        target if target in carried else None)
    if not drop:
        return None
    carried.remove(drop)
    removed = object_by_id("curses", drop)
    return f"Shed the {removed['name'] if removed else drop} curse."


def _gain_relic(run: Run, effect: dict) -> str | None:
    relic = object_by_id("relics", effect.get("id", ""))
    if relic and relic["id"] not in (run.deck.get("relics") or []):
        run.deck.setdefault("relics", []).append(relic["id"])
        return f"Gained the {relic['name']} relic."
    return None


def _gain_card(run: Run, effect: dict) -> str | None:
    pool = run.game.setdefault("hand_pool", run.owned_card_ids())
    rarity = effect.get("rarity")
    rng = mapgen.floor_rng(mapgen.act_seed(run.seed, run.act) + 4,
                           int(run.deck.get("floor", 0)))
    options = [c for c in content("cards")["cards"]
               if c["id"] not in pool and (not rarity or c["rarity"] == rarity)]
    # Rewards, the shop and trades all refuse past the cap; an event that
    # quietly pushed you over was the last way around it. There is nobody to
    # prompt for a trade mid-effect, so the gift is declined and said so rather
    # than silently dropped.
    if len(pool) >= SOFT_CAP:
        return f"No room for another card (deck is at {SOFT_CAP})."
    if not options:
        return None
    pick = options[rng.randrange(len(options))]
    pool.append(pick["id"])
    return f"Gained {pick['title']}."


def _lose_card(run: Run, effect: dict) -> str | None:
    pool = run.game.setdefault("hand_pool", run.owned_card_ids())
    if effect.get("id") in pool:
        pool.remove(effect["id"])
        return f"Lost {effect['id']}."
    return None


def _gain_focus(run: Run, effect: dict) -> str | None:
    amount = int(effect.get("amount", 0))
    run.game["focus"] = int(run.game.get("focus", 0)) + amount
    return f"Gained ◈{amount} focus."


def _spend_focus(run: Run, effect: dict) -> str | None:
    amount = int(effect.get("amount", 0))
    run.game["focus"] = max(0, int(run.game.get("focus", 0)) - amount)
    return f"Spent ◈{amount} focus."


def _require_card(run: Run, effect: dict) -> str | None:
    # "Spent" has to mean spent. This only wrote the text, so a choice gated on
    # owning a card could be taken again and again with the card still in hand
    # — the one event verb whose message and effect disagreed.
    card = card_by_id(effect.get("id", ""))
    pool = run.game.setdefault("hand_pool", run.owned_card_ids())
    if effect.get("id") in pool:
        pool.remove(effect["id"])
    return f"Spent {card['title'] if card else effect.get('id')}."


def _bump_prior(run: Run, effect: dict) -> str | None:
    priors = run.game.setdefault("prior_bump", {})
    room = effect.get("room", "")
    delta = float(effect.get("delta", 0))
    priors[room] = round(priors.get(room, 0.0) + delta, 4)
    return f"{room.capitalize()} pressure {'rises' if delta > 0 else 'falls'} for the act."


def _log_room(run: Run, effect: dict) -> str | None:
    return "Logged and left."


EFFECTS = {
    "add_curse": _add_curse,
    "remove_curse": _remove_curse,
    "gain_relic": _gain_relic,
    "gain_card": _gain_card,
    "lose_card": _lose_card,
    "gain_focus": _gain_focus,
    "spend_focus": _spend_focus,
    "require_card": _require_card,
    "bump_prior": _bump_prior,
    "log_room": _log_room,
}


def apply_effects(run: Run, effects: list[dict]) -> list[str]:
    """Apply one choice's effects and describe what happened, in order.

    Every verb here is one `content-schema.md` names. An effect that names an
    object that does not exist is skipped rather than crashing the room — but
    `tests/test_run.py` refuses to let such an effect ship in the first place.
    """
    log: list[str] = []
    for effect in effects or []:
        handler = EFFECTS.get(effect.get("verb", ""))
        if handler is None:
            continue
        said = handler(run, effect)
        if said:
            log.append(said)
    return log
