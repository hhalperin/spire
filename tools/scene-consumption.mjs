#!/usr/bin/env node
/* Does every scene consume exactly the roll budget the engine ships it?
 *
 *   node tools/scene-consumption.mjs
 *
 * `mapgen.scene_budget` sizes each scene's float vector from the full grammar,
 * because the budget belongs to the *scene* — a modifier changes what is drawn,
 * never how much of the stream is walked. If the composer consumes a different
 * amount, "same seed, same picture" stops being true: every layer after the
 * mismatch reads floats meant for something else, and the result still looks
 * plausible, which is why the drift went unnoticed.
 *
 * `composeScene` needs only `document.createElementNS`, so this runs it against
 * a counting cursor under a shim rather than a browser. Exit 1 on any drift.
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

/* The composer only ever builds SVG nodes and sets attributes on them. */
function stubNode() {
  const node = {
    childNodes: [],
    attrs: {},
    setAttribute(k, v) { node.attrs[k] = v; },
    appendChild(child) { node.childNodes.push(child); return child; },
  };
  return node;
}
globalThis.document = { createElementNS: () => stubNode() };

const { loadScenes, composeScene } = await import(join(ROOT, 'app', 'scene.js'));
const SCENES = JSON.parse(readFileSync(join(ROOT, 'content', 'scenes.json'), 'utf8'));
loadScenes(SCENES);

/* The path data one layer drew, as a comparable string. */
function shapesOf(composed, layerId) {
  const layer = (composed.layers || []).find((l) => l.id === layerId);
  if (!layer) return '';
  return layer.node.childNodes.map((n) => n.attrs.d || '').join('|');
}

const failures = [];

for (const [name, spec] of Object.entries(SCENES.scenes)) {
  if (name.startsWith('_')) continue;

  // A vector long enough that nothing wraps, so consumption is measurable.
  const budget = 4096;
  const rolls = Array.from({ length: budget }, (_, i) => ((i * 37) % 1000) / 1000);

  const runs = [];
  const modifiers = [null, ...Object.keys(SCENES.modifiers).filter((m) => !m.startsWith('_'))];

  for (const modifier of modifiers) {
    const mod = modifier ? SCENES.modifiers[modifier] : null;
    // Only modifiers that touch a layer this scene actually has are meaningful.
    const suppresses = new Set((mod && mod.suppress) || []);
    const touches = [...suppresses].some((l) => spec.layers.includes(l));
    if (modifier && !touches) continue;

    const composed = composeScene({ scene: name, biome: 'depths', modifier, rolls });
    runs.push({ modifier: modifier || '(none)', used: composed.consumed });
  }

  const base = runs[0];
  for (const run of runs.slice(1)) {
    if (run.used !== base.used) {
      failures.push(
        `${name}: ${run.modifier} walks ${run.used} floats, base walks ${base.used} `
        + `— a modifier must not change how much of the stream is consumed`,
      );
    }
  }
  process.stderr.write(`  ${name}: ${base.used} floats, stable across ${runs.length} modifier(s)\n`);

  /* Length is not enough — that check passed while `elite` still spliced its
     extras into the near layer mid-stream. `mapgen.scene_budget` sums the base
     grammar and *appends* the worst modifier's extras, so the composer has to
     walk them at the tail. Splicing them into their layer pushed every later
     layer along, and the same node drawn as an elite read a different floor
     from the same node drawn plain. This pins where the extras start. */
  const baseSlices = (() => {
    let total = 0;
    for (const layer of SCENES.layers) {
      if (layer.kind !== 'silhouette' || !spec.layers.includes(layer.id)) continue;
      for (const entry of spec.grammar[layer.id] || []) {
        const cost = (SCENES.components[entry.component] || {}).rolls || 0;
        total += 2 + cost * entry.count[1];
      }
    }
    return total;
  })();

  for (const modifier of modifiers) {
    const mod = modifier ? SCENES.modifiers[modifier] : null;
    if (!mod || !mod.extra) continue;
    const touched = Object.keys(mod.extra).filter((l) => spec.layers.includes(l));
    if (!touched.length) continue;

    const composed = composeScene({ scene: name, biome: 'depths', modifier, rolls });
    if (composed.consumedBase !== baseSlices) {
      failures.push(
        `${name}: under ${modifier} the extras begin at float ${composed.consumedBase}, `
        + `but the base grammar ends at ${baseSlices} — extras must sit at the tail, `
        + 'where mapgen.scene_budget reserves them',
      );
    }
  }
}

if (failures.length) {
  process.stderr.write('\nscene consumption drift:\n');
  failures.forEach((f) => process.stderr.write(`  ✗ ${f}\n`));
  process.exit(1);
}
process.stderr.write('scenes: every modifier walks the same stream length\n');
