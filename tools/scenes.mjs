#!/usr/bin/env node
/* The scene gallery: every wireframe, and what each one composes into.
 *
 *   node tools/scenes.mjs [--out docs/scenes.html] [--seed 0] [--open]
 *
 * Two sections, and the relationship between them is the point of the whole
 * system. The first draws each scene's declared zones and safe areas straight
 * from content/scenes.json. The second composes the same scene through
 * app/scene.js for every act. They cannot disagree, because the wireframe is not
 * a drawing *of* the data — it is a drawing *from* it, and so is the art.
 *
 * Rolls come from `python3 scripts/mapgen.py scene-rolls`, which is the same
 * function scripts/run.py ships to the client. The gallery composes from the
 * exact numbers the engine would send, not from lookalikes generated here.
 *
 * Output is one self-contained HTML file, for the same reason the client is:
 * it has to survive being opened from disk with no server.
 */

import { spawnSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const flag = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback;
};

const SEED = Number(flag('seed', '0'));
// resolve, not join: an absolute --out must land where it was asked to,
// not underneath the repo.
const OUT = resolve(ROOT, flag('out', 'docs/scenes.html'));

const read = (p) => readFileSync(join(ROOT, p), 'utf8');
const scenes = JSON.parse(read('content/scenes.json'));

/* ------------------------------------------------------------------ rolls -- */

const python = process.env.SPIRE_PYTHON || 'python3';

function rollsFor(act, scene) {
  const result = spawnSync(python, [
    join(ROOT, 'scripts', 'mapgen.py'), 'scene-rolls',
    '--seed', String(SEED), '--act', String(act), '--scene', scene,
  ], { encoding: 'utf8' });
  if (result.status !== 0) {
    throw new Error(`scene-rolls failed for ${scene} act ${act}: ${result.stderr}`);
  }
  return JSON.parse(result.stdout).rolls;
}

const biomes = Object.entries(scenes.biomes).filter(([k]) => k !== '_comment');
const sceneNames = Object.keys(scenes.scenes);

/* ------------------------------------------------------------- roll audit -- */

/* Every component declares how many floats it draws, and `scripts/run.py` sizes
 * each scene's vector from those declarations. Only this file can check the
 * declaration against the truth, because the truth is eighteen JavaScript path
 * builders. So: run each one against a counting cursor, at the extremes of every
 * range it can be handed, and fail if it ever draws more than it promised.
 *
 * The composer fences each entry into its slice, so an under-declaration would
 * not corrupt a neighbour — it would make one component silently repeat itself.
 * That is a bug you would never notice by looking, which is what a check is for.
 */
const source = readFileSync(join(ROOT, 'app', 'scene.js'), 'utf8');
const module_ = await import(`data:text/javascript,${encodeURIComponent(
  `${source.replace(/^import .*$/gm, '')}\nexport { COMPONENTS };`,
)}`);

const overdrawn = [];
for (const [name, spec] of Object.entries(scenes.components)) {
  if (name.startsWith('_')) continue;
  const builder = module_.COMPONENTS[name];
  if (!builder) { overdrawn.push(`${name}: declared but no builder`); continue; }

  let worst = 0;
  // Sweep the unit interval: which branch a builder takes, and how many times it
  // loops, are both decided by the floats it is given, so the worst case lives at
  // one of the extremes rather than at a midpoint.
  for (const fill of [0, 0.001, 0.25, 0.5, 0.75, 0.999]) {
    for (const [, biome] of biomes) {
      let drawn = 0;
      const cursor = { take: () => { drawn += 1; return fill; } };
      cursor.between = (lo, hi) => lo + cursor.take() * (hi - lo);
      cursor.intBetween = (lo, hi) => (hi <= lo ? lo
        : Math.min(hi, lo + Math.floor(cursor.take() * (hi - lo + 1))));
      cursor.pick = (list) => (list && list.length
        ? list[Math.min(list.length - 1, Math.floor(cursor.take() * list.length))] : null);
      builder({ x: 0, y: 0, w: 400, h: 300 }, cursor, biome);
      worst = Math.max(worst, drawn);
    }
  }
  if (worst > spec.rolls) {
    overdrawn.push(`${name}: declares ${spec.rolls} rolls, draws up to ${worst}`);
  }
}

if (overdrawn.length) {
  process.stderr.write(`\nscenes: component roll costs are wrong:\n`);
  overdrawn.forEach((line) => process.stderr.write(`  ✗ ${line}\n`));
  process.stderr.write('Fix content/scenes.json → components.*.rolls, then re-run.\n');
  process.exit(1);
}
console.error(`scenes: ${Object.keys(scenes.components).length - 1} components draw`
  + ' no more than they declare');

/* Pre-roll everything up front so the page needs no runtime process access. */
const rolls = {};
for (const name of sceneNames) {
  rolls[name] = {};
  for (const [, biome] of biomes) {
    rolls[name][biome.act] = rollsFor(biome.act, name);
  }
}

/* ------------------------------------------------------------------ page -- */

/* Only the token blocks from app.css — the gallery needs the palette but not the
   client's layout, and pulling the whole stylesheet in would style the gallery
   like the app and hide what the scenes actually look like. */
const appCss = read('app/app.css');
const tokens = appCss.slice(0, appCss.indexOf('/* ------------------------------------------------------------------ base -- */'));
const sceneCss = read('app/scene.css');
const sceneJs = read('app/scene.js')
  .replace(/^export /gm, '')
  .replace(/^import .*$/gm, '');

const html = `<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Spire — scene wireframes</title>
<style>
${tokens}
${sceneCss}

body {
  margin: 0; background: var(--stone-900); color: var(--ink);
  font: 15px/1.6 ui-sans-serif, system-ui, sans-serif;
}
.page { max-width: 1240px; margin: 0 auto; padding: 32px 24px 80px; }
h1 { font-size: 30px; margin: 0 0 6px; letter-spacing: -.01em; }
h2 {
  font-size: 12px; letter-spacing: .18em; text-transform: uppercase;
  color: var(--ember); margin: 52px 0 6px;
}
h3 { font-size: 17px; margin: 0 0 2px; }
p.lede { color: var(--ink-soft); max-width: 68ch; margin: 0 0 8px; }
p.note { color: var(--muted); font-size: 13px; max-width: 72ch; margin: 0 0 20px; }
code { font-family: ui-monospace, monospace; font-size: .88em; color: var(--ember-hot); }

.grid { display: grid; gap: 22px; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); }
.tile { border: 1px solid var(--line); border-radius: 10px; overflow: hidden; background: var(--stone-800); }
.stage {
  position: relative; aspect-ratio: 10 / 7; overflow: hidden;
  container-type: size;
}
/* #scene is position:fixed in the client because it is the page background.
   Inside a tile it has to be contained instead. */
.stage #scene { position: absolute; }
.meta { padding: 11px 14px 13px; border-top: 1px solid var(--line); }
.meta .about { color: var(--muted); font-size: 12.5px; margin-top: 3px; }
.chips { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; }
.chip {
  font: 10.5px/1.5 ui-monospace, monospace; border: 1px solid var(--line);
  border-radius: 3px; padding: 1px 6px; color: var(--ink-soft);
}
.chip.sil { border-color: color-mix(in srgb, var(--ember) 55%, transparent); color: var(--ember-text); }
.chip.atm { border-color: color-mix(in srgb, var(--steel) 55%, transparent); color: var(--steel); }

.legend { display: flex; flex-wrap: wrap; gap: 14px; margin: 14px 0 26px; font-size: 12.5px; color: var(--muted); }
.swatch { display: inline-block; width: 14px; height: 14px; vertical-align: -2px; margin-right: 5px; border-radius: 2px; }
.sw-zone { border: 1px solid var(--facet-map); }
.sw-safe { border: 1px dashed var(--moss); background: repeating-linear-gradient(-45deg, transparent, transparent 3px, color-mix(in srgb, var(--moss) 22%, transparent) 3px, color-mix(in srgb, var(--moss) 22%, transparent) 6px); }

.acts { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
.acts button {
  font: 12px/1.5 ui-sans-serif, system-ui, sans-serif; background: var(--stone-700);
  border: 1px solid var(--line); border-radius: 5px; color: var(--ink-soft);
  padding: 5px 11px; cursor: pointer;
}
.acts button[aria-pressed='true'] { border-color: var(--ember); color: var(--ink); }
</style>
</head>
<body>
<div class="page">
  <h1>Scene wireframes</h1>
  <p class="lede">
    Every place the client can put you, declared as data and composed from it.
    <code>content/scenes.json</code> is not documentation about these backgrounds —
    it is their source. The wireframes below are drawn from the same object the
    composer reads, so the two cannot drift apart.
  </p>
  <p class="note">
    Seed ${SEED}. Rolls come from <code>scripts/mapgen.py scene-rolls</code>, the
    same function the engine ships to the client, so what you see here is what a
    run at this seed would actually render.
  </p>

  <h2>The wireframe</h2>
  <p class="note">
    Zones are where components may be placed. Safe areas are where text lives —
    silhouette crossing one has its contrast attenuated to
    ${Math.round(scenes.contrast.safe_attenuation * 100)}% rather than being
    clipped, because in a UI this text-dense there is nowhere to draw around the
    content.
  </p>
  <div class="legend">
    <span><i class="swatch sw-zone"></i>zone</span>
    <span><i class="swatch sw-safe"></i>safe area (text lives here)</span>
  </div>
  <div class="grid" id="wireframes"></div>

  <h2>Composed</h2>
  <p class="note">
    The same scenes, run through <code>app/scene.js</code>. The act supplies the
    biome — palette and material — and the scene supplies the structure, so one
    grammar produces a different world per act.
  </p>
  <div class="acts" id="acts"></div>
  <div class="grid" id="composed"></div>
</div>

<script type="module">
const SCENES_DATA = ${JSON.stringify(scenes)};
const ROLLS = ${JSON.stringify(rolls)};

${sceneJs}

loadScenes(SCENES_DATA);

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

function layerChips(spec) {
  const kinds = Object.fromEntries(SCENES_DATA.layers.map((l) => [l.id, l.kind]));
  const wrap = el('div', 'chips');
  spec.layers.forEach((id) => {
    wrap.appendChild(el('span', 'chip ' + (kinds[id] === 'silhouette' ? 'sil' : 'atm'), id));
  });
  return wrap;
}

function tile(name, spec, body, subtitle) {
  const card = el('div', 'tile');
  const stage = el('div', 'stage');
  stage.appendChild(body);
  card.appendChild(stage);
  const meta = el('div', 'meta');
  meta.appendChild(el('h3', null, name + (subtitle ? '  ·  ' + subtitle : '')));
  meta.appendChild(el('div', 'about', spec.about));
  meta.appendChild(layerChips(spec));
  card.appendChild(meta);
  return card;
}

/* ---- the wireframe: zones and safe areas, straight from the JSON ---- */
function wireframe(name, spec) {
  const host = el('div', 'wireframe');
  host.id = 'scene';
  host.classList.add('wireframe');

  (spec.safe || []).forEach((r, i) => {
    const box = el('div', 'sc-safe', 'safe' + (spec.safe.length > 1 ? '[' + i + ']' : ''));
    Object.assign(box.style, {
      left: (r.x * 100) + '%', top: (r.y * 100) + '%',
      width: (r.w * 100) + '%', height: (r.h * 100) + '%',
    });
    host.appendChild(box);
  });

  // Which components each zone can receive — the grammar, shown in place.
  const contents = {};
  Object.entries(spec.grammar).forEach(([layer, entries]) => {
    entries.forEach((e) => {
      (contents[e.zone] = contents[e.zone] || []).push(
        e.component + '×' + (e.count[0] === e.count[1] ? e.count[0] : e.count.join('-')));
    });
  });

  Object.entries(spec.zones).forEach(([zoneName, r]) => {
    const box = el('div', 'sc-zone');
    box.appendChild(el('div', null, zoneName));
    (contents[zoneName] || []).forEach((c) => {
      const line = el('div', null, c);
      line.style.opacity = '.7';
      box.appendChild(line);
    });
    Object.assign(box.style, {
      left: (r.x * 100) + '%', top: (r.y * 100) + '%',
      width: (r.w * 100) + '%', height: (r.h * 100) + '%',
    });
    host.appendChild(box);
  });

  // The light source, so the wireframe says where the warm pool comes from.
  const light = el('div');
  Object.assign(light.style, {
    position: 'absolute', width: '14px', height: '14px', borderRadius: '50%',
    left: 'calc(' + (spec.light.x * 100) + '% - 7px)',
    top: 'calc(' + (spec.light.y * 100) + '% - 7px)',
    border: '2px solid var(--ember)',
    boxShadow: '0 0 18px -2px var(--ember)',
  });
  light.title = 'light source';
  host.appendChild(light);
  return host;
}

const wf = document.getElementById('wireframes');
Object.entries(SCENES_DATA.scenes).forEach(([name, spec]) => {
  wf.appendChild(tile(name, spec, wireframe(name, spec)));
});

/* ---- composed ---- */
const BIOMES = Object.entries(SCENES_DATA.biomes).filter(([k]) => k !== '_comment');
let activeAct = 1;

function composedHost(name, spec, biomeKey, biome) {
  const host = el('div');
  host.id = 'scene';
  ['void', 'haze', 'shaft', 'glow', 'vignette'].forEach((layer) => {
    const node = el('div', 'sc-' + layer);
    host.appendChild(node);
  });
  // mountScene weaves the silhouette layers between the atmosphere ones, using
  // the order content/scenes.json declares. The gallery calls the same function
  // the client does, so a stack that looks right here looks right there.
  mountScene(host, composeScene({
    scene: name, biome: biomeKey, modifier: null,
    rolls: ROLLS[name][biome.act],
  }));
  applyAtmosphere(host, { scene: name, biome: biomeKey, modifier: null });
  // The facet tint the client would be showing on this screen.
  const screen = Object.entries(SCENES_DATA.screens)
    .find(([k, v]) => k !== '_comment' && v.scene === name);
  host.style.setProperty('--facet', 'var(--facet-' + (screen ? screen[0].split('.')[0] : 'map') + ', var(--facet-map))');
  return host;
}

function renderComposed() {
  const grid = document.getElementById('composed');
  grid.innerHTML = '';
  const [biomeKey, biome] = BIOMES.find(([, b]) => b.act === activeAct) || BIOMES[0];
  Object.entries(SCENES_DATA.scenes).forEach(([name, spec]) => {
    grid.appendChild(tile(name, spec, composedHost(name, spec, biomeKey, biome), biome.name));
  });
}

const actBar = document.getElementById('acts');
BIOMES.forEach(([key, biome]) => {
  const btn = el('button', null, 'Act ' + biome.act + ' · ' + biome.name);
  btn.setAttribute('aria-pressed', String(biome.act === activeAct));
  btn.addEventListener('click', () => {
    activeAct = biome.act;
    [...actBar.children].forEach((c, i) =>
      c.setAttribute('aria-pressed', String(BIOMES[i][1].act === activeAct)));
    renderComposed();
  });
  actBar.appendChild(btn);
});
renderComposed();

window.__scenes = { SCENES_DATA, ROLLS, composeScene, renderComposed };
</script>
</body>
</html>
`;

mkdirSync(dirname(OUT), { recursive: true });
writeFileSync(OUT, html, 'utf8');
console.error(
  `scenes: wrote ${OUT.replace(`${ROOT}/`, '')} — `
  + `${sceneNames.length} scenes × ${biomes.length} acts, ${Math.round(html.length / 1024)}K`,
);
