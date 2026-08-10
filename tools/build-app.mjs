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
 * No bundler. app/ is three files and a stylesheet — reaching for a toolchain
 * to concatenate them would add a build step the rest of this repo does not have.
 */

import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const APP = join(ROOT, 'app');
const OUT = join(ROOT, 'server', 'assets', 'app.html');

const read = (name) => readFileSync(join(APP, name), 'utf8');

const html = read('index.html');
const fonts = read('fonts.css');
const css = read('app.css');

/* bridge.js and app.js are ES modules split for readability, not for loading.
   Concatenating them into one module is exactly what a bundler would do, minus
   the bundler: drop bridge's `export` keywords and app's matching import. */
const bridge = read('bridge.js').replace(/^export\s+/gm, '');
const app = read('app.js').replace(/^import\s+\{[^}]*\}\s+from\s+'\.\/bridge\.js';\s*$/m, '');

const script = `${bridge}\n\n/* ---- app.js ---- */\n${app}`;

let out = html
  .replace(
    /<link rel="stylesheet" href="fonts\.css" \/>\s*<link rel="stylesheet" href="app\.css" \/>/,
    `<style>\n${fonts}\n</style>\n  <style>\n${css}\n  </style>`,
  )
  .replace(
    /<script type="module" src="app\.js"><\/script>/,
    `<script type="module">\n${script}\n</script>`,
  );

/* A missed replacement would silently ship a document that fetches nothing and
   renders blank, so verify rather than hope. */
const problems = [];
if (out.includes('href="app.css"')) problems.push('app.css was not inlined');
if (out.includes('src="app.js"')) problems.push('app.js was not inlined');
if (out.includes('href="fonts.css"')) problems.push('fonts.css was not inlined');
if (/https?:\/\/fonts\./.test(out)) problems.push('a font CDN reference survived');
if (!out.includes('ui/initialize')) problems.push('the MCP Apps handshake is missing');
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
  + `(fonts ${kb(fonts.length)}, css ${kb(css.length)}, js ${kb(script.length)})`,
);
