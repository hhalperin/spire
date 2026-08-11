#!/usr/bin/env python3
"""Deterministic map generation in the shape of Slay the Spire's act maps.

Pure standard library, like every other script here. A map is a function of
(seed, act, ascension), so a run is reproducible and a reviewer can re-derive
any map from its seed.

The rules implemented here are documented in design/spire-ai/sts-fidelity.md.
The short version: walk paths up a grid, then deal room kinds from a shuffled
fixed-quota bag subject to a row rule, a parent rule and a sibling rule, with
monster as the fallback when nothing in the bag is legal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import random
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from typing import Any

ROWS = 15
COLS = 7
PATHS = 6

# Forced floors, zero-indexed. StS forces floor 1 to monster, floor 9 to
# treasure and floor 15 to rest. The boss sits one row above the last.
TREASURE_ROW = 8
BOSS_ROW = ROWS
BOSS_COL = COLS // 2

# Placement constraints.
NO_REST_ELITE_BEFORE_ROW = 5

KINDS = ("monster", "elite", "rest", "shop", "treasure", "unknown", "boss")

# Kinds that may not repeat along an edge, and kinds that may not repeat
# between siblings. Monster is exempt from both because it is the fallback an
# over-constrained node lands on.
PARENT_UNIQUE_KINDS = ("rest", "shop", "elite")
SIBLING_UNIQUE_KINDS = ("rest", "shop", "elite", "unknown")

QUOTAS = {"rest": 0.12, "elite": 0.08, "shop": 0.05, "unknown": 0.22}
ELITE_ASCENSION_MULTIPLIER = 1.6

# Endless: past the Heart the climb keeps going and elites keep thickening.
HEART_ACT = 4
ENDLESS_ELITE_STEP = 0.12
ENDLESS_ELITE_CAP = 2.5

BOSSES_PATH = pathlib.Path(__file__).resolve().parent.parent / "content" / "bosses.json"

# (row, col) -> set of columns on the next row.
EdgeMap = dict["tuple[int, int]", "set[int]"]

# How much the demo ships. One source of truth for the generator and the
# staleness test, so they cannot check different things.
EMIT_SEEDS = 3
EMIT_ACTS = 8

# Unknown-node resolution. Checked in this order; each base climbs by its own
# value every time it fails to fire, and resets when it fires.
RAMP_ORDER = ("monster", "shop", "treasure")
RAMP_BASE = {"monster": 0.10, "shop": 0.03, "treasure": 0.02}

def act_seed(seed: int, act: int) -> int:
    """Derive the per-act seed.

    Hashed rather than added. Adding a per-act offset collides across acts:
    with offsets 1 and 200, seed 199 act 1 produced a graph identical to
    seed 0 act 2, which quietly breaks the promise that a seed and an act
    name one map.
    """
    if act < 1:
        raise ValueError(f"act must be >= 1, got {act}")
    digest = hashlib.sha256(f"{seed}:{act}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def load_bosses() -> dict:
    with BOSSES_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def boss_pool(bosses: dict, act: int) -> list[dict]:
    """The act boss is visible from floor 1, which is what makes an act a plan."""
    return bosses.get(str(act)) or bosses["endless"]


def elite_multiplier(act: int, ascension: int) -> float:
    mult = ELITE_ASCENSION_MULTIPLIER if ascension >= 1 else 1.0
    if act > HEART_ACT:
        mult *= min(ENDLESS_ELITE_CAP, 1.0 + ENDLESS_ELITE_STEP * (act - HEART_ACT))
    return mult


# The background composer's roll budget. content/scenes.json states the same
# number and tests/test_scenes.py holds the two together, because a client that
# expects more rolls than the engine ships would silently compose half a scene.
SCENE_ROLLS = 96


def scene_seed(seed: int, act: int, scene: str) -> int:
    """Derive the per-scene seed.

    Hashed, for exactly the reason `act_seed` is hashed: adding a scene offset
    would let `(seed, act, scene_a)` collide with `(seed, act + k, scene_b)` and
    two different places would quietly compose the same picture. A scene name is
    a string anyway, so there is nothing to add.
    """
    digest = hashlib.sha256(f"scene:{seed}:{act}:{scene}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def scene_rolls(seed: int, act: int, scene: str, count: int = SCENE_ROLLS) -> list[float]:
    """The float vector the background composer walks.

    Same contract as `unknown_rolls`: the client has no RNG, so the engine
    pre-rolls a fixed-length vector and the client applies the grammar in
    content/scenes.json to it. Fixed length matters — a variable-length vector
    would make the composer's fixed-slice cursor meaningless.
    """
    rng = random.Random(scene_seed(seed, act, scene))
    return [rng.random() for _ in range(count)]


def floor_rng(seed: int, floor: int) -> random.Random:
    """Per-floor isolated RNG.

    StS re-seeds several streams to `seed + floor` on entering a room, so that
    what you did on floor 7 cannot perturb floor 8. That isolation is what lets
    a player reason precisely and a run stay reproducible.
    """
    return random.Random(seed + floor)


@dataclass(frozen=True)
class Node:
    row: int
    col: int
    kind: str = "monster"
    next_cols: tuple[int, ...] = ()

    @property
    def key(self) -> tuple[int, int]:
        return (self.row, self.col)

    @property
    def id(self) -> str:
        return f"r{self.row}c{self.col}"

    def replace(self, **changes: Any) -> Node:
        return dc_replace(self, **changes)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "row": self.row,
            "col": self.col,
            "kind": self.kind,
            "next": list(self.next_cols),
        }


@dataclass
class SpireMap:
    seed: int
    act: int
    ascension: int
    nodes: dict[tuple[int, int], Node]
    boss: dict
    rows: int = ROWS
    cols: int = COLS
    _unknown: dict[tuple[int, int], dict] = field(default_factory=dict, repr=False)

    def row_nodes(self, row: int) -> list[Node]:
        return [self.nodes[k] for k in sorted(self.nodes) if k[0] == row]

    def next_nodes(self, node: Node) -> list[Node]:
        # Skips edges pointing at nothing rather than raising, so
        # check_invariants can report a malformed map instead of crashing on it.
        out = []
        for col in node.next_cols:
            child = self.nodes.get((node.row + 1, col))
            if child is not None:
                out.append(child)
        return out

    def parents(self, node: Node) -> list[Node]:
        if node.row == 0:
            return []
        return [n for n in self.row_nodes(node.row - 1) if node.col in n.next_cols]

    def siblings(self, node: Node) -> list[Node]:
        out: dict[tuple[int, int], Node] = {}
        for parent in self.parents(node):
            for child in self.next_nodes(parent):
                if child.key != node.key:
                    out[child.key] = child
        return [out[k] for k in sorted(out)]

    def unknown_rolls(self, node: Node) -> list[float]:
        """The rolls resolve_unknown will consume for this node, in order.

        Exported so a client can resolve an unknown node itself without
        reimplementing the RNG. Only the ramp thresholds are shared.
        """
        rng = floor_rng(act_seed(self.seed, self.act), node.row)
        return [rng.random() for _ in RAMP_ORDER]

    def to_dict(self) -> dict:
        nodes = []
        for key in sorted(self.nodes):
            node = self.nodes[key]
            payload = node.to_dict()
            if node.kind == "unknown":
                payload["rolls"] = self.unknown_rolls(node)
            nodes.append(payload)
        return {
            "seed": self.seed,
            "act": self.act,
            "ascension": self.ascension,
            "rows": self.rows,
            "cols": self.cols,
            "boss": dict(self.boss),
            "nodes": nodes,
        }

    def fingerprint(self) -> str:
        blob = json.dumps(self.to_dict(), sort_keys=True).encode()
        return hashlib.sha1(blob).hexdigest()


class Ramp:
    """Per-act miss counters for unknown-node resolution."""

    def __init__(self) -> None:
        self.misses = dict.fromkeys(RAMP_BASE, 0)

    def chance(self, kind: str) -> float:
        return RAMP_BASE[kind] * (self.misses[kind] + 1)

    def miss(self, kind: str) -> None:
        self.misses[kind] += 1

    def fire(self, kind: str) -> None:
        self.misses[kind] = 0


# ---------------------------------------------------------------------------
# path walking
# ---------------------------------------------------------------------------


def _crosses(edges: dict[tuple[int, int], set[int]], row: int, col: int, target: int) -> bool:
    for (r, c), targets in edges.items():
        if r != row or c == col:
            continue
        for t in targets:
            if (col < c and target > t) or (col > c and target < t):
                return True
    return False


def _siblings_of(
    edges: dict[tuple[int, int], set[int]],
    parents: dict[tuple[int, int], set[int]],
    row: int,
    col: int,
) -> set[int]:
    """Columns on `row` that share a parent with (row, col)."""
    out: set[int] = set()
    for pcol in parents.get((row, col), ()):
        out |= edges.get((row - 1, pcol), set())
    out.discard(col)
    return out


def _rejoins_immediately(
    edges: dict[tuple[int, int], set[int]],
    parents: dict[tuple[int, int], set[int]],
    row: int,
    col: int,
    target: int,
) -> bool:
    """Would this edge let two branches that just split rejoin one floor later?"""
    for sib in _siblings_of(edges, parents, row, col):
        if target in edges.get((row, sib), set()):
            return True
    return False


WALK_ATTEMPTS = 24
GENERATE_ATTEMPTS = 12


def _count_rejoins(edges: dict[tuple[int, int], set[int]]) -> int:
    total = 0
    for (row, _col), children in edges.items():
        if row + 2 >= BOSS_ROW:
            continue
        ordered = sorted(children)
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                a = edges.get((row + 1, ordered[i]), set())
                b = edges.get((row + 1, ordered[j]), set())
                total += len(a & b)
    return total


def _copy(state: dict[tuple[int, int], set[int]]) -> dict[tuple[int, int], set[int]]:
    return {k: set(v) for k, v in state.items()}


def _walk_one(
    rng: random.Random,
    edges: dict[tuple[int, int], set[int]],
    parents: dict[tuple[int, int], set[int]],
    start: int,
) -> None:
    col = start
    for row in range(ROWS - 1):
        options = [c for c in (col - 1, col, col + 1) if 0 <= c < COLS]
        rng.shuffle(options)
        legal = [c for c in options if not _crosses(edges, row, col, c)]
        preferred = [c for c in legal if not _rejoins_immediately(edges, parents, row, col, c)]
        target = (preferred or legal or [col])[0]
        edges.setdefault((row, col), set()).add(target)
        parents.setdefault((row + 1, target), set()).add(col)
        col = target


def _walk_paths(rng: random.Random) -> dict[tuple[int, int], set[int]]:
    """Walk PATHS routes up the grid, retrying any walk that adds a rejoin.

    Preferring non-rejoining steps is not enough on a 7-wide grid: a path can
    reach a node where all three steps collide. Retrying the whole path is
    cheap and removes almost every violation at the source, which beats
    patching the graph afterwards.
    """
    edges: dict[tuple[int, int], set[int]] = {}
    parents: dict[tuple[int, int], set[int]] = {}
    first_start: int | None = None

    for path in range(PATHS):
        before = _count_rejoins(edges)
        best: tuple[int, EdgeMap, EdgeMap, int] | None = None

        for _ in range(WALK_ATTEMPTS):
            trial_edges, trial_parents = _copy(edges), _copy(parents)
            col = rng.randrange(COLS)
            if path == 1 and col == first_start:
                # Guarantee at least two entrances, as StS does for path two.
                col = (col + 1 + rng.randrange(COLS - 1)) % COLS
            _walk_one(rng, trial_edges, trial_parents, col)
            added = _count_rejoins(trial_edges) - before
            if best is None or added < best[0]:
                best = (added, trial_edges, trial_parents, col)
            if added == 0:
                break

        assert best is not None
        _, edges, parents, chosen = best
        if path == 0:
            first_start = chosen

    _repair_rejoins(edges, parents)
    return edges


def _drop_edge(
    edges: dict[tuple[int, int], set[int]],
    parents: dict[tuple[int, int], set[int]],
    row: int,
    col: int,
    target: int,
) -> bool:
    """Remove an edge if the source keeps a child and the target keeps a parent."""
    if len(edges.get((row, col), set())) <= 1:
        return False
    if len(parents.get((row + 1, target), set())) <= 1:
        return False
    edges[(row, col)].discard(target)
    parents[(row + 1, target)].discard(col)
    return True


def _try_add_child(
    edges: dict[tuple[int, int], set[int]],
    parents: dict[tuple[int, int], set[int]],
    keys: set[tuple[int, int]],
    row: int,
    col: int,
    avoid: int,
) -> bool:
    """Give a node a second child so an offending edge becomes safe to drop."""
    for target in (col - 1, col + 1):
        if target == avoid or not 0 <= target < COLS:
            continue
        if (row + 1, target) not in keys:
            continue
        if target in edges.get((row, col), set()):
            continue
        if _crosses(edges, row, col, target):
            continue
        if _rejoins_immediately(edges, parents, row, col, target):
            continue
        edges.setdefault((row, col), set()).add(target)
        parents.setdefault((row + 1, target), set()).add(col)
        return True
    return False


def _repair_rejoins(
    edges: dict[tuple[int, int], set[int]], parents: dict[tuple[int, int], set[int]]
) -> None:
    """Remove edges that let two branches rejoin one floor after they split.

    _walk_paths retries a whole path that would add one, which removes almost
    all of them. This catches the residue: a path whose every option collided
    on all of its retries. Working from the splitting node lets us drop
    whichever of the two offending edges is safe to lose.
    """
    # A repair can expose another, so iterate. This is best effort, not a
    # guarantee: generate() checks the result and re-walks if any survive.
    for _ in range(3):
        keys = set(edges) | set(parents)
        for row in range(ROWS - 2):
            for col in sorted({c for (r, c) in edges if r == row}):
                siblings = sorted(edges.get((row, col), set()))
                for i in range(len(siblings)):
                    for j in range(i + 1, len(siblings)):
                        a, b = siblings[i], siblings[j]
                        shared = edges.get((row + 1, a), set()) & edges.get((row + 1, b), set())
                        for target in sorted(shared):
                            if _drop_edge(edges, parents, row + 1, a, target):
                                continue
                            if _drop_edge(edges, parents, row + 1, b, target):
                                continue
                            # Both siblings have only this child. Give one
                            # another way up, then the drop is safe.
                            added = False
                            for src in (a, b):
                                if _try_add_child(edges, parents, keys, row + 1, src, target):
                                    _drop_edge(edges, parents, row + 1, src, target)
                                    added = True
                                    break
                            if added:
                                continue
                            # Last resort: unsplit. When a node has more
                            # children than the next row has distinct columns,
                            # no edit on the children's row can help, so drop
                            # one of this node's own out-edges instead.
                            for src in (a, b):
                                if _drop_edge(edges, parents, row, col, src):
                                    break


# ---------------------------------------------------------------------------
# room assignment
# ---------------------------------------------------------------------------


def _is_forced_row(row: int) -> bool:
    return row in (0, TREASURE_ROW, ROWS - 1)


def _forced_kind(row: int) -> str:
    if row == 0:
        return "monster"
    if row == TREASURE_ROW:
        return "treasure"
    return "rest"


def _row_allows(kind: str, row: int) -> bool:
    if kind in ("rest", "elite") and row < NO_REST_ELITE_BEFORE_ROW:
        return False
    if kind == "rest" and row >= ROWS - 2:
        return False
    return True


def _build_bag(rng: random.Random, assignable: int, act: int, ascension: int) -> list[str]:
    bag: list[str] = []
    for kind, share in QUOTAS.items():
        if kind == "elite":
            share *= elite_multiplier(act, ascension)
        bag.extend([kind] * round(share * assignable))
    rng.shuffle(bag)
    return bag


def _plan_rows(
    rng: random.Random, rows_to_nodes: dict[int, list[tuple[int, int]]], bag: list[str]
) -> dict[int, list[str]]:
    """Spread the bag across floors instead of letting the lowest ones drain it.

    Dealing the bag strictly row by row starves the top of the map: the early
    rows are the only ones that can legally take shop and unknown, so they
    consume every one before the upper rows are reached. Assigning each bag
    item to a random legal *slot* keeps the same totals while spreading the
    variety over the whole climb, which is the point of a routing decision.
    """
    slots: list[int] = []
    for row, keys in rows_to_nodes.items():
        slots.extend([row] * len(keys))
    rng.shuffle(slots)

    plan: dict[int, list[str]] = {row: [] for row in rows_to_nodes}
    for kind in bag:
        for i, row in enumerate(slots):
            if _row_allows(kind, row):
                plan[row].append(kind)
                slots.pop(i)
                break
    for row in plan:
        rng.shuffle(plan[row])
    return plan


def _kind_is_legal(nodes: dict[tuple[int, int], Node], node: Node, kind: str) -> bool:
    if not _row_allows(kind, node.row):
        return False

    parent_cols = [
        n.col
        for n in nodes.values()
        if n.row == node.row - 1 and node.col in n.next_cols
    ]
    if kind in PARENT_UNIQUE_KINDS:
        for pcol in parent_cols:
            if nodes[(node.row - 1, pcol)].kind == kind:
                return False

    if kind in SIBLING_UNIQUE_KINDS:
        for pcol in parent_cols:
            for sib_col in nodes[(node.row - 1, pcol)].next_cols:
                sib = nodes.get((node.row, sib_col))
                if sib is not None and sib_col != node.col and sib.kind == kind:
                    return False
    return True


def _assign_kinds(
    rng: random.Random, edges: dict[tuple[int, int], set[int]], act: int, ascension: int
) -> dict[tuple[int, int], Node]:
    keys = set(edges)
    for (row, _col), targets in edges.items():
        for t in targets:
            keys.add((row + 1, t))

    nodes: dict[tuple[int, int], Node] = {}
    for key in sorted(keys):
        row, col = key
        nodes[key] = Node(
            row=row,
            col=col,
            kind=_forced_kind(row) if _is_forced_row(row) else "monster",
            next_cols=tuple(sorted(edges.get(key, ()))),
        )

    rows_to_nodes: dict[int, list[tuple[int, int]]] = {}
    for key in sorted(nodes):
        if not _is_forced_row(key[0]):
            rows_to_nodes.setdefault(key[0], []).append(key)

    total = sum(len(v) for v in rows_to_nodes.values())
    bag = _build_bag(rng, total, act, ascension)
    plan = _plan_rows(rng, rows_to_nodes, bag)

    for row, keys_in_row in rows_to_nodes.items():
        planned = plan[row]
        for key in keys_in_row:
            for i, kind in enumerate(planned):
                if _kind_is_legal(nodes, nodes[key], kind):
                    nodes[key] = nodes[key].replace(kind=kind)
                    planned.pop(i)
                    break
            # Nothing legal left for this row, so the node stays a monster room.

    # Every node on the last climbable row funnels into the single boss.
    nodes[(BOSS_ROW, BOSS_COL)] = Node(row=BOSS_ROW, col=BOSS_COL, kind="boss")
    for key in list(nodes):
        if key[0] == ROWS - 1:
            nodes[key] = nodes[key].replace(next_cols=(BOSS_COL,))

    return nodes


def generate(seed: int, act: int, ascension: int = 0) -> SpireMap:
    """Build the act map for a seed. Pure: same inputs, same map.

    `act` is unbounded. Acts past the Heart keep generating, with elite
    density climbing, so a run has no level limit.
    """
    base = act_seed(seed, act)
    # The walker retries individual paths and the repair pass cleans up the
    # residue, but neither is a guarantee: a node can have more children than
    # the next row has columns. Re-walk from a perturbed seed until the graph
    # is clean, and fail loudly rather than returning an illegal map. Attempt 0
    # succeeds for all but a handful of seeds, so clean maps are unchanged.
    for attempt in range(GENERATE_ATTEMPTS):
        rng = random.Random(base + attempt)
        edges = _walk_paths(rng)
        if _count_rejoins(edges) == 0:
            break
    else:
        raise RuntimeError(
            f"could not generate a rejoin-free map for seed={seed} act={act} "
            f"in {GENERATE_ATTEMPTS} attempts"
        )
    nodes = _assign_kinds(rng, edges, act, ascension)
    pool = boss_pool(load_bosses(), act)
    boss = dict(pool[rng.randrange(len(pool))])
    return SpireMap(seed=seed, act=act, ascension=ascension, nodes=nodes, boss=boss)


# ---------------------------------------------------------------------------
# unknown resolution and traversal
# ---------------------------------------------------------------------------


def resolve_unknown(spire_map: SpireMap, node: Node, ramp: Ramp) -> dict:
    """Resolve one unknown node, frozen so re-entering never rerolls."""
    if node.key in spire_map._unknown:
        return spire_map._unknown[node.key]

    rng = floor_rng(act_seed(spire_map.seed, spire_map.act), node.row)
    outcome = "event"
    for kind in RAMP_ORDER:
        if rng.random() < ramp.chance(kind):
            ramp.fire(kind)
            outcome = kind
            break
        ramp.miss(kind)

    result = {"node": node.id, "resolve": outcome}
    spire_map._unknown[node.key] = result
    return result


def legal_moves(spire_map: SpireMap, node: Node | None) -> list[Node]:
    """Any entry is legal to start. After that, only outgoing edges are."""
    if node is None:
        return spire_map.row_nodes(0)
    return spire_map.next_nodes(node)


def is_legal_move(spire_map: SpireMap, frm: Node | None, to: Node) -> bool:
    return any(n.key == to.key for n in legal_moves(spire_map, frm))


# ---------------------------------------------------------------------------
# the lever: a rerunnable invariant check
# ---------------------------------------------------------------------------


def check_invariants(spire_map: SpireMap) -> list[str]:
    """Return a list of violated invariants. Empty means the map is legal."""
    problems: list[str] = []
    m = spire_map

    def fail(msg: str) -> None:
        problems.append(f"seed={m.seed} act={m.act} {msg}")

    if m.rows != ROWS or m.cols != COLS:
        fail(f"grid is {m.rows}x{m.cols}")

    entries = m.row_nodes(0)
    if not entries:
        fail("no entry nodes")
    if len({n.col for n in entries}) < 2:
        fail("fewer than two entrances")
    if any(n.kind != "monster" for n in entries):
        fail("floor 1 is not all monster")

    for row in range(ROWS):
        if not m.row_nodes(row):
            fail(f"row {row} is empty")

    if any(n.kind != "treasure" for n in m.row_nodes(TREASURE_ROW)):
        fail("treasure floor is not all treasure")
    if any(n.kind != "rest" for n in m.row_nodes(ROWS - 1)):
        fail("pre-boss floor is not all rest")
    if not m.boss.get("name"):
        fail("boss is not named")

    boss_nodes = [n for n in m.nodes.values() if n.kind == "boss"]
    if len(boss_nodes) != 1:
        fail(f"expected exactly one boss node, found {len(boss_nodes)}")
    else:
        boss_node = boss_nodes[0]
        if boss_node.next_cols:
            fail("the boss has outgoing edges")
        for node in m.row_nodes(ROWS - 1):
            if node.next_cols != (boss_node.col,):
                fail(f"{node.id} does not funnel into the boss")

    for node in m.nodes.values():
        if node.kind not in KINDS:
            fail(f"unknown kind {node.kind!r} at {node.id}")
        if node.kind == "boss":
            continue
        if not node.next_cols:
            fail(f"dead end at {node.id}")

        for col in node.next_cols:
            # Edges into the boss converge from anywhere; that is the point.
            if node.row != ROWS - 1 and abs(col - node.col) > 1:
                fail(f"{node.id} steps more than one column")
            if (node.row + 1, col) not in m.nodes:
                fail(f"{node.id} points at a missing node")

        if node.kind in ("rest", "elite") and node.row < NO_REST_ELITE_BEFORE_ROW:
            fail(f"{node.kind} too early at {node.id}")
        if node.kind == "rest" and node.row == ROWS - 2:
            fail(f"rest directly below the pre-boss rest at {node.id}")

        # The parent and sibling rules govern bag dealing. Forced floors are
        # assigned before that step and are uniform by design, so a whole floor
        # of treasure or rest is legal rather than a violation.
        if not _is_forced_row(node.row):
            if node.kind in PARENT_UNIQUE_KINDS:
                for parent in m.parents(node):
                    if parent.kind == node.kind:
                        fail(f"{node.kind} stacked along an edge at {node.id}")
            if node.kind in SIBLING_UNIQUE_KINDS:
                for sib in m.siblings(node):
                    if sib.kind == node.kind and not _is_forced_row(sib.row):
                        fail(f"{node.kind} duplicated between siblings at {node.id}")

    for row in range(ROWS - 1):
        edges = [(n.col, c) for n in m.row_nodes(row) for c in n.next_cols]
        for a_from, a_to in edges:
            for b_from, b_to in edges:
                if a_from < b_from and a_to > b_to:
                    fail(f"crossing edges on row {row}")

    # Two branches that just split may not rejoin one floor later. Converging
    # on the boss is exempt, since every path is meant to end there.
    for node in m.nodes.values():
        if node.row + 2 >= BOSS_ROW:
            continue
        kids = m.next_nodes(node)
        for i in range(len(kids)):
            for j in range(i + 1, len(kids)):
                shared = set(kids[i].next_cols) & set(kids[j].next_cols)
                if shared:
                    fail(f"branches from {node.id} rejoin immediately at row {node.row + 2}")

    # Variety has to be spread over the climb, not bunched at the bottom. A
    # floor whose nodes are all the same kind offers no routing decision.
    uniform = sum(
        1
        for row in range(ROWS)
        if not _is_forced_row(row) and len({n.kind for n in m.row_nodes(row)}) == 1
    )
    assignable_rows = sum(1 for row in range(ROWS) if not _is_forced_row(row))
    if uniform > assignable_rows // 2:
        fail(f"{uniform} of {assignable_rows} choosable floors offer no choice")

    run = 0
    for row in range(ROWS):
        if _is_forced_row(row):
            run = 0
            continue
        kinds = {n.kind for n in m.row_nodes(row)}
        run = run + 1 if kinds == {"monster"} else 0
        if run >= 4:
            fail(f"four consecutive monster-only floors ending at row {row}")

    # Quota bounds. Without these a map of almost pure monster rooms is
    # "legal", which is how a zero-elite act would slip past. Bands are wide
    # because the bag rounds and the placement rules reject some slots, but
    # they are tight enough to catch a kind going missing. Measured ranges sit
    # comfortably inside them.
    assignable = [
        n for n in m.nodes.values() if not _is_forced_row(n.row) and n.kind != "boss"
    ]
    if assignable:
        for kind, share in QUOTAS.items():
            target = share
            if kind == "elite":
                target *= elite_multiplier(m.act, m.ascension)
            actual = sum(1 for n in assignable if n.kind == kind) / len(assignable)
            if not target * 0.35 <= actual <= target * 1.9:
                fail(f"{kind} share {actual:.3f} is outside the band around {target:.3f}")

    # Routing has to exist. A map with almost no branch points is a corridor,
    # whatever its room mix, and the whole point of the facet is choosing.
    forks = sum(1 for n in m.nodes.values() if len(n.next_cols) > 1)
    if forks < 3:
        fail(f"only {forks} branch point(s), so there is nothing to route")

    reached: set[tuple[int, int]] = set()
    frontier = list(entries)
    while frontier:
        node = frontier.pop()
        if node.key in reached:
            continue
        reached.add(node.key)
        frontier.extend(m.next_nodes(node))
    if reached != set(m.nodes):
        fail("some nodes are unreachable from an entry")

    return problems


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------

# One glyph vocabulary for the whole project. These are the silhouettes named in
# design/spire-ai/ui/ENTITY_STANDARDS.md, and the Rust server's terminal renderer
# uses the same set. Before this, `mapgen.py` printed M/E/R/$/T/? while the client
# drew ✦/✸/▲/◆/▮/? — two visual languages for the same seven node kinds, and no
# doc reconciled them.
GLYPHS = {
    "monster": "✦",
    "elite": "✸",
    "rest": "▲",
    "shop": "◆",
    "treasure": "▮",
    "event": "◇",
    "boss": "☠",
    "unknown": "?",
}


def render(spire_map: SpireMap) -> str:
    """ASCII map, boss at the top, floor 1 at the bottom."""
    width = spire_map.cols * 4
    lines = [f"  {'BOSS':^{width}}", f"  {spire_map.boss['name']:^{width}}"]
    for row in range(spire_map.rows - 1, -1, -1):
        nodes = {n.col: n for n in spire_map.row_nodes(row)}
        cells = "".join(
            f" {GLYPHS[nodes[c].kind]}  " if c in nodes else "    "
            for c in range(spire_map.cols)
        )
        lines.append(f"{row + 1:>2}{cells}")
        if row:
            links = ["    "] * spire_map.cols
            for node in spire_map.row_nodes(row - 1):
                for target in node.next_cols:
                    # Read top-down: the glyph sits under the upper node and
                    # leans toward the lower node it came from.
                    mark = "|" if target == node.col else ("/" if target > node.col else "\\")
                    if links[target].strip() in ("", mark):
                        links[target] = f" {mark}  "
                    else:
                        links[target] = " *  "
            lines.append(f"  {''.join(links)}")
    legend = "  ".join(f"{g}={k}" for k, g in GLYPHS.items())
    lines.append(f"\n  {legend}")
    return "\n".join(lines)


def _cmd_scene_rolls(args: argparse.Namespace) -> int:
    """Print one scene's roll vector. Used by tools/scenes.mjs so the gallery
    composes from exactly the numbers the engine would ship, not lookalikes."""
    print(json.dumps({
        "seed": args.seed,
        "act": args.act,
        "scene": args.scene,
        "rolls": scene_rolls(args.seed, args.act, args.scene),
    }))
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    spire_map = generate(args.seed, args.act, ascension=args.ascension)
    if args.json:
        print(json.dumps(spire_map.to_dict(), indent=2))
    else:
        print(render(spire_map))
    return 0


def _cmd_emit_js(args: argparse.Namespace) -> int:
    """Emit maps as a plain JS global so the wireframe demo needs no build step.

    Keeps one source of truth: the demo renders what this generator produced
    rather than reimplementing the rules in JavaScript.
    """
    maps = [
        generate(seed, act, ascension=args.ascension).to_dict()
        for seed in range(args.seeds)
        for act in range(1, args.acts + 1)
    ]
    ramp = {"order": list(RAMP_ORDER), "base": dict(RAMP_BASE)}
    print("/* Generated by scripts/mapgen.py emit-js. Do not edit by hand. */")
    print(f"window.SPIRE_MAPS = {json.dumps(maps, indent=2)};")
    print(f"window.SPIRE_RAMP = {json.dumps(ramp)};")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    problems: list[str] = []
    checked = 0
    for seed in range(args.seeds):
        for act in range(1, args.acts + 1):
            for ascension in (0, 1):
                spire_map = generate(seed, act, ascension=ascension)
                problems.extend(check_invariants(spire_map))
                if generate(seed, act, ascension=ascension).fingerprint() != (
                    spire_map.fingerprint()
                ):
                    problems.append(f"seed={seed} act={act} is not reproducible")
                checked += 1
    if problems:
        for line in problems[: args.limit]:
            print(line)
        print(f"\n{len(problems)} problem(s) across {checked} maps")
        return 1
    print(f"ok: {checked} maps, every invariant holds")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate and check Spire act maps.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    show = sub.add_parser("show", help="render one map")
    show.add_argument("--seed", type=int, default=0)
    show.add_argument("--act", type=int, default=1)
    show.add_argument("--ascension", type=int, default=0)
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=_cmd_show)

    emit = sub.add_parser("emit-js", help="emit maps as a JS global for the demo")
    emit.add_argument("--seeds", type=int, default=EMIT_SEEDS)
    emit.add_argument("--acts", type=int, default=EMIT_ACTS)
    emit.add_argument("--ascension", type=int, default=0)
    emit.set_defaults(func=_cmd_emit_js)

    rolls = sub.add_parser("scene-rolls", help="the roll vector for one scene")
    rolls.add_argument("--seed", type=int, default=0)
    rolls.add_argument("--act", type=int, default=1)
    rolls.add_argument("--scene", required=True)
    rolls.set_defaults(func=_cmd_scene_rolls)

    verify = sub.add_parser("verify", help="check invariants across many seeds")
    verify.add_argument("--seeds", type=int, default=200)
    verify.add_argument("--acts", type=int, default=6)
    verify.add_argument("--limit", type=int, default=20)
    verify.set_defaults(func=_cmd_verify)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
