#!/usr/bin/env node
/* Drive the real client through a whole run and photograph every screen.
 *
 *   node tools/shoot.mjs [--out docs/screenshots] [--port 8933]
 *
 * The goal is not pretty pictures. It is that "validate the interface through
 * demos" means something checkable: this walks the shipping client — served by
 * the real MCP server, over the real postMessage protocol — through map, room,
 * combat, reward, campfire, shop, deck and badges, and fails the run if a screen
 * does not appear or a required element is missing from it.
 *
 * Three passes, because the client has to survive all three:
 *   dark      — the default, what most hosts show
 *   light     — the host said light, or the OS did
 *   greyscale — ENTITY_STANDARDS rule 1: silhouette before colour. If a screen
 *               is unreadable here, the design leans on hue and needs fixing.
 *
 * Uses the preinstalled Chromium at PLAYWRIGHT_BROWSERS_PATH; never downloads.
 */

import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const flag = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback;
};

const PORT = Number(flag('port', '8933'));
const OUT = join(ROOT, flag('out', 'docs/screenshots'));
const BASE = `http://localhost:${PORT}`;

const failures = [];
const shots = [];

/* ------------------------------------------------------------------ host -- */

/* The client is compiled into the server binary, so a stale binary silently
   photographs the previous build. Rebuild both before shooting anything. */
for (const [cmd, argv] of [
  ['node', [join(ROOT, 'tools', 'build-app.mjs')]],
  ['cargo', ['build', '--release', '--quiet', '--manifest-path', join(ROOT, 'server', 'Cargo.toml')]],
]) {
  const built = spawnSync(cmd, argv, { stdio: ['ignore', 'ignore', 'inherit'] });
  if (built.status !== 0) {
    process.stderr.write(`shoot: ${cmd} failed; cannot photograph a stale build\n`);
    process.exit(1);
  }
}

const host = spawn('node', [join(ROOT, 'tools', 'host', 'serve.mjs'), '--port', String(PORT)], {
  stdio: ['ignore', 'ignore', 'pipe'],
});
host.stderr.on('data', (d) => {
  const line = d.toString().trim();
  if (line && !line.startsWith('spire demo host') && !line.startsWith('  ')) {
    process.stderr.write(`host: ${line}\n`);
  }
});
const stopHost = () => { try { host.kill(); } catch { /* already gone */ } };
process.on('exit', stopHost);

await new Promise((resolve, reject) => {
  const deadline = Date.now() + 20000;
  const poll = async () => {
    try {
      const res = await fetch(`${BASE}/app.html`);
      if (res.ok) return resolve();
    } catch { /* not up yet */ }
    if (Date.now() > deadline) return reject(new Error('demo host did not start'));
    setTimeout(poll, 200);
  };
  poll();
});

/* --------------------------------------------------------------- browser -- */

/* The environment preinstalls Chromium at a fixed path; the npm playwright
   version may not match its build number, so point at it explicitly rather than
   letting the launcher hunt for a build it thinks it needs. Never download. */
const EXECUTABLE = process.env.SPIRE_CHROMIUM
  || (existsSync('/opt/pw-browsers/chromium') ? '/opt/pw-browsers/chromium' : undefined);
const browser = await chromium.launch(
  EXECUTABLE ? { executablePath: EXECUTABLE, args: ['--no-sandbox'] } : { args: ['--no-sandbox'] },
);

/* Everything below runs inside the iframe — that is the shipping client. */
const view = (page) => page.frameLocator('#view');
const inside = (page, fn, arg) => page.frame({ url: /app\.html/ }).evaluate(fn, arg);

async function newPage(theme, width) {
  const context = await browser.newContext({
    viewport: { width, height: 900 },
    deviceScaleFactor: 1,
    colorScheme: theme === 'light' ? 'light' : 'dark',
  });
  const page = await context.newPage();
  page.on('pageerror', (err) => failures.push(`page error: ${err.message}`));
  page.on('console', (msg) => {
    if (msg.type() === 'error') failures.push(`console: ${msg.text()}`);
  });
  await page.goto(BASE, { waitUntil: 'networkidle' });
  await page.addStyleTag({ content: '#log { display: none !important; }' });
  await page.selectOption('#theme', theme === 'light' ? 'light' : 'dark');
  await page.waitForFunction(() => window.__spireHostReady === true, { timeout: 15000 });
  await view(page).locator('#stage .screen').first().waitFor({ timeout: 15000 });
  await settled(page);
  return page;
}

/* Wait until the client is actually idle, not merely alive.
 *
 * `__spireHostReady` is set on `ui/notifications/initialized`, which the client
 * sends immediately after `ui/initialize` — before its boot sequence has run
 * `spire_get_run` and `spire_map_refresh`. Driving a tool in that window hits
 * `ui.busy` and `callTool` returns `null` **without throwing**, so the harness
 * carried on against whatever state boot happened to leave behind. A flaky
 * screenshot run is bad; one that silently photographs the wrong run is worse. */
async function settled(page) {
  // `__spire` lives inside the iframe — the client is the frame, not the host
  // page — so this has to wait in the frame's context, like `inside` does.
  const frame = page.frame({ url: /app\.html/ });
  if (!frame) return;
  await frame.waitForFunction(
    () => window.__spire && window.__spire.ui.busy === false,
    { timeout: 15000 },
  );
}

/* Call a tool from inside the client and refuse to continue unless it landed.
 *
 * Two ways a call fails to take effect, and both used to pass silently:
 * `callTool` returns null when `ui.busy` is held, and the engine answers
 * `ok: false` when it refuses. The callers here resolve to `r && r.ok`, so a
 * refusal arrives as `false` — which an `=== null` check waves straight
 * through. A walk that continued on the wrong climb still produced a full set
 * of screenshots and exit 0.
 *
 * So: anything falsy is a failure. A harness that swallows the refusals it
 * exists to catch is the same mistake as one that supplies the step it is
 * meant to be testing.
 *
 * And it *throws*, rather than noting the failure and carrying on. Recording a
 * failure while continuing meant the rest of the pass ran against the wrong
 * climb and photographed it anyway — a full set of plausible screenshots for a
 * run that never started. Each pass catches, so one bad pass does not cost the
 * others their coverage. */
async function drive(page, label, fn, arg) {
  await settled(page);
  const landed = await inside(page, fn, arg);
  if (!landed) {
    const why = `${label}: tool call did not land (dropped or refused)`;
    failures.push(why);
    throw new Error(why);
  }
  await settled(page);
  return landed;
}

async function shoot(page, name, opts = {}) {
  await page.waitForTimeout(opts.settle ?? 260);
  /* JPEG at 1x. These are committed, and 32 lossless 2x captures came to 9MB —
     more repository weight than a gallery is worth. The harness is the artifact;
     the images are its receipt. */
  const file = join(OUT, `${name}.jpg`);
  const target = opts.full ? page : page.locator('#view');
  await target.screenshot({ path: file, type: 'jpeg', quality: 86 });
  shots.push(name);
  process.stderr.write(`  ✓ ${name}\n`);
}

/** Assert a screen rendered what its format doc promises. */
async function expect(page, label, checks) {
  for (const [what, locator] of Object.entries(checks)) {
    const count = await view(page).locator(locator).count();
    if (count === 0) failures.push(`${label}: missing ${what} (${locator})`);
  }
}

/* ----------------------------------------------------------------- scenes -- */

const SCENES = JSON.parse(readFileSync(join(ROOT, 'content', 'scenes.json'), 'utf8'));
const CEILING = SCENES.contrast.legibility_ceiling;

/* Measure the composed background where text lives, on real pixels.
 *
 * The composer keeps backgrounds legible by bounding how far each layer may step
 * from the void and attenuating that step behind a safe rectangle. This is the
 * check that the bound survives contact with a browser — blend modes, gradients,
 * theme inversion and all — rather than only holding in the arithmetic.
 *
 * What it measures is the *local* step: the largest luminance jump between
 * neighbouring samples on an 8px grid inside each safe rectangle. Local is the
 * right scale, because text contrast is local. A slow wash across the whole
 * frame does not hurt a paragraph; a silhouette edge running under one does.
 *
 * The browser does the PNG decoding — a blank page with an <img> and a canvas —
 * so this needs no image library and no new dependency.
 */
async function sceneContrast(page, label) {
  const shell = view(page).locator('.shell');
  await shell.evaluate((node) => { node.style.visibility = 'hidden'; });
  /* Photograph #scene itself rather than the iframe element, and trim the rim
     below. The demo host draws the view inside a rounded 1px border; on the
     paper theme that dark edge against a light page is a 0.67 step, which is
     what this check reported the first time it ran. It was right about the
     pixels and wrong about whose they were. */
  const png = await view(page).locator('#scene').screenshot({ type: 'png' });
  await shell.evaluate((node) => { node.style.visibility = ''; });

  const scene = await inside(page, () => document.querySelector('#scene').dataset.scene);
  const safe = (SCENES.scenes[scene]?.safe) || [];
  if (!safe.length) return;

  const probe = await browser.newPage();
  const worst = await probe.evaluate(async ({ data, rects }) => {
    const img = new Image();
    img.src = `data:image/png;base64,${data}`;
    await img.decode();
    const canvas = document.createElement('canvas');
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0);
    const { data: px, width, height } = ctx.getImageData(0, 0, canvas.width, canvas.height);
    // Rec. 709 luma is close enough here and is what "how light is this" means
    // to an eye; the check is about steps, not about absolute appearance.
    const lum = (x, y) => {
      const i = (y * width + x) * 4;
      return (0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2]) / 255;
    };

    // An element capture lands on whole pixels while the element itself sits on
    // a half one, so the outermost rows can be the host's frame rather than the
    // scene. Trim the rim: a capture boundary is not a background.
    const RIM = 3;
    let step = 0;
    let where = null;
    rects.forEach((r, n) => {
      const x0 = Math.max(RIM, Math.round(r.x * width));
      const y0 = Math.max(RIM, Math.round(r.y * height));
      const x1 = Math.min(width - RIM, Math.round((r.x + r.w) * width));
      const y1 = Math.min(height - RIM, Math.round((r.y + r.h) * height));
      for (let y = y0; y < y1; y += 8) {
        for (let x = x0; x < x1; x += 8) {
          const here = lum(x, y);
          const jump = Math.max(
            Math.abs(here - lum(Math.min(x1, x + 8), y)),
            Math.abs(here - lum(x, Math.min(y1, y + 8))),
          );
          if (jump > step) { step = jump; where = `safe[${n}] at ${x},${y}`; }
        }
      }
    });
    return { step, where };
  }, { data: png.toString('base64'), rects: safe });
  await probe.close();

  process.stderr.write(`  · ${label}: ${scene}, worst step ${worst.step.toFixed(3)}`
    + ` (ceiling ${CEILING})\n`);
  if (worst.step > CEILING) {
    failures.push(
      `${label}: background steps ${worst.step.toFixed(3)} behind text in ${scene}`
      + ` — over the ${CEILING} legibility ceiling, ${worst.where}`,
    );
  }
}

/* ---------------------------------------------------------------- the run -- */

/* Seed 2's Act I offers a shop on floor 2 and a campfire six floors up on one
   walkable path, so every facet is reachable in a short, repeatable walk. The
   map is a pure function of the seed, so these screenshots are reproducible. */
const DEMO_SEED = 2;

async function walk(page, theme) {
  const tag = (name) => `${name}-${theme}`;

  // Each pass starts from the same fresh climb, or state leaks between them and
  // the screenshots stop being comparable. This is the call the boot race used
  // to eat, which meant the "fresh" climb was whatever the previous pass left.
  await drive(page, 'new run', (seed) =>
    window.__spire.callTool('spire_new_run', { seed }).then((r) => r && r.ok), DEMO_SEED);
  await page.waitForTimeout(500);
  await inside(page, () => { window.__spire.ui.screen = 'title'; window.__spire.render(); });
  await page.waitForTimeout(200);

  // Title. The client boots with no state until it asks for it.
  await expect(page, 'title', { wordmark: '.wordmark', 'run chrome': '#chrome' });
  await shoot(page, tag('title'));

  // Map — the hero is the graph, and only reachable nodes are offered.
  await drive(page, 'map refresh', () =>
    window.__spire.callTool('spire_map_refresh', {}).then((r) => r && r.ok));
  await page.waitForTimeout(400);
  await view(page).locator('.mnode.legal').first().waitFor({ timeout: 15000 });
  await expect(page, 'map', {
    'node graph': '.map-canvas',
    edges: '.map-edge',
    'reachable nodes': '.mnode.legal',
    legend: '.map-legend',
    'draw tool': '.marker-bar .marker',
    'deck count in chrome': '#chrome',
    'composed background': '#scene .scene-svg',
  });
  await shoot(page, tag('map'));
  await sceneContrast(page, tag('map'));

  // The background alone, with the content hidden — the receipt that the scene
  // is a place and not a texture.
  await view(page).locator('.shell').evaluate((n) => { n.style.visibility = 'hidden'; });
  await shoot(page, tag('scene-map'));
  await view(page).locator('.shell').evaluate((n) => { n.style.visibility = ''; });

  // The Slay the Spire 2 annotation layer.
  await view(page).locator('.marker').first().click();
  await view(page).locator('.mnode.legal').first().click();
  await page.waitForTimeout(500);
  const marked = await view(page).locator('.mnode .mark').count();
  if (marked === 0) failures.push('map: annotating a node left no mark');
  await shoot(page, tag('map-annotated'));

  // Turn the marker off, then commit to an edge.
  await view(page).locator('.marker').first().click();
  await view(page).locator('.mnode.legal').first().click();
  await view(page).locator('[data-action="enter"]').click();
  await view(page).locator('.enemy').waitFor({ timeout: 15000 });

  // Intent lands before the hand is usable — the entertainment requirement in
  // mcp-client.md, and the reason there is a Begin beat at all.
  const handBeforeReveal = await view(page).locator('.hand .card').count();
  if (handBeforeReveal > 0) failures.push('room: the hand was live before the intent beat');
  await expect(page, 'intent', {
    'enemy silhouette': '.enemy-sil',
    'enemy name': '.enemy-name',
    'room type chip': '.room-chip',
    'intent or an honest absence': '.intent, .no-intent',
    'progress meter': '.meter',
  });
  await shoot(page, tag('intent'));

  // Combat.
  const begin = view(page).locator('[data-action="reveal"]');
  if (await begin.count()) await begin.click();
  await view(page).locator('.hand').waitFor({ timeout: 15000 });
  await expect(page, 'combat', {
    hand: '.hand',
    'a playable card': '.card[data-action="play"]',
    'cost orb': '.card .cost',
    'type band': '.card .band',
    'rarity notch': '.card .notch',
    'energy pips': '.pips .pip',
    'keyboard hints': '.status .kbd',
    'active-room banner': '#banner',
    'composed background': '#scene .scene-svg',
  });
  await shoot(page, tag('combat'));
  // The densest screen in the product, and so the one legibility has to hold on.
  await sceneContrast(page, tag('combat'));

  // Play by keyboard — one screen, everything reachable without a mouse.
  await page.keyboard.press('1');
  await page.waitForTimeout(700);
  await shoot(page, tag('combat-played'));

  // Acceptance: a deterministic verdict, or an honest report that none is bound.
  await view(page).locator('[data-action="acceptance"]').click();
  await page.waitForTimeout(900);
  await expect(page, 'acceptance', { 'acceptance panel': '.acceptance' });
  await shoot(page, tag('acceptance'));

  // Clear the room however many turns it takes.
  for (let i = 0; i < 12; i += 1) {
    const done = await inside(page, () => {
      const room = window.__spire.ui.state && window.__spire.ui.state.room;
      return !room || (room.clear_at !== undefined && room.progress >= room.clear_at);
    });
    if (done) break;
    const card = view(page).locator('.card[data-action="play"]').first();
    if (await card.count()) {
      await card.click();
    } else {
      await view(page).locator('[data-action="end-turn"]').click();
    }
    await page.waitForTimeout(420);
  }
  await view(page).locator('[data-action="clear"]').click();
  await page.waitForTimeout(1100);

  // Reward — Skip owns the hero region and holds focus.
  const reward = view(page).locator('.skip-hero');
  if (await reward.count()) {
    await expect(page, 'reward', {
      'skip hero': '.skip-hero',
      'skip payout': '.skip-payout',
      offers: '.offers .card',
    });
    const skipFirst = await inside(page, () => {
      const html = document.querySelector('#stage').innerHTML;
      return html.indexOf('skip-hero') < html.indexOf('offers');
    });
    if (!skipFirst) failures.push('reward: Skip does not own the hero region');
    const focused = await inside(page, () => document.activeElement?.id);
    if (focused !== 'skip-hero') failures.push(`reward: focus landed on ${focused}, not Skip`);
    await shoot(page, tag('reward'));
    await reward.click();
    await page.waitForTimeout(900);
  } else {
    failures.push('reward: no reward screen after clearing a fight');
  }

  // Deck — objects filed by how they are spent.
  await view(page).locator('[data-action="screen-deck"]').click();
  await page.waitForTimeout(400);
  await expect(page, 'deck', { 'entity rows': '.entity', silhouettes: '.sil' });
  await shoot(page, tag('deck'));

  // Badges.
  await view(page).locator('[data-action="screen-map"]').click();
  await page.waitForTimeout(300);
  await view(page).locator('[data-action="screen-badges"]').click();
  await page.waitForTimeout(700);
  await shoot(page, tag('badges'));

  // Walk on until a campfire or a merchant turns up, and photograph whichever.
  await view(page).locator('[data-action="screen-map"]').click();
  await page.waitForTimeout(300);
  const seen = new Set();
  for (let i = 0; i < 16 && seen.size < 2; i += 1) {
    await drive(page, 'map refresh', () =>
    window.__spire.callTool('spire_map_refresh', {}).then((r) => r && r.ok));
    await page.waitForTimeout(350);
    const kinds = await inside(page, () => (window.__spire.ui.map?.nodes || [])
      .filter((n) => n.legal)
      .map((n) => ({ id: n.id, kind: n.resolved || n.kind })));
    if (!kinds.length) break;
    const wanted = kinds.find((n) => (n.kind === 'rest' || n.kind === 'shop') && !seen.has(n.kind))
      || kinds[0];
    await drive(page, `enter ${wanted.kind}`, (id) =>
      window.__spire.callTool('spire_enter_node', { node: id }).then((r) => r && r.ok), wanted.id);
    await page.waitForTimeout(700);

    const screen = await inside(page, () => window.__spire.ui.screen);

    /* The intent beat has to survive arriving by tool call, not just by click.
       Entering here goes straight through `callTool`, bypassing every click
       handler — which is exactly how a reveal from the previous fight used to
       leak into the next room and put its cards face up before the telegraph
       was read. Assert it on every room the walk opens, not just the first. */
    if (screen === 'room') {
      const early = await view(page).locator('.hand .card').count();
      if (early > 0) {
        failures.push(`${wanted.kind}: hand was live on entry — the intent beat was skipped`);
      }
    }
    if (screen === 'campfire' && !seen.has('rest')) {
      seen.add('rest');
      await expect(page, 'campfire', { options: '.choice', 'card shelf': '.entity' });
      await shoot(page, tag('campfire'));
    } else if (screen === 'shop' && !seen.has('shop')) {
      seen.add('shop');
      // No `spire_shop_list` injected here. This used to call it before
      // asserting the shelf existed, which supplied the exact step the client
      // was missing — so the harness proved the merchant worked while a player
      // walking in from the map saw an empty room. A check that provides the
      // missing step is not a check. The client fetches its own shelf now, and
      // this waits for it like a player would.
      await view(page).locator('.entity').first().waitFor({ timeout: 15000 });
      await expect(page, 'shop', { wares: '.entity' });
      await shoot(page, tag('shop'));
    } else if (screen === 'event') {
      await expect(page, 'event', { choices: '.choice' });
      // A gate that is *met* must stay takeable, so compare against the run's
      // actual state rather than asserting that any gate implies a lock.
      const expected = await inside(page, () => {
        const st = window.__spire.ui.state;
        const owned = new Set((st.pool || []).map((c) => c.id));
        return (st.room.event.choices || []).filter((c) => {
          const need = c.requires || {};
          return (need.card && !owned.has(need.card))
            || (need.focus !== undefined && (st.focus || 0) < need.focus);
        }).length;
      });
      const shown = await view(page).locator('.choice[aria-disabled="true"]').count();
      if (shown !== expected) {
        failures.push(`event: ${expected} choice(s) unaffordable but ${shown} rendered locked`);
      }
      await shoot(page, tag('event'));
    }

    // Move on: clear it if it is clearable, otherwise leave.
    await inside(page, async () => {
      const room = window.__spire.ui.state.room;
      if (!room) return;
      if (room.clear_at !== undefined) {
        for (let n = 0; n < 12; n += 1) {
          const s = window.__spire.ui.state.room;
          if (!s || s.progress >= s.clear_at) break;
          const hand = await window.__spire.callTool('spire_list_hand', {});
          const card = (hand?.hand || []).find((c) => c.playable && c.progress > 0);
          if (card) await window.__spire.callTool('spire_play_card', { card: card.id });
          else await window.__spire.callTool('spire_end_turn', {});
        }
      }
      // An event is cleared by choosing. Take the option with no gate on it,
      // which also exercises the effect pipeline on the way past.
      const args = { action: 'clear' };
      if (room.kind === 'event') {
        const open = (room.event?.choices || []).find((c) => !c.requires)
          || room.event?.choices?.[0];
        if (open) args.choice = open.id;
      }
      await window.__spire.callTool('spire_clear_or_flee', args);
      if (window.__spire.ui.state.pending_reward) {
        await window.__spire.callTool('spire_reward_resolve', { skip: true });
      }
    });
    await page.waitForTimeout(700);
  }
  // Both facets, not just the one. The walk exits three ways — sixteen steps
  // spent, no legal node left, or wandering into the boss — and only a missing
  // campfire was ever recorded, so a run that never found the merchant printed
  // "all screens rendered and checked" with two screenshots quietly absent.
  if (!seen.has('rest')) failures.push('never reached a campfire while walking the act');
  if (!seen.has('shop')) failures.push('never reached a merchant while walking the act');
}

/* ------------------------------------------------------------------- run -- */

rmSync(OUT, { recursive: true, force: true });
mkdirSync(OUT, { recursive: true });

/* Each pass is fenced. `drive` throws when a tool call does not land, which
   ends that pass rather than photographing a climb that never started — and the
   fence keeps one bad pass from costing the others their coverage. The failure
   is already recorded, and the required-screenshot check at the end turns the
   missing images into named failures. */
async function pass(label, body) {
  process.stderr.write(`\n${label}:\n`);
  try {
    await body();
  } catch (err) {
    failures.push(`${label}: pass aborted — ${err && err.message ? err.message : err}`);
  }
}

for (const theme of ['dark', 'light']) {
  await pass(theme, async () => {
    const page = await newPage(theme, 1180);
    await walk(page, theme);
    await page.context().close();
  });
}

/* Narrow panel — an IDE sidebar is the common case and the layout has to hold. */
await pass('narrow', async () => {
  const page = await newPage('dark', 560);
  await drive(page, 'new run', (seed) =>
    window.__spire.callTool('spire_new_run', { seed }).then((r) => r && r.ok), DEMO_SEED);
  await page.waitForTimeout(500);
  await page.selectOption('#size', '420x760');
  await page.waitForTimeout(400);
  await view(page).locator('.mnode.legal').first().waitFor({ timeout: 15000 });
  await shoot(page, 'map-narrow');
  const overflow = await inside(page, () =>
    document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  if (overflow) failures.push('narrow: the body scrolls horizontally at 420px');
  await page.context().close();
});

/* Greyscale — the silhouettes have to carry it with every hue removed. */
await pass('greyscale', async () => {
  const page = await newPage('dark', 1180);
  await drive(page, 'new run', (seed) =>
    window.__spire.callTool('spire_new_run', { seed }).then((r) => r && r.ok), DEMO_SEED);
  await page.waitForTimeout(500);
  await view(page).locator('.mnode.legal').first().waitFor({ timeout: 15000 });
  await page.addStyleTag({ content: '#view { filter: grayscale(1); }' });
  await shoot(page, 'map-greyscale');
  await view(page).locator('.mnode.legal').first().click();
  await view(page).locator('[data-action="enter"]').click();
  await view(page).locator('.enemy').waitFor({ timeout: 15000 });
  const begin = view(page).locator('[data-action="reveal"]');
  if (await begin.count()) await begin.click();
  await view(page).locator('.hand').waitFor({ timeout: 15000 });
  await shoot(page, 'combat-greyscale');
  await page.context().close();
});

/* Reduced motion — animations must not be the only thing conveying state. */
await pass('reduced-motion', async () => {
  const context = await browser.newContext({
    viewport: { width: 1180, height: 900 },
    deviceScaleFactor: 1,
    colorScheme: 'dark',
    reducedMotion: 'reduce',
  });
  const page = await context.newPage();
  await page.goto(BASE, { waitUntil: 'networkidle' });
  await page.addStyleTag({ content: '#log { display: none !important; }' });
  await page.waitForFunction(() => window.__spireHostReady === true, { timeout: 15000 });
  await drive(page, 'new run', (seed) =>
    window.__spire.callTool('spire_new_run', { seed }).then((r) => r && r.ok), 2);
  await page.waitForTimeout(500);
  await view(page).locator('.mnode.legal').first().waitFor({ timeout: 15000 });
  await shoot(page, 'map-reduced-motion');
  await context.close();
});

await browser.close();
stopHost();

/* The screenshots are the product, so name the ones that must exist rather than
   counting whatever happened to be taken. A count cannot tell "we photographed
   everything" from "we photographed the easy ones twice". */
const REQUIRED = [
  'map-narrow', 'map-greyscale', 'combat-greyscale', 'map-reduced-motion',
  ...['dark', 'light'].flatMap((theme) => [
    'title', 'map', 'map-annotated', 'intent', 'combat', 'combat-played',
    'acceptance', 'reward', 'deck', 'badges', 'campfire', 'shop',
  ].map((name) => `${name}-${theme}`)),
];
const missing = REQUIRED.filter((name) => !shots.includes(name));
if (missing.length) failures.push(`never photographed: ${missing.join(', ')}`);

process.stderr.write(`\n${shots.length} screenshots → ${OUT.replace(ROOT + '/', '')}\n`);
if (failures.length) {
  process.stderr.write(`\n${failures.length} problem(s):\n`);
  for (const problem of [...new Set(failures)]) process.stderr.write(`  ✗ ${problem}\n`);
  process.exit(1);
}
process.stderr.write('all screens rendered and checked\n');
