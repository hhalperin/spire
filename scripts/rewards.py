#!/usr/bin/env python3
"""What a run hands you, what it takes back, and what it says you earned.

Card offers after a fight, the weighted chest draw, the payout for refusing an
offer, the removal that both the campfire and a trade go through, and the
end-of-act badges. They sit together because they are the same conversation from
four directions — the deck grows here, shrinks here, and is measured here — and
because the soft cap has to mean the same thing in all of them.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deck  # noqa: E402
import mapgen  # noqa: E402
from gamedata import content, object_by_id  # noqa: E402
from runstate import Run  # noqa: E402

# The Singing Bowl port. Refusing a reward has to pay a number that goes up, or
# discipline never feels like a win (sts-fidelity.md, "a payout for refusal").
SKIP_PAYOUT = 1
SKIP_PAYOUT_WITH_BOWL = 2


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
