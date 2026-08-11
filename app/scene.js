/* The scene composer.
 *
 * Reads `content/scenes.json` — inlined at build time by tools/build-app.mjs —
 * and builds a background out of modular silhouette parts. The JSON is not a
 * description of the backgrounds; it is their source, and tools/scenes.mjs draws
 * its labelled wireframe from the same object, so the spec cannot drift.
 *
 * Two things are worth understanding before changing anything here.
 *
 * **There is no RNG.** `scripts/mapgen.py` established the pattern with
 * `unknown_rolls`: the engine pre-rolls a fixed float vector, ships it in the
 * payload, and the client applies shared thresholds. So this file is a pure
 * function from (scene, biome, rolls) to SVG — same inputs, same picture, every
 * time, on any machine. Re-entering a floor looks identical because it *is*
 * identical, not because something was cached.
 *
 * **Legibility is bought with contrast, not geometry.** The content column
 * covers most of the frame, so there is nowhere to draw "around" the text. Each
 * layer instead declares how far it may step from the void, and any shape whose
 * box crosses a safe rectangle has that step attenuated. The result is bold
 * framing at the edges and a whisper behind the paragraphs — which is what a
 * background is supposed to do anyway.
 */

const SVG_NS = 'http://www.w3.org/2000/svg';

/* The frame every scene is composed in. Normalised coordinates from the JSON are
   multiplied up into this box; the SVG then scales to whatever the viewport is,
   so a scene is resolution-independent and never needs a resize handler. */
const W = 1000;
const H = 700;

/* ------------------------------------------------------------------ rolls -- */

/* A cursor over the engine's float vector.
 *
 * Each grammar entry consumes a FIXED slice — `2 + 3 * count_max` — whether or
 * not it uses all of it. That is deliberate: if entries consumed only what they
 * needed, adding one pillar to a scene would shift the dice of every entry after
 * it and silently redraw the whole background. content/scenes.json states the
 * same formula, and tests/test_scenes.py holds both to it. */
class Rolls {
  constructor(values) {
    this.values = (values && values.length) ? values : [0.5];
    this.i = 0;
  }

  take() {
    const value = this.values[this.i % this.values.length];
    this.i += 1;
    return value;
  }

  /* Skip to the end of this entry's slice, so the next entry starts where the
     contract says it does regardless of what this one used. */
  advanceTo(mark) {
    this.i = mark;
  }

  between(low, high) {
    return low + this.take() * (high - low);
  }

  intBetween(low, high) {
    if (high <= low) return low;
    return Math.min(high, low + Math.floor(this.take() * (high - low + 1)));
  }

  pick(list) {
    if (!list || !list.length) return null;
    return list[Math.min(list.length - 1, Math.floor(this.take() * list.length))];
  }
}

/* ------------------------------------------------------------------ paint -- */

/* How far a shape may step away from the void, given its layer and where it sits.
 *
 * `--sc-void` and `--sc-form` are defined per theme in scene.css, so the step is
 * always *toward* legibility: lighter than near-black on stone, darker than
 * paper on the light theme. The composer never names a colour. */
function fillFor(layerDelta, behindText) {
  // Continuous, not binary. A shape half behind the content column should be
  // half-quieted; an earlier binary version muted anything that touched a safe
  // area, which flattened every wing pillar in the game and made the interiors
  // read as flat washes.
  const quiet = 1 - behindText * (1 - SCENES.contrast.safe_attenuation);
  const pct = Math.round(layerDelta * quiet * 100);
  return `color-mix(in srgb, var(--sc-form) ${pct}%, var(--sc-void))`;
}

/** How much of this shape sits behind text, 0..1. */
function behindTextFraction(box, safeRects) {
  let covered = 0;
  safeRects.forEach((safe) => {
    const x = Math.max(0, Math.min(box.x + box.w, safe.x + safe.w) - Math.max(box.x, safe.x));
    const y = Math.max(0, Math.min(box.y + box.h, safe.y + safe.h) - Math.max(box.y, safe.y));
    covered = Math.max(covered, (x * y) / (box.w * box.h));
  });
  return Math.min(1, covered);
}

/* ------------------------------------------------------- component library -- */

/* Every builder is a pure function of (box, rolls, biome) returning SVG path
   data in frame coordinates. `box` is the zone, already scaled up from the
   normalised rectangle. Shapes are drawn deliberately coarse — a silhouette that
   survives `filter: grayscale(1)` is one made of large, distinguishable masses,
   not of detail. */

const rect = (x, y, w, h) => `M${x} ${y}h${w}v${h}h${-w}Z`;

function crag(box, rolls) {
  const peaks = rolls.intBetween(3, 6);
  const base = box.y + box.h;
  let d = `M${box.x} ${base}`;
  for (let i = 0; i <= peaks; i += 1) {
    const x = box.x + (box.w * i) / peaks;
    const dip = rolls.between(0.25, 1);
    d += `L${x.toFixed(1)} ${(base - box.h * dip).toFixed(1)}`;
    if (i < peaks) {
      const mid = box.x + (box.w * (i + 0.5)) / peaks;
      d += `L${mid.toFixed(1)} ${(base - box.h * rolls.between(0.05, 0.5)).toFixed(1)}`;
    }
  }
  return `${d}L${box.x + box.w} ${base}Z`;
}

function tower(box, rolls) {
  const base = box.y + box.h;
  const taper = rolls.between(0.28, 0.46);
  const left = box.x + box.w * taper * 0.5;
  const right = box.x + box.w - box.w * taper * 0.5;
  const tiers = rolls.intBetween(2, 4);
  let d = `M${box.x} ${base}L${box.x} ${(box.y + box.h * 0.55).toFixed(1)}`;
  for (let i = 0; i < tiers; i += 1) {
    const t = (i + 1) / tiers;
    const y = box.y + box.h * 0.55 * (1 - t);
    const inset = box.w * 0.06 * t;
    d += `L${(box.x + inset).toFixed(1)} ${y.toFixed(1)}`;
  }
  d += `L${left.toFixed(1)} ${box.y.toFixed(1)}L${right.toFixed(1)} ${box.y.toFixed(1)}`;
  for (let i = tiers - 1; i >= 0; i -= 1) {
    const t = (i + 1) / tiers;
    const y = box.y + box.h * 0.55 * (1 - t);
    const inset = box.w * 0.06 * t;
    d += `L${(box.x + box.w - inset).toFixed(1)} ${y.toFixed(1)}`;
  }
  return `${d}L${box.x + box.w} ${(box.y + box.h * 0.55).toFixed(1)}L${box.x + box.w} ${base}Z`;
}

function skyline(box, rolls) {
  const steps = rolls.intBetween(5, 8);
  const base = box.y + box.h;
  let d = `M${box.x} ${base}`;
  let x = box.x;
  for (let i = 0; i < steps; i += 1) {
    const w = box.w / steps;
    const h = box.h * rolls.between(0.15, 0.72);
    d += `L${x.toFixed(1)} ${(base - h).toFixed(1)}L${(x + w).toFixed(1)} ${(base - h).toFixed(1)}`;
    x += w;
  }
  return `${d}L${box.x + box.w} ${base}Z`;
}

function pillar(box, rolls, biome) {
  const variant = rolls.pick(biome.pillar) || 'round';
  const w = box.w * rolls.between(0.34, 0.62);
  const x = box.x + (box.w - w) * rolls.between(0.1, 0.9);
  const base = box.y + box.h;
  const capH = box.h * 0.06;

  if (variant === 'broken') {
    const stop = box.y + box.h * rolls.between(0.3, 0.62);
    return `M${x.toFixed(1)} ${base}L${x.toFixed(1)} ${stop.toFixed(1)}`
      + `L${(x + w * 0.55).toFixed(1)} ${(stop + capH * 1.4).toFixed(1)}`
      + `L${(x + w).toFixed(1)} ${(stop - capH * 0.6).toFixed(1)}`
      + `L${(x + w).toFixed(1)} ${base}Z`;
  }

  const shaftInset = variant === 'fluted' ? w * 0.12 : w * 0.06;
  return `M${(x - w * 0.1).toFixed(1)} ${base}`
    + `L${(x - w * 0.1).toFixed(1)} ${(base - capH).toFixed(1)}`
    + `L${(x + shaftInset).toFixed(1)} ${(base - capH * 2).toFixed(1)}`
    + `L${(x + shaftInset).toFixed(1)} ${(box.y + capH * 2).toFixed(1)}`
    + `L${(x - w * 0.1).toFixed(1)} ${(box.y + capH).toFixed(1)}`
    + `L${(x - w * 0.1).toFixed(1)} ${box.y.toFixed(1)}`
    + `L${(x + w * 1.1).toFixed(1)} ${box.y.toFixed(1)}`
    + `L${(x + w * 1.1).toFixed(1)} ${(box.y + capH).toFixed(1)}`
    + `L${(x + w - shaftInset).toFixed(1)} ${(box.y + capH * 2).toFixed(1)}`
    + `L${(x + w - shaftInset).toFixed(1)} ${(base - capH * 2).toFixed(1)}`
    + `L${(x + w * 1.1).toFixed(1)} ${(base - capH).toFixed(1)}`
    + `L${(x + w * 1.1).toFixed(1)} ${base}Z`;
}

/* An arch is drawn as the WALL around the opening, not the opening itself —
   that is what makes it read as something you are standing inside rather than a
   croquet hoop floating in the dark. */
function arch(box, rolls, biome) {
  const style = biome.arch;
  const span = box.w * rolls.between(0.44, 0.72);
  const cx = box.x + box.w / 2 + (box.w - span) * (rolls.take() - 0.5) * 0.4;
  const left = cx - span / 2;
  const right = cx + span / 2;
  const base = box.y + box.h;
  const spring = box.y + box.h * rolls.between(0.42, 0.62);

  let opening;
  if (style === 'pointed') {
    const apex = box.y + box.h * 0.06;
    opening = `M${left.toFixed(1)} ${base}L${left.toFixed(1)} ${spring.toFixed(1)}`
      + `Q${left.toFixed(1)} ${apex.toFixed(1)} ${cx.toFixed(1)} ${apex.toFixed(1)}`
      + `Q${right.toFixed(1)} ${apex.toFixed(1)} ${right.toFixed(1)} ${spring.toFixed(1)}`
      + `L${right.toFixed(1)} ${base}Z`;
  } else if (style === 'broken') {
    const apex = box.y + box.h * rolls.between(0.1, 0.24);
    opening = `M${left.toFixed(1)} ${base}L${left.toFixed(1)} ${spring.toFixed(1)}`
      + `L${(cx - span * 0.18).toFixed(1)} ${apex.toFixed(1)}`
      + `L${(cx + span * 0.1).toFixed(1)} ${(apex + box.h * 0.08).toFixed(1)}`
      + `L${right.toFixed(1)} ${spring.toFixed(1)}L${right.toFixed(1)} ${base}Z`;
  } else {
    opening = `M${left.toFixed(1)} ${base}L${left.toFixed(1)} ${spring.toFixed(1)}`
      + `A${(span / 2).toFixed(1)} ${(span / 2).toFixed(1)} 0 0 1 ${right.toFixed(1)} ${spring.toFixed(1)}`
      + `L${right.toFixed(1)} ${base}Z`;
  }
  // Outer wall first, opening second: with fill-rule evenodd the second subpath
  // punches a hole, which is the whole trick.
  return `${rect(box.x, box.y, box.w, box.h)}${opening}`;
}

function windowShape(box, rolls, biome) {
  const w = box.w * rolls.between(0.1, 0.2);
  const h = box.h * rolls.between(0.16, 0.34);
  const x = box.x + (box.w - w) * rolls.take();
  const y = box.y + (box.h - h) * rolls.between(0.1, 0.7);
  if (biome.arch === 'round') {
    return `M${x.toFixed(1)} ${(y + h).toFixed(1)}L${x.toFixed(1)} ${(y + w / 2).toFixed(1)}`
      + `A${(w / 2).toFixed(1)} ${(w / 2).toFixed(1)} 0 0 1 ${(x + w).toFixed(1)} ${(y + w / 2).toFixed(1)}`
      + `L${(x + w).toFixed(1)} ${(y + h).toFixed(1)}Z`;
  }
  return rect(x, y, w, h);
}

function wall(box, rolls) {
  const courses = rolls.intBetween(4, 7);
  const top = box.y + box.h * rolls.between(0.0, 0.14);
  let d = rect(box.x, top, box.w, box.y + box.h - top);
  // Mortar lines read as thin gaps punched out of the mass.
  const step = (box.y + box.h - top) / courses;
  for (let i = 1; i < courses; i += 1) {
    const y = top + step * i;
    d += rect(box.x, y, box.w, Math.max(1.5, step * 0.06));
  }
  return d;
}

function stair(box, rolls) {
  const steps = rolls.intBetween(4, 7);
  const base = box.y + box.h;
  let d = `M${box.x} ${base}`;
  for (let i = 0; i < steps; i += 1) {
    const x = box.x + (box.w * i) / steps;
    const y = base - (box.h * (i + 1)) / steps;
    d += `L${x.toFixed(1)} ${y.toFixed(1)}L${(box.x + (box.w * (i + 1)) / steps).toFixed(1)} ${y.toFixed(1)}`;
  }
  return `${d}L${box.x + box.w} ${base}Z`;
}

function gate(box, rolls, biome) {
  const w = box.w * rolls.between(0.3, 0.44);
  const x = box.x + (box.w - w) / 2;
  const base = box.y + box.h;
  const lintel = box.y + box.h * 0.18;
  let d = rect(x - w * 0.14, lintel - box.h * 0.1, w * 1.28, box.h * 0.1);
  d += rect(x, lintel, w, base - lintel);
  if (biome.arch !== 'broken') {
    // A seam down the middle, so it reads as two leaves rather than a slab.
    d += rect(x + w / 2 - 1.5, lintel, 3, base - lintel);
  }
  return d;
}

function brazier(box, rolls) {
  const base = box.y + box.h;
  const w = box.w * 0.6;
  const x = box.x + (box.w - w) / 2;
  const bowlY = box.y + box.h * 0.42;
  return `M${(x + w * 0.32).toFixed(1)} ${base}L${(x + w * 0.42).toFixed(1)} ${(bowlY + box.h * 0.18).toFixed(1)}`
    + `L${(x + w * 0.58).toFixed(1)} ${(bowlY + box.h * 0.18).toFixed(1)}L${(x + w * 0.68).toFixed(1)} ${base}Z`
    + `M${x.toFixed(1)} ${bowlY.toFixed(1)}L${(x + w).toFixed(1)} ${bowlY.toFixed(1)}`
    + `L${(x + w * 0.78).toFixed(1)} ${(bowlY + box.h * 0.2).toFixed(1)}`
    + `L${(x + w * 0.22).toFixed(1)} ${(bowlY + box.h * 0.2).toFixed(1)}Z`
    + rect(x - w * 0.2, base - 4, w * 1.4, 4);
}

function stall(box, rolls) {
  const scallops = rolls.intBetween(4, 7);
  const hem = box.y + box.h * rolls.between(0.5, 0.8);
  let d = `M${box.x} ${box.y}L${box.x + box.w} ${box.y}L${box.x + box.w} ${hem.toFixed(1)}`;
  for (let i = scallops; i > 0; i -= 1) {
    const x0 = box.x + (box.w * i) / scallops;
    const x1 = box.x + (box.w * (i - 1)) / scallops;
    const mid = (x0 + x1) / 2;
    d += `Q${mid.toFixed(1)} ${(hem + box.h * 0.22).toFixed(1)} ${x1.toFixed(1)} ${hem.toFixed(1)}`;
  }
  return `${d}Z`;
}

function chest(box, rolls) {
  const base = box.y + box.h;
  const w = box.w * 0.66;
  const x = box.x + (box.w - w) / 2;
  const lid = box.y + box.h * 0.44;
  return rect(x - w * 0.2, base - box.h * 0.12, w * 1.4, box.h * 0.12)
    + rect(x, lid, w, base - lid - box.h * 0.12)
    + `M${x.toFixed(1)} ${lid.toFixed(1)}A${(w / 2).toFixed(1)} ${(box.h * 0.22).toFixed(1)} 0 0 1 ${(x + w).toFixed(1)} ${lid.toFixed(1)}Z`;
}

function pillarEdge(box, rolls) {
  const fromLeft = box.x < 0.5 * W;
  const w = box.w * rolls.between(0.5, 0.85);
  const x = fromLeft ? box.x : box.x + box.w - w;
  return rect(x, box.y, w, box.h);
}

function chain(box, rolls) {
  const x = box.x + box.w * rolls.take();
  const len = box.h * rolls.between(0.3, 0.8);
  const links = Math.max(3, Math.round(len / 26));
  let d = rect(x - 1.5, box.y, 3, len);
  for (let i = 0; i < links; i += 1) {
    const y = box.y + (len * i) / links;
    d += rect(x - 5, y, 10, 5);
  }
  return d;
}

function banner(box, rolls) {
  const w = box.w * rolls.between(0.3, 0.55);
  const x = box.x + (box.w - w) * rolls.take();
  const len = box.h * rolls.between(0.35, 0.7);
  const notch = len * 0.12;
  return `M${x.toFixed(1)} ${box.y}L${(x + w).toFixed(1)} ${box.y}`
    + `L${(x + w).toFixed(1)} ${(box.y + len).toFixed(1)}`
    + `L${(x + w / 2).toFixed(1)} ${(box.y + len - notch).toFixed(1)}`
    + `L${x.toFixed(1)} ${(box.y + len).toFixed(1)}Z`;
}

function root(box, rolls) {
  const fromLeft = box.x < 0.5 * W;
  const y0 = box.y + box.h * rolls.between(0.1, 0.6);
  const reach = box.w * rolls.between(0.6, 1.1);
  const x0 = fromLeft ? box.x : box.x + box.w;
  const dir = fromLeft ? 1 : -1;
  const sag = box.h * rolls.between(0.1, 0.3);
  const thick = Math.max(3, box.w * 0.06);
  return `M${x0.toFixed(1)} ${y0.toFixed(1)}`
    + `Q${(x0 + dir * reach * 0.5).toFixed(1)} ${(y0 + sag).toFixed(1)} `
    + `${(x0 + dir * reach).toFixed(1)} ${(y0 + sag * 1.6).toFixed(1)}`
    + `L${(x0 + dir * reach).toFixed(1)} ${(y0 + sag * 1.6 + thick).toFixed(1)}`
    + `Q${(x0 + dir * reach * 0.5).toFixed(1)} ${(y0 + sag + thick).toFixed(1)} `
    + `${x0.toFixed(1)} ${(y0 + thick).toFixed(1)}Z`;
}

function rubble(box, rolls) {
  const chunks = rolls.intBetween(3, 6);
  const base = box.y + box.h;
  let d = '';
  for (let i = 0; i < chunks; i += 1) {
    const w = box.w * rolls.between(0.02, 0.06);
    const h = w * rolls.between(0.4, 1.1);
    const x = box.x + box.w * rolls.take();
    d += `M${x.toFixed(1)} ${base}L${(x + w * 0.3).toFixed(1)} ${(base - h).toFixed(1)}`
      + `L${(x + w).toFixed(1)} ${(base - h * 0.6).toFixed(1)}L${(x + w * 1.2).toFixed(1)} ${base}Z`;
  }
  return d;
}

function step(box, rolls) {
  const lip = box.y + box.h * rolls.between(0.0, 0.25);
  return rect(box.x, lip, box.w, box.y + box.h - lip);
}

const COMPONENTS = {
  crag, tower, skyline,
  pillar, arch, window: windowShape, wall, stair, gate, brazier, stall, chest,
  pillar_edge: pillarEdge, chain, banner, root,
  rubble, step,
};

/* ---------------------------------------------------------------- compose -- */

let SCENES = null;

export function loadScenes(data) {
  SCENES = data;
}

function scaleRect(r) {
  return { x: r.x * W, y: r.y * H, w: r.w * W, h: r.h * H };
}

function svgEl(name, attrs) {
  const node = document.createElementNS(SVG_NS, name);
  Object.entries(attrs || {}).forEach(([k, v]) => node.setAttribute(k, String(v)));
  return node;
}

/** Build the silhouette layers for one scene. Pure: same inputs, same DOM. */
export function composeScene({ scene, biome, modifier, rolls }) {
  const spec = SCENES.scenes[scene];
  if (!spec) return null;
  const biomeSpec = SCENES.biomes[biome] || SCENES.biomes.understone;
  const mod = modifier ? SCENES.modifiers[modifier] : null;
  const cursor = new Rolls(rolls);
  const safe = (spec.safe || []).map(scaleRect);
  const deltas = Object.fromEntries(SCENES.layers.map((l) => [l.id, l.max_delta]));

  const grammar = JSON.parse(JSON.stringify(spec.grammar));
  if (mod && mod.extra) {
    Object.entries(mod.extra).forEach(([layer, entries]) => {
      grammar[layer] = (grammar[layer] || []).concat(entries);
    });
  }
  const suppressed = new Set((mod && mod.suppress) || []);
  const built = [];

  SCENES.layers
    .filter((layer) => layer.kind === 'silhouette' && spec.layers.includes(layer.id))
    .forEach((layer) => {
      const entries = grammar[layer.id] || [];
      if (!entries.length || suppressed.has(layer.id)) return;

      // One SVG per layer, not one per scene. The stack interleaves — haze sits
      // between far and mid, the light shaft between mid and near — and CSS
      // gradients cannot be siblings of SVG groups. mountScene() does the
      // weaving; this only has to keep the layers separable.
      const group = svgEl('svg', {
        viewBox: `0 0 ${W} ${H}`,
        preserveAspectRatio: 'xMidYMid slice',
        'aria-hidden': 'true',
        focusable: 'false',
        class: `scene-svg scene-layer-${layer.id}`,
      });
      entries.forEach((entry) => {
        // Fixed slice: reserve this entry's budget before drawing anything, so
        // one entry can never shift the dice of the next.
        const slice = 2 + 3 * entry.count[1];
        const mark = cursor.i + slice;

        const appears = cursor.take();
        const [low, high] = entry.count;
        const count = cursor.intBetween(low, high);
        if (count > 0 && (entry.weight === undefined || appears <= entry.weight)) {
          const zone = spec.zones[entry.zone] || SCENES.bands[entry.zone];
          const box = scaleRect(zone);
          const builder = COMPONENTS[entry.component];
          for (let i = 0; i < count; i += 1) {
            // Each instance gets its own slot across the zone, so repeats read as
            // a colonnade rather than a pile.
            const slot = count === 1 ? box : {
              x: box.x + (box.w * i) / count,
              y: box.y,
              w: box.w / count,
              h: box.h,
            };
            const scaled = mod && mod.scale
              ? { ...slot, y: slot.y - slot.h * (mod.scale - 1) * 0.5, h: slot.h * mod.scale }
              : slot;
            const d = builder(scaled, cursor, biomeSpec);
            if (!d) continue;
            group.appendChild(svgEl('path', {
              d,
              'fill-rule': 'evenodd',
              fill: fillFor(deltas[layer.id], behindTextFraction(scaled, safe)),
            }));
          }
        }
        cursor.advanceTo(mark);
      });
      if (group.childNodes.length) built.push({ id: layer.id, node: group });
    });

  return { layers: built, spec, biome: biomeSpec, modifier: mod };
}

/* Weave the composed silhouette into a host that already holds the atmosphere
 * elements, in the order content/scenes.json declares.
 *
 * The rule is one line: a silhouette layer goes immediately before the first
 * atmosphere element declared after it. That is what makes the stack real rather
 * than decorative — fog genuinely sits between the distance and the architecture,
 * and the light shaft genuinely falls behind the foreground. It lives here, and
 * not at each call site, so the gallery and the client cannot stack the same
 * scene two different ways. */
export function mountScene(host, composed) {
  host.querySelectorAll('.scene-svg').forEach((node) => node.remove());
  if (!composed) return;
  const order = SCENES.layers.map((l) => l.id);
  composed.layers.forEach(({ id, node }) => {
    const after = order.slice(order.indexOf(id) + 1)
      .map((next) => host.querySelector(`.sc-${next}`))
      .find(Boolean);
    host.insertBefore(node, after || null);
  });
}

/* Atmosphere is CSS, not SVG: gradients belong in the stylesheet where the theme
   tokens live, and a gradient does not need geometry. The composer only tells the
   stylesheet where the light is and how far it reaches. */
export function applyAtmosphere(host, { scene, biome, modifier }) {
  const spec = SCENES.scenes[scene];
  if (!spec) return;
  const biomeSpec = SCENES.biomes[biome] || SCENES.biomes.understone;
  const mod = modifier ? SCENES.modifiers[modifier] : null;
  const light = spec.light;

  host.style.setProperty('--sc-light-x', `${(light.x * 100).toFixed(1)}%`);
  host.style.setProperty('--sc-light-y', `${(light.y * 100).toFixed(1)}%`);
  host.style.setProperty('--sc-light-spread', String(light.spread * (mod ? mod.light || 1 : 1)));
  host.style.setProperty('--sc-tint', `var(${biomeSpec.tint})`);
  host.style.setProperty('--sc-tint-pct', `${biomeSpec.tint_pct}%`);
  host.dataset.scene = scene;
  host.dataset.biome = biome;
  const layers = new Set(spec.layers);
  ['void', 'haze', 'shaft', 'glow', 'vignette'].forEach((layer) => {
    host.classList.toggle(`has-${layer}`, layers.has(layer));
  });
}

/** Which scene a screen is standing in. */
export function sceneFor(screen, room) {
  // The room's kind refines the room screen and only the room screen. The deck
  // and badges views are not places you are standing in, so an open room must
  // not leak its scene into them.
  if (screen === 'room' && room) {
    const byKind = SCENES.screens[`room.${room.kind}`] || SCENES.screens[room.kind];
    if (byKind) return byKind;
  }
  return SCENES.screens[screen] || SCENES.screens.map;
}

export function biomeForAct(act) {
  const entries = Object.entries(SCENES.biomes).filter(([k]) => k !== '_comment');
  const exact = entries.find(([, b]) => b.act === act);
  if (exact) return exact[0];
  // The climb is unbounded; everything past the last named act is the endless one.
  return entries[entries.length - 1][0];
}

export const SCENE_FRAME = { W, H };
