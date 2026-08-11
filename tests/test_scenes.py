"""Tests for content/scenes.json — the scene wireframes.

This file is the backgrounds, not a description of them: `app/scene.js` composes
from it at runtime and `tools/scenes.mjs` draws the labelled wireframe from the
same data. So it gets the same treatment as any other content pool — every
reference must resolve, and the invariants the composer relies on are asserted
here rather than discovered as a blank layer in someone's Claude Desktop.

The load-bearing ones are the contrast-budget tests. In a UI this text-dense the
content covers most of the frame, so a background cannot be kept readable by
drawing around it — there is nowhere to draw. It is kept readable by bounding how
loud each layer may be, and by fading shapes where they pass behind text. That is
the only failure mode here that would actually damage the product.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCENES_PATH = REPO_ROOT / "content" / "scenes.json"
APP_JS_PATH = REPO_ROOT / "app" / "app.js"
SILHOUETTE_LAYERS = {"far", "mid", "near", "floor"}


@pytest.fixture(scope="module")
def scenes() -> dict:
    with SCENES_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def rects_overlap(a: dict, b: dict) -> float:
    """Fraction of `a` covered by `b`."""
    x = max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    y = max(0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    area = a["w"] * a["h"]
    return (x * y) / area if area else 0.0


# --------------------------------------------------------------------------- #
# structure
# --------------------------------------------------------------------------- #

def test_every_declared_layer_is_used_by_some_scene(scenes):
    declared = {layer["id"] for layer in scenes["layers"]}
    used = {name for scene in scenes["scenes"].values() for name in scene["layers"]}
    assert used <= declared, f"scenes use undeclared layers: {used - declared}"
    assert declared == used, f"declared but never used: {declared - used}"


def test_layers_are_ordered_back_to_front_without_gaps(scenes):
    zs = [layer["z"] for layer in scenes["layers"]]
    assert zs == sorted(zs)
    assert zs == list(range(len(zs))), "layer z values must be a dense 0..n-1 ramp"


def test_every_layer_is_silhouette_or_atmosphere(scenes):
    for layer in scenes["layers"]:
        assert layer["kind"] in {"silhouette", "atmosphere"}, layer["id"]
    kinds = {layer["id"]: layer["kind"] for layer in scenes["layers"]}
    assert {k for k, v in kinds.items() if v == "silhouette"} == SILHOUETTE_LAYERS


def test_every_scene_declares_its_layers_in_stack_order(scenes):
    order = [layer["id"] for layer in scenes["layers"]]
    for name, scene in scenes["scenes"].items():
        used = scene["layers"]
        assert used == [layer for layer in order if layer in used], (
            f"{name} lists its layers out of stack order"
        )


# --------------------------------------------------------------------------- #
# references resolve
# --------------------------------------------------------------------------- #

def test_every_grammar_entry_names_a_real_component(scenes):
    known = set(scenes["components"]) - {"_comment"}
    for name, scene in scenes["scenes"].items():
        for layer, entries in scene["grammar"].items():
            for entry in entries:
                assert entry["component"] in known, (
                    f"{name}.{layer} references unknown component {entry['component']}"
                )


def test_every_component_is_placed_on_the_layer_it_belongs_to(scenes):
    components = {k: v for k, v in scenes["components"].items() if k != "_comment"}
    for name, scene in scenes["scenes"].items():
        for layer, entries in scene["grammar"].items():
            for entry in entries:
                expected = components[entry["component"]]["layer"]
                assert expected == layer, (
                    f"{name} puts {entry['component']} on {layer}, "
                    f"but it belongs on {expected}"
                )


def test_every_grammar_entry_names_a_zone_the_scene_declares(scenes):
    shared = set(scenes["bands"]) - {"_comment"}
    for name, scene in scenes["scenes"].items():
        zones = set(scene["zones"]) | shared
        for layer, entries in scene["grammar"].items():
            for entry in entries:
                assert entry["zone"] in zones, (
                    f"{name}.{layer} places {entry['component']} in undeclared zone "
                    f"{entry['zone']}"
                )


def test_every_grammar_layer_is_one_the_scene_uses(scenes):
    for name, scene in scenes["scenes"].items():
        for layer in scene["grammar"]:
            assert layer in scene["layers"], (
                f"{name} has grammar for {layer} but does not use that layer"
            )


def test_every_component_is_used_by_at_least_one_scene(scenes):
    known = set(scenes["components"]) - {"_comment"}
    used = {
        entry["component"]
        for scene in scenes["scenes"].values()
        for entries in scene["grammar"].values()
        for entry in entries
    }
    assert known == used, f"components declared but never placed: {known - used}"


def test_every_screen_maps_to_a_real_scene_and_modifier(scenes):
    known_scenes = set(scenes["scenes"])
    known_modifiers = set(scenes["modifiers"]) - {"_comment"}
    for screen, binding in scenes["screens"].items():
        if screen == "_comment":
            continue
        assert binding["scene"] in known_scenes, f"{screen} -> unknown scene"
        if "modifier" in binding:
            assert binding["modifier"] in known_modifiers, f"{screen} -> unknown modifier"


def client_screen_names() -> set[str]:
    """The screens the client actually renders, read from the client.

    Parsed out of `app/app.js` rather than written down here, because a written
    list goes stale silently. This test held nine names hard-coded; when the
    chest got its own screen the tenth was simply absent, so `screens.treasure`
    could have been deleted from the scene data and the test would still have
    passed over a room with no background binding at all.
    """
    source = APP_JS_PATH.read_text(encoding="utf-8")
    block = re.search(r"const SCREENS = \{(.*?)\n\};", source, re.S)
    assert block, "could not find the SCREENS map in app/app.js"
    names = set(re.findall(r"(\w+):\s*screen\w+", block.group(1)))
    assert len(names) >= 9, f"parsed too few screens from app.js: {names}"
    return names


def test_every_client_screen_has_a_scene(scenes):
    """Every screen the client can render has somewhere to stand.

    A screen with no scene binding falls through to a bare background — which is
    what the chest did before `treasure` was declared, standing in the combat
    tint on a screen labelled Combat.
    """
    missing = client_screen_names() - set(scenes["screens"])
    assert not missing, f"client screens with no scene binding: {sorted(missing)}"


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #

def all_rects(scenes):
    for name, scene in scenes["scenes"].items():
        for zone_name, rect in scene["zones"].items():
            yield f"{name}.zones.{zone_name}", rect
        for i, rect in enumerate(scene["safe"]):
            yield f"{name}.safe[{i}]", rect
    for band, rect in scenes["bands"].items():
        if band != "_comment":
            yield f"bands.{band}", rect


def test_every_rectangle_is_normalised_and_inside_the_frame(scenes):
    for label, rect in all_rects(scenes):
        for key in ("x", "y", "w", "h"):
            assert key in rect, f"{label} missing {key}"
            assert 0.0 <= rect[key] <= 1.0, f"{label}.{key} = {rect[key]} is outside 0..1"
        assert rect["w"] > 0 and rect["h"] > 0, f"{label} has no area"
        assert rect["x"] + rect["w"] <= 1.0001, f"{label} runs off the right edge"
        assert rect["y"] + rect["h"] <= 1.0001, f"{label} runs off the bottom edge"


def test_every_scene_declares_at_least_one_safe_area(scenes):
    for name, scene in scenes["scenes"].items():
        assert scene["safe"], f"{name} declares no safe area, so text has nowhere to live"


# --------------------------------------------------------------------------- #
# the contrast budget — the invariant that actually keeps this readable
# --------------------------------------------------------------------------- #

def test_every_layer_declares_a_contrast_budget(scenes):
    for layer in scenes["layers"]:
        assert "max_delta" in layer, f"{layer['id']} declares no max_delta"
        assert 0.0 <= layer["max_delta"] <= 1.0, layer["id"]


def test_no_layer_exceeds_the_legibility_ceiling(scenes):
    """The whole product risk in one assertion.

    A text-dense UI leaves nowhere to draw *around* the content, so legibility is
    bought by bounding how loud the background may be rather than by keeping it
    out of the way. Past the ceiling, silhouette starts competing with body text
    and the intent bar becomes work to read.
    """
    ceiling = scenes["contrast"]["legibility_ceiling"]
    for layer in scenes["layers"]:
        assert layer["max_delta"] <= ceiling, (
            f"{layer['id']} may step {layer['max_delta']} from the void, past the "
            f"{ceiling} ceiling. Raising this makes text harder to read; that is "
            "the trade being made, so make it deliberately."
        )


def test_silhouette_layers_step_further_forward_as_they_come_nearer(scenes):
    """Depth reads from the ordering of the steps, not their size.

    If `far` were allowed a bigger step than `mid`, distance would invert and the
    background would read as flat regardless of what the shapes are.
    """
    deltas = {layer["id"]: layer["max_delta"] for layer in scenes["layers"]}
    assert deltas["far"] < deltas["mid"] < deltas["near"], (
        f"silhouette depth is not monotonic: far={deltas['far']} "
        f"mid={deltas['mid']} near={deltas['near']}"
    )
    assert deltas["floor"] < deltas["near"], "the floor must not out-shout the framing"


def test_safe_areas_attenuate_rather_than_exclude(scenes):
    """Safe rectangles fade the background where text sits; they do not clip it.

    An earlier version of this file tried geometric exclusion — silhouette
    forbidden inside safe areas — and every single scene failed it, because in
    this UI the content covers 80-100% of the frame. That was the model being
    wrong, not the scenes.
    """
    attenuation = scenes["contrast"]["safe_attenuation"]
    assert 0.0 < attenuation < 0.6, (
        f"safe_attenuation is {attenuation}; above ~0.6 it stops meaningfully "
        "quieting the background behind text"
    )


def test_safe_areas_cover_where_text_actually_is(scenes):
    """Each scene's safe rectangles must cover most of the frame, because they do.

    This is the honest inverse of the exclusion test: rather than pretending the
    content is confined, assert that the scene admits how much of the frame it
    occupies, so the attenuation is applied everywhere it needs to be.
    """
    for name, scene in scenes["scenes"].items():
        covered = sum(rect["w"] * rect["h"] for rect in scene["safe"])
        assert covered >= 0.5, (
            f"{name} claims only {covered:.0%} of the frame is text-critical. "
            "If that is true the layout changed; if not, the safe areas are wrong."
        )


def test_every_scene_puts_its_light_source_somewhere_real(scenes):
    for name, scene in scenes["scenes"].items():
        light = scene["light"]
        assert 0.0 <= light["x"] <= 1.0, name
        assert 0.0 <= light["y"] <= 1.0, name
        assert 0.3 <= light["spread"] <= 2.0, name


def test_there_is_exactly_one_warm_source_per_scene(scenes):
    """DESIGN_SYSTEM: one warm light source. Not a stylistic preference — two
    light sources make the silhouette layers ambiguous about which way is up."""
    for name, scene in scenes["scenes"].items():
        assert isinstance(scene["light"], dict), f"{name} declares more than one light"


# --------------------------------------------------------------------------- #
# the roll budget
# --------------------------------------------------------------------------- #

def rolls_needed(scenes: dict, name: str) -> int:
    """Independent restatement of the composer's fixed-slice contract.

    Deliberately written from the JSON rather than by calling
    `mapgen.scene_budget`, so this checks the engine rather than agreeing with
    it. The first version of this file mirrored the *formula* the composer
    declared (`2 + 3 * count_max`) instead of what the builders drew, and so
    passed happily while every scene overran its vector by roughly double.
    """
    costs = {key: entry["rolls"]
             for key, entry in scenes["components"].items() if not key.startswith("_")}
    return sum(
        2 + costs[entry["component"]] * entry["count"][1]
        for entries in scenes["scenes"][name]["grammar"].values()
        for entry in entries
    )


def test_every_component_declares_what_it_draws(scenes):
    """The slice formula multiplies this number. A component without one gets a
    slice of two, and silently repeats itself inside it."""
    for name, entry in scenes["components"].items():
        if name.startswith("_"):
            continue
        assert "rolls" in entry, f"{name} declares no roll cost"
        assert isinstance(entry["rolls"], int) and entry["rolls"] >= 0, name
        assert entry["rolls"] <= 24, (
            f"{name} claims {entry['rolls']} rolls per instance — past about 20 "
            "the component is doing too much and should be split"
        )


def test_the_engine_ships_every_scene_the_rolls_it_will_walk(scenes):
    """The bug this file exists to prevent: a vector shorter than the walk. It
    does not error — the cursor wraps and the scene quietly repeats itself."""
    import mapgen

    for name in scenes["scenes"]:
        needed = rolls_needed(scenes, name)
        shipped = mapgen.scene_budget(name)
        assert shipped >= needed, (
            f"{name} walks {needed} rolls but the engine ships {shipped}"
        )


def test_a_scene_carries_the_worst_modifier_on_top(scenes):
    """Modifiers add entries to a scene rather than replacing it, so the budget
    has to cover the version with the most in it, not the bare one."""
    import mapgen

    extra = max(
        sum(2 + scenes["components"][entry["component"]]["rolls"] * entry["count"][1]
            for entries in (mod.get("extra") or {}).values() for entry in entries)
        for key, mod in scenes["modifiers"].items() if not key.startswith("_")
    )
    assert extra > 0, "no modifier adds anything — this test is watching nothing"
    for name in scenes["scenes"]:
        assert mapgen.scene_budget(name) >= rolls_needed(scenes, name) + extra


def test_growing_a_scene_keeps_the_dice_already_dealt():
    """Why a per-scene length is safe. The vector is a prefix of one stream, so
    asking for more floats never disturbs the ones already there — a scene that
    gains a component keeps every die the components before it were drawn with.
    """
    import mapgen

    short = mapgen.scene_rolls(5, 2, "chamber", 40)
    long = mapgen.scene_rolls(5, 2, "chamber", 120)
    assert long[:40] == short


def test_scene_rolls_are_deterministic_and_isolated():
    """Same inputs, same picture. Different act or scene, different picture."""
    import mapgen

    a = mapgen.scene_rolls(7, 1, "chamber")
    assert a == mapgen.scene_rolls(7, 1, "chamber")
    assert len(a) == mapgen.scene_budget("chamber")
    assert all(0.0 <= r < 1.0 for r in a)
    assert a != mapgen.scene_rolls(7, 2, "chamber"), "acts must not share a scene"
    assert a != mapgen.scene_rolls(7, 1, "alcove"), "scenes must not share a roll stream"
    assert a != mapgen.scene_rolls(8, 1, "chamber"), "seeds must not collide"


def test_scene_seeds_do_not_collide_across_the_act_scene_grid():
    """`act_seed`'s docstring records a real collision from adding an offset
    instead of hashing. Hold the scene seed to the same standard."""
    import mapgen

    seen = {}
    for act in range(1, 12):
        for scene in ("gate", "shaft", "chamber", "sanctum", "crossroads",
                      "vault", "alcove", "market", "study", "summit"):
            value = mapgen.scene_seed(3, act, scene)
            assert value not in seen, f"{(act, scene)} collides with {seen[value]}"
            seen[value] = (act, scene)


def test_no_scene_ships_rolls_it_will_never_walk(scenes):
    """The other side of the same coin. Every float rides on the map payload ten
    times over, so a budget well above what a scene walks is dead weight."""
    import mapgen

    for name in scenes["scenes"]:
        needed = rolls_needed(scenes, name)
        shipped = mapgen.scene_budget(name)
        assert shipped <= needed * 2 + 32, (
            f"{name} walks {needed} rolls but is shipped {shipped}"
        )


def test_counts_are_sane_ranges(scenes):
    for name, scene in scenes["scenes"].items():
        for layer, entries in scene["grammar"].items():
            for entry in entries:
                low, high = entry["count"]
                assert 0 <= low <= high, f"{name}.{layer} {entry['component']}: {low}..{high}"
                assert high <= 8, (
                    f"{name}.{layer} can place {high} {entry['component']}s — "
                    "past about 6 the silhouette reads as noise"
                )


# --------------------------------------------------------------------------- #
# biomes
# --------------------------------------------------------------------------- #

def test_every_act_has_a_biome_and_the_endless_one_is_last(scenes):
    biomes = {k: v for k, v in scenes["biomes"].items() if k != "_comment"}
    acts = sorted(b["act"] for b in biomes.values())
    assert acts == list(range(1, len(acts) + 1)), f"biome acts are not a dense ramp: {acts}"
    assert max(acts) >= 5, "acts past the Heart need a biome; the climb is unbounded"


def test_biomes_tint_tokens_rather_than_naming_colours(scenes):
    """A biome that names a hex only works in one theme."""
    for name, biome in scenes["biomes"].items():
        if name == "_comment":
            continue
        assert biome["tint"].startswith("--"), f"{name} names a colour instead of a token"
        assert 0 < biome["tint_pct"] <= 25, (
            f"{name} tints {biome['tint_pct']}% — past ~25 the stone ramp stops "
            "reading as stone and the theme flip breaks"
        )


def test_every_biome_declares_the_variants_the_components_ask_for(scenes):
    for name, biome in scenes["biomes"].items():
        if name == "_comment":
            continue
        assert biome["arch"] in {"round", "pointed", "broken"}, name
        assert biome["pillar"], f"{name} declares no pillar variants"
        for variant in biome["pillar"]:
            assert variant in {"round", "fluted", "broken"}, f"{name}: {variant}"
