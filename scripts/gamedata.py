#!/usr/bin/env python3
"""The content pools, loaded once and looked up by id.

`content/*.json` is the run's whole vocabulary — cards, enemies, events, relics,
potions, curses, shop stock, badges. Every other run-loop module reads it, and
nothing here reads them back, which is why it is the bottom of the import graph
and why it holds no game rules at all.

The cache is module-level and deliberate. A single `run.py` invocation answers
one verb and exits, but that verb may touch `cards` a dozen times through
`card_by_id`; re-reading the file each time would be the only I/O in an
otherwise pure path.
"""

from __future__ import annotations

import json
import pathlib

CONTENT_DIR = pathlib.Path(__file__).resolve().parent.parent / "content"

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
