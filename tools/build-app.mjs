#!/usr/bin/env node
/* Bundle app/ into a single self-contained document.
 *
 *   node tools/build-app.mjs
 *
 * Output: server/assets/app.html, which src/server.rs `include_str!`s into the
 * binary. Compiling it in rather than reading it at runtime means the HTML a
 * host fetches can never drift from the server that advertised it.
 *
 * Self-contained is not a nicety here. An MCP App renders in a sandboxed iframe
 * under a deny-by-default CSP; every external origin has to be declared in
 * `_meta.ui.csp` and hosts may still refuse it. One file with zero external
 * references is the only version that renders identically everywhere.
 *
 * No bundler. app/ is a handful of files and two stylesheets — reaching for a
 * toolchain to concatenate them would add a build step the rest of this repo
 * does not have.
 */

import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const APP = join(ROOT, 'app');
const OUT = join(ROOT, 'server', 'assets', 'app.html');

/* The bundle must stay small enough to hand a host in one resource read. Fonts
   are 186K of it and are not going to shrink, so this ceiling is really a budget
   on everything else — it exists so the scene system cannot quietly grow into a
   megabyte of generated paths. */
const CEILING = 340 * 1024;

const read = (name) => readFileSync(join(APP, name), 'utf8');

const html = read('index.html');
const fonts = read('fonts.css');
const sceneCss = read('scene.css');
const css = read('app.css');

/* content/scenes.json is the wireframe *and* the art — app/scene.js composes
   from it and tools/scenes.mjs draws its labelled view from the same object.
   Inlining it rather than fetching it is what keeps the bundle single-origin;
   re-parsing it here would just be a slower way to copy bytes, so it goes in
   verbatim as a JSON literal the module reads once. */
const scenes = readFileSync(join(ROOT, 'content', 'scenes.json'), 'utf8');

/* bridge.js, scene.js and app.js are ES modules split for readability, not for
   loading. Concatenating them into one module is exactly what a bundler would
   do, minus the bundler: drop the `export` keywords and app's matching imports. */
const stripExports = (src) => src.replace(/^export\s+/gm, '');
const bridge = stripExports(read('bridge.js'));
const scene = stripExports(read('scene.js'));
const app = read('app.js')
  .replace(/^import\s+\{[^}]*\}\s+from\s+'\.\/bridge\.js';\s*$/m, '')
  .replace(/^import\s+\{[\s\S]*?\}\s+from\s+'\.\/scene\.js';\s*$/m, '');

const script = [
  `/* ---- content/scenes.json ---- */\nconst SPIRE_SCENES = ${scenes.trim()};`,
  `/* ---- bridge.js ---- */\n${bridge}`,
  `/* ---- scene.js ---- */\n${scene}`,
  `/* ---- app.js ---- */\n${app}`,
].join('\n\n');

/* Three modules sharing one scope means two files can now declare the same
   top-level name, and `const x` twice is a SyntaxError that blanks the whole
   client with nothing in the DOM to say why. Separate files never collide, so
   the collision is invisible until the bundle runs — which is exactly the kind
   of failure to catch at build time. */
const topLevel = (src) => {
  const names = new Set();
  const re = /^(?:async\s+)?(?:const|let|var|function|class)\s+([A-Za-z_$][\w$]*)/gm;
  let match;
  while ((match = re.exec(src))) names.add(match[1]);
  return names;
};
const declared = new Map();
for (const [name, src] of [['bridge.js', bridge], ['scene.js', scene], ['app.js', app]]) {
  for (const id of topLevel(src)) {
    if (declared.has(id)) {
      console.error(`build-app: ${name} redeclares '${id}' from ${declared.get(id)}`);
      process.exit(1);
    }
    declared.set(id, name);
  }
}
if (declared.has('SPIRE_SCENES')) {
  console.error("build-app: a module declares 'SPIRE_SCENES', which the prelude owns");
  process.exit(1);
}

let out = html
  .replace(
    /<link rel="stylesheet" href="fonts\.css" \/>\s*<link rel="stylesheet" href="scene\.css" \/>\s*<link rel="stylesheet" href="app\.css" \/>/,
    `<style>\n${fonts}\n</style>\n  <style>\n${sceneCss}\n  </style>\n  <style>\n${css}\n  </style>`,
  )
  .replace(
    /<script type="module" src="app\.js"><\/script>/,
    `<script type="module">\n${script}\n</script>`,
  );

/* A missed replacement would silently ship a document that fetches nothing and
   renders blank, so verify rather than hope. */
const problems = [];
if (out.includes('href="app.css"')) problems.push('app.css was not inlined');
if (out.includes('href="scene.css"')) problems.push('scene.css was not inlined');
if (out.includes('src="app.js"')) problems.push('app.js was not inlined');
if (out.includes('href="fonts.css"')) problems.push('fonts.css was not inlined');
if (/^\s*import\s/m.test(script)) problems.push('an ES import survived the concatenation');
if (/https?:\/\/fonts\./.test(out)) problems.push('a font CDN reference survived');
if (!out.includes('ui/initialize')) problems.push('the MCP Apps handshake is missing');
if (!out.includes('SPIRE_SCENES')) problems.push('the scene data was not inlined');
if (out.length > CEILING) {
  problems.push(`bundle is ${Math.round(out.length / 1024)}K, over the ${CEILING / 1024}K ceiling`);
}
if (problems.length) {
  console.error(`build-app: ${problems.join('; ')}`);
  process.exit(1);
}

out = `${out.trimEnd()}\n`;
mkdirSync(dirname(OUT), { recursive: true });
writeFileSync(OUT, out, 'utf8');

const kb = (n) => `${Math.round(n / 1024)}K`;
console.error(
  `build-app: wrote server/assets/app.html — ${kb(out.length)} `
  + `(fonts ${kb(fonts.length)}, css ${kb(css.length + sceneCss.length)}, `
  + `js ${kb(script.length - scenes.length)}, scenes ${kb(scenes.length)})`,
);
