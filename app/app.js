/* Spire — the client.
 *
 * Renders whatever the engine last said and calls tools when the player acts.
 * It holds no rules: legality, energy, progress, unknown-node resolution and the
 * single-room lock all live in scripts/run.py, because two implementations of
 * the rules would drift and the Python one is the one with property tests.
 *
 * Screen order and hero regions come from design/spire-ai/ui/formats/*.md, which
 * treat layout regions as an API. The two rules those docs care most about are
 * enforced here in code rather than left to taste:
 *   - the intent is shown before the hand is usable
 *   - Skip owns the reward hero region and takes focus on entry
 */

import { Bridge, unwrap } from './bridge.js';

const SOFT_CAP_WARN = 'Deck is at the soft cap — taking a card means trading one away.';

/* Slay the Spire 2's intent vocabulary. `shows` decides whether a number is
   printed: an attack prints its exact tier, a block prints nothing at all. The
   middle option — a bracketed range — is the one Mega Crit playtested and cut,
   because players could not tell randomness from ignorance and stalled. */
const INTENT_ICONS = {
  attack: { icon: '⚔', tone: 'danger', shows: true },
  defend: { icon: '⛨', tone: 'muted', shows: false },
  buff: { icon: '↑', tone: 'warn', shows: false },
  debuff: { icon: '↓', tone: 'danger', shows: false },
  status: { icon: '⬚', tone: 'warn', shows: true },
  affliction: { icon: '▧', tone: 'warn', shows: true },
  heal: { icon: '+', tone: 'safe', shows: false },
  summon: { icon: '◉', tone: 'danger', shows: true },
  deathblow: { icon: '☠', tone: 'danger', shows: false },
  cowardly: { icon: '↷', tone: 'muted', shows: false },
  stunned: { icon: '✳', tone: 'muted', shows: false },
  sleeping: { icon: 'z', tone: 'muted', shows: true },
  unknown: { icon: '?', tone: 'muted', shows: false },
};

const NODE_GLYPH = {
  monster: '✦', elite: '✸', rest: '▲', shop: '◆',
  treasure: '▮', boss: '☠', event: '◇', unknown: '?',
};

const NODE_LABEL = {
  monster: 'Monster room', elite: 'Elite room', rest: 'Campfire', shop: 'Merchant',
  treasure: 'Chest', boss: 'Act boss', event: 'Event', unknown: 'Unknown node',
};

const FACETS = {
  title: 'title', map: 'map', room: 'combat', event: 'event',
  reward: 'reward', campfire: 'campfire', shop: 'shop', deck: 'deck', badges: 'title',
  treasure: 'reward',
};

/* Markers the map draw tool offers. Slay the Spire 2 shipped annotation because
   routing is the decision the map exists to support; community sticker mods
   converged on roughly this set. */
const MARKERS = ['★', '✕', '☠', '❓', '👁'];

const bridge = new Bridge({ name: 'Spire', version: '0.3.0' });

const ui = {
  state: null,
  map: null,
  screen: 'title',
  selected: null,
  marker: null,
  /* Which room the player has pressed Begin in — not a bare boolean.
   *
   * The intent beat is the one entertainment rule mcp-client.md states
   * outright: the telegraph lands before the hand is usable. A flag set by the
   * click handler enforced that only for players who arrive by clicking. The
   * model calling `spire_enter_node`, a tool result pushed by the host, or a
   * reload into an open room all reach `absorb` without passing through any
   * handler, so a reveal from the *previous* fight was still set and the next
   * room opened with its cards already face up.
   *
   * Keyed on the room id, the question answers itself: a hand is revealed only
   * for the room it was revealed in, by every path in and out. */
  revealedFor: null,
  busy: false,
  lastError: null,
};

/* ------------------------------------------------------------------ util -- */

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

function toast(message) {
  const existing = $('#toast');
  if (existing) existing.remove();
  const node = el('div', 'toast', message);
  node.id = 'toast';
  node.setAttribute('role', 'status');
  document.body.appendChild(node);
  setTimeout(() => node.remove(), 2600);
}

function fanfare(text) {
  const overlay = el('div', 'fanfare');
  overlay.appendChild(el('div', 'stamp', text));
  document.body.appendChild(overlay);
  setTimeout(() => overlay.remove(), 660);
}

function setFacet(name) {
  document.documentElement.style.setProperty('--facet', `var(--facet-${FACETS[name] || 'map'})`);
}

/* ---------------------------------------------------------------- engine -- */

async function callTool(name, args) {
  if (ui.busy) return null;
  ui.busy = true;
  try {
    const result = await bridge.callServerTool(name, args || {});
    const payload = unwrap(result);
    if (payload && payload.ok === false) {
      ui.lastError = payload.error;
      toast(payload.error.message || 'Refused.');
    } else {
      ui.lastError = null;
    }
    absorb(payload);
    return payload;
  } catch (err) {
    toast(String(err && err.message ? err.message : err));
    return null;
  } finally {
    ui.busy = false;
  }
}

/* Fold whatever came back into the client's view of the world. */
function absorb(payload) {
  if (!payload) return;
  if (payload.state) ui.state = payload.state;
  if (payload.map) ui.map = payload.map;
  if (payload.room && ui.state) ui.state.room = payload.room;
  if (payload.hand && ui.state) ui.state.hand = payload.hand;
  if (payload.wares) ui.wares = payload.wares;
  if (payload.badges) ui.badges = payload.badges;
  if (payload.annotations && ui.map) {
    ui.map.nodes.forEach((n) => { n.mark = payload.annotations[n.id] || null; });
  }
  route();
  render();
  fetchWhatTheScreenNeeds();
}

/* One room needs data no other payload carries.
 *
 * `screenShop` renders each tile's `detail` and `affordable`, and only
 * `spire_shop_list` computes them — the enter payload puts the raw catalog on
 * `room`, which nothing reads. Without this the merchant is a dead room: an
 * empty shelf, and no action in the client that could fill it, because the buy
 * buttons *are* the tiles that were never drawn.
 *
 * This sits in `absorb` rather than in the click handler because the click is
 * only one way in. The model calling `spire_enter_node` itself, a tool-result
 * pushed by the host, a reload into an open shop — all land here, and the first
 * version of this fix covered none of them. The screenshot harness enters nodes
 * by calling the tool directly and is what caught it.
 *
 * Deferred by a tick because `absorb` runs inside `callTool`, which holds
 * `ui.busy` until it returns; keyed on the room id so re-entering the same shop
 * does not refetch on every payload. */
function fetchWhatTheScreenNeeds() {
  const room = ui.state && ui.state.room;
  // Leaving a shop clears the claim *and* the attempt count, so walking back in
  // later always gets a fresh set of tries.
  if (!room || room.kind !== 'shop') { ui.waresFor = null; ui.waresTries = 0; return; }
  if (ui.waresFor === room.id) return;
  // Releasing the claim is not enough on its own: this only runs from `absorb`,
  // so after a failed fetch nothing retries until the player does something —
  // and on an empty shelf the only thing they can do is leave. Retry here, but
  // bounded, because a shop that refuses forever must not spin.
  if (ui.waresTriesFor !== room.id) { ui.waresTriesFor = room.id; ui.waresTries = 0; }
  if (ui.waresTries >= 3) return;
  ui.waresTries += 1;
  // Claim the room before the call so a second payload does not fire a second
  // fetch — then release the claim if the fetch did not land. Claiming and
  // never releasing meant one failed list left the merchant empty for as long
  // as the player stayed in it, with no action that could refill it: the exact
  // dead room this function exists to prevent, just harder to reach.
  ui.waresFor = room.id;
  setTimeout(async () => {
    const got = await callTool('spire_shop_list', {});
    if (!got || got.ok === false) {
      ui.waresFor = null;
      fetchWhatTheScreenNeeds();
    }
  }, 0);
}

/* Has the player pressed Begin in the room that is open right now?
 *
 * Derived rather than stored, for the same reason `waresFor` is: the client is
 * handed state by paths that never touch a click handler, so anything a handler
 * sets can be carried into a room it was not meant for. */
function handRevealed() {
  const room = ui.state && ui.state.room;
  return !!room && ui.revealedFor === room.id;
}

/* Which screen the state implies. The client never picks a screen the state does
   not justify — that is how the single-room lock stays true in the UX and not
   just in the data. */
function route() {
  const s = ui.state;
  if (!s) { ui.screen = 'title'; return; }
  if (s.room) {
    // A chest and a fight are not the same screen. Everything that is not a
    // named kind falls through to `room`, which is right for monsters, elites
    // and bosses and was wrong for treasure.
    ui.screen = s.room.kind === 'event' ? 'event'
      : s.room.kind === 'rest' ? 'campfire'
        : s.room.kind === 'shop' ? 'shop'
          : s.room.kind === 'treasure' ? 'treasure' : 'room';
    return;
  }
  if (s.pending_reward) { ui.screen = 'reward'; return; }
  if (ui.screen === 'badges' || ui.screen === 'deck') return;
  ui.screen = 'map';
}

/* ---------------------------------------------------------------- chrome -- */

function renderChrome() {
  const s = ui.state;
  const bar = $('#chrome');
  const banner = $('#banner');
  bar.innerHTML = '';
  banner.hidden = true;
  if (!s) { bar.hidden = true; return; }
  bar.hidden = false;

  const add = (label, value, cls) => {
    const wrap = el('span', cls || '');
    wrap.append(document.createTextNode(`${label} `));
    wrap.appendChild(el('b', null, String(value)));
    bar.appendChild(wrap);
  };

  bar.appendChild(el('span', 'chip cls', s.class_name || 'Colorless'));
  add('Act', (s.act_label || '').replace(/^Act\s*/, ''));
  add('Floor', s.floor);

  const deck = el('span', s.over_soft_cap ? 'chip cap-warn' : '');
  deck.append(document.createTextNode('Deck '));
  deck.appendChild(el('b', null, `${s.deck_size}/${s.soft_cap}`));
  bar.appendChild(deck);

  add('Focus', `◈${s.focus}`);
  add('Streak', s.streak);
  add('Ascension', `A${s.ascension}`);

  if (s.room && s.room.energy !== undefined) {
    const wrap = el('span');
    wrap.append(document.createTextNode('Energy '));
    wrap.appendChild(pips(s.room.energy, s.room.energy_max));
    bar.appendChild(wrap);
  }

  (s.relics || []).slice(0, 6).forEach((relic) => {
    const chip = el('span', 'sil relic');
    chip.title = `${relic.name} — ${relic.rule}`;
    chip.setAttribute('aria-label', `Relic: ${relic.name}`);
    bar.appendChild(chip);
  });
  (s.potions || []).filter(Boolean).forEach((potion) => {
    const chip = el('span', 'sil potion');
    chip.title = `${potion.name} — ${potion.spent_on}`;
    chip.setAttribute('aria-label', `Potion: ${potion.name}`);
    bar.appendChild(chip);
  });

  if (s.room) {
    banner.hidden = false;
    banner.innerHTML = '';
    banner.appendChild(el('span', 'dot', '●'));
    banner.appendChild(el('span', null,
      `ROOM ACTIVE: ${s.room.name} · ${s.room.room_type || s.room.kind}`));
    const spacer = el('span', 'spacer');
    banner.appendChild(spacer);
    if (ui.screen !== 'map') {
      const flee = el('button', 'btn ghost', 'Flee');
      flee.dataset.action = 'flee';
      banner.appendChild(flee);
    }
  }
}

function pips(now, max) {
  const wrap = el('span', 'pips');
  wrap.setAttribute('role', 'img');
  wrap.setAttribute('aria-label', `${now} of ${max} energy`);
  for (let i = 0; i < Math.max(now, max); i += 1) {
    wrap.appendChild(el('span', i < now ? 'pip on' : 'pip'));
  }
  return wrap;
}

/* ------------------------------------------------------------------ title -- */

function screenTitle() {
  const wrap = el('div', 'screen');
  wrap.appendChild(el('span', 'facet-tab', 'The Spire'));
  const h = el('h1', 'hero-title', 'SPIRE');
  wrap.appendChild(h);
  wrap.appendChild(el('p', 'sub', 'Your project is the run. Your config is the deck. One room at a time.'));

  const panel = el('div', 'panel');
  if (ui.lastError && ui.lastError.code === 'no_deck') {
    panel.appendChild(el('p', null,
      'No save in this repository yet. Run /spire in Claude Code to scan the project, '
      + 'detect its class, and deal a starter deck.'));
  } else {
    panel.appendChild(el('p', null, 'Load the run and start climbing.'));
  }
  wrap.appendChild(panel);

  const actions = el('div', 'actions');
  const go = el('button', 'btn', 'Continue climb');
  go.dataset.action = 'load';
  actions.appendChild(go);
  const fresh = el('button', 'btn ghost', 'New climb');
  fresh.dataset.action = 'new-run';
  actions.appendChild(fresh);
  wrap.appendChild(actions);
  return wrap;
}

/* -------------------------------------------------------------------- map -- */

function screenMap() {
  const wrap = el('div', 'screen');
  const m = ui.map;
  wrap.appendChild(el('span', 'facet-tab', 'Map'));
  if (!m) {
    wrap.appendChild(el('p', 'sub', 'Loading the climb…'));
    return wrap;
  }

  wrap.appendChild(el('h1', 'hero-title', m.act_label || 'The climb'));
  const boss = m.boss || {};
  wrap.appendChild(el('p', 'sub',
    `Boss: ${boss.name || 'unknown'} — ${boss.intent || ''} `
    + 'Visible from floor one, which is what makes the act a plan.'));

  const canvas = el('div', 'map-canvas');
  canvas.style.setProperty('--floors', String((m.rows || 15) + 1));
  canvas.setAttribute('role', 'group');
  canvas.setAttribute('aria-label', 'The climb. Only reachable nodes can be entered.');

  const rows = (m.rows || 15) + 1;
  const cols = m.cols || 7;
  /* Row 0 is floor 1 and sits at the bottom; the boss row is at the top. */
  const yPct = (row) => (1 - (row + 0.5) / rows) * 100;
  const xPct = (col) => ((col + 0.5) / cols) * 100;

  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const byId = {};
  m.nodes.forEach((n) => { byId[n.id] = n; });

  m.nodes.forEach((node) => {
    (node.next || []).forEach((col) => {
      const target = m.nodes.find((n) => n.row === node.row + 1 && n.col === col);
      if (!target) return;
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', `${xPct(node.col)}%`);
      line.setAttribute('y1', `${yPct(node.row)}%`);
      line.setAttribute('x2', `${xPct(target.col)}%`);
      line.setAttribute('y2', `${yPct(target.row)}%`);
      let cls = 'map-edge';
      if (node.cleared && target.cleared) cls += ' lit';
      else if (node.current && target.legal) cls += ' open';
      line.setAttribute('class', cls);
      svg.appendChild(line);
    });
  });
  canvas.appendChild(svg);

  m.nodes.forEach((node) => {
    const kind = node.resolved || node.kind;
    const btn = el('button', `mnode k-${kind}`);
    btn.style.left = `${xPct(node.col)}%`;
    btn.style.top = `${yPct(node.row)}%`;
    if (node.cleared) btn.classList.add('cleared');
    if (node.legal) btn.classList.add('legal');
    if (node.current) btn.classList.add('current');
    if (ui.selected === node.id) btn.classList.add('selected');
    btn.dataset.node = node.id;
    btn.dataset.action = 'node';
    // Shape carries the kind; the label carries it again for anyone who cannot
    // see shape. Never colour alone.
    btn.setAttribute('aria-label',
      `Floor ${node.row + 1}, ${NODE_LABEL[kind] || kind}`
      + (node.legal ? ', reachable' : node.cleared ? ', cleared' : ', not reachable')
      + (node.mark ? `, marked ${node.mark}` : ''));
    btn.title = `${NODE_LABEL[kind] || kind} · floor ${node.row + 1}`;
    if (!node.legal && !node.current) btn.tabIndex = -1;
    btn.appendChild(el('span', 'g', NODE_GLYPH[kind] || '?'));
    if (node.mark) btn.appendChild(el('span', 'mark', node.mark));
    canvas.appendChild(btn);

    if (node.current) {
      const here = el('span', 'you-are-here', 'you are here');
      here.style.left = `${xPct(node.col)}%`;
      here.style.top = `${yPct(node.row)}%`;
      canvas.appendChild(here);
    }
  });

  wrap.appendChild(canvas);

  /* Scroll to where the player actually is. A 16-floor column opened at the top
     shows the boss and hides the only nodes you can click. */
  requestAnimationFrame(() => {
    const focusNode = canvas.querySelector('.mnode.current') || canvas.querySelector('.mnode.legal');
    if (!focusNode) return;
    const target = focusNode.offsetTop - canvas.clientHeight * 0.66;
    canvas.scrollTop = Math.max(0, target);
  });

  const legend = el('div', 'map-legend');
  Object.keys(NODE_GLYPH).forEach((kind) => {
    legend.appendChild(el('span', null, `${NODE_GLYPH[kind]} ${NODE_LABEL[kind]}`));
  });
  wrap.appendChild(legend);

  /* The draw tool. */
  const bar = el('div', 'marker-bar');
  bar.appendChild(el('span', 'marker-hint', 'Mark a node:'));
  MARKERS.forEach((mark) => {
    const btn = el('button', 'marker', mark);
    btn.dataset.action = 'marker';
    btn.dataset.mark = mark;
    btn.setAttribute('aria-pressed', String(ui.marker === mark));
    btn.setAttribute('aria-label', `Marker ${mark}`);
    bar.appendChild(btn);
  });
  const off = el('button', 'marker', '⌫');
  off.dataset.action = 'marker';
  off.dataset.mark = 'clear';
  off.setAttribute('aria-pressed', String(ui.marker === 'clear'));
  off.setAttribute('aria-label', 'Erase marker');
  bar.appendChild(off);
  bar.appendChild(el('span', 'marker-hint',
    ui.marker ? 'Now click a node to mark it.' : 'Plan a route before you commit to an edge.'));
  wrap.appendChild(bar);

  const actions = el('div', 'actions');
  const enter = el('button', 'btn', ui.selected ? 'Enter room' : 'Select a node');
  enter.dataset.action = 'enter';
  if (!ui.selected) enter.setAttribute('aria-disabled', 'true');
  actions.appendChild(enter);
  const deck = el('button', 'btn ghost', 'Deck');
  deck.dataset.action = 'screen-deck';
  actions.appendChild(deck);
  const badges = el('button', 'btn ghost', 'Badges');
  badges.dataset.action = 'screen-badges';
  actions.appendChild(badges);
  wrap.appendChild(actions);
  return wrap;
}

/* ------------------------------------------------------------------- room -- */

function intentRow(intent) {
  const spec = INTENT_ICONS[intent.kind] || INTENT_ICONS.unknown;
  const row = el('div', `intent tone-${spec.tone}`);
  row.appendChild(el('span', 'icon', spec.icon));
  const magnitude = intent.tier !== undefined && intent.tier !== null ? intent.tier
    : intent.count !== undefined && intent.count !== null ? intent.count : null;
  if (spec.shows && magnitude !== null) row.appendChild(el('span', 'n', String(magnitude)));
  row.appendChild(el('span', 'txt', intent.text || ''));
  row.appendChild(el('span', 'sensor', intent.sensor ? intent.sensor : 'no sensor'));
  return row;
}

/* A chest is not a fight, and drawing it as one taught the player three wrong
   things at once: a footer offering cards there is no hand for, an End turn the
   engine refuses with "Chest has no turns", and — worst — no sight of what is
   actually in the chest, so Clear was a blind click. `content/scenes.json`
   already gave treasure its own place (`vault`); this is the screen catching up
   with the background it was already standing in. */
function screenTreasure() {
  const room = ui.state.room;
  const offer = room.offer || {};
  const wrap = el('div', 'screen');
  wrap.appendChild(el('span', 'facet-tab', 'Chest'));

  const head = el('div', 'enemy');
  const sil = el('div', 'enemy-sil treasure');
  sil.style.setProperty('--room', 'var(--room-orient)');
  sil.textContent = NODE_GLYPH.treasure;
  head.appendChild(sil);
  const meta = el('div');
  meta.appendChild(el('h1', 'enemy-name', room.name || 'Chest'));
  meta.appendChild(el('p', 'sub', 'Nothing here fights back. Take it and move on.'));
  head.appendChild(meta);
  wrap.appendChild(head);

  const offers = el('div', 'offers');
  const tile = el('div', 'card');
  tile.appendChild(el('span', 'band'));
  tile.appendChild(el('span', 'notch'));
  tile.appendChild(el('span', 'title', offer.title || offer.ref || 'Something'));
  tile.appendChild(el('span', 'body', offer.body || ''));
  const foot = el('span', 'foot');
  foot.appendChild(el('span', null, offer.kind || 'relic'));
  tile.appendChild(foot);
  offers.appendChild(tile);
  wrap.appendChild(offers);

  const actions = el('div', 'actions');
  const open = el('button', 'btn', 'Open the chest');
  open.dataset.action = 'clear';
  actions.appendChild(open);
  wrap.appendChild(actions);
  return wrap;
}

function screenRoom() {
  const s = ui.state;
  const room = s.room;
  const wrap = el('div', 'screen');
  wrap.appendChild(el('span', 'facet-tab', room.kind === 'elite' ? 'Elite' : room.kind === 'boss' ? 'Boss' : 'Combat'));

  const head = el('div', 'enemy');
  const sil = el('div', `enemy-sil ${room.kind}`);
  sil.style.setProperty('--room', `var(--room-${room.room_type || 'orient'})`);
  sil.textContent = NODE_GLYPH[room.kind] || '✦';
  head.appendChild(sil);

  const meta = el('div');
  meta.appendChild(el('h1', 'enemy-name', room.name));
  const line = el('div', 'enemy-meta');
  const chip = el('span', `room-chip rt-${room.room_type || 'orient'}`, room.room_type || 'room');
  line.appendChild(chip);
  if (room.telegraph) line.append(document.createTextNode(`  Telegraph: ${room.telegraph}`));
  meta.appendChild(line);
  if (room.blurb) meta.appendChild(el('p', 'sub', room.blurb));
  head.appendChild(meta);
  wrap.appendChild(head);

  const intents = room.intents || [];
  if (intents.length) {
    const box = el('div', 'intents');
    intents.forEach((intent) => box.appendChild(intentRow(intent)));
    wrap.appendChild(box);
  } else {
    wrap.appendChild(el('div', 'no-intent',
      'No intent shown. No deterministic check stands behind this room, so nothing '
      + 'is telegraphed — a guess dressed as a telegraph would be worse than silence.'));
  }

  if (room.acceptance) {
    const box = el('div', 'acceptance');
    const acc = room.acceptance;
    const text = acc.type === 'command'
      ? `acceptance: the repo's ${acc.cmd} gate → exit 0`
      : `acceptance: ${acc.type} → ${acc.expect || ''}`;
    box.appendChild(el('div', null, text));
    const verdict = room.acceptance_result;
    if (verdict) {
      const cls = verdict.result === 'pass' ? 'pass' : verdict.result === 'fail' ? 'fail' : 'unknown';
      const line2 = el('div', 'verdict');
      line2.appendChild(el('span', cls, `→ ${verdict.result.toUpperCase()}`));
      if (verdict.reason) line2.append(document.createTextNode(` ${verdict.reason}`));
      if (verdict.command) line2.append(document.createTextNode(` (${verdict.command})`));
      box.appendChild(line2);
    }
    wrap.appendChild(box);
  }

  if (room.clear_at !== undefined) {
    const row = el('div', 'meter-row');
    row.appendChild(el('span', 'meter-label', 'PROGRESS'));
    const meter = el('div', 'meter');
    meter.setAttribute('role', 'progressbar');
    meter.setAttribute('aria-valuenow', String(room.progress));
    meter.setAttribute('aria-valuemax', String(room.clear_at));
    const fill = el('div', 'meter-fill');
    fill.style.width = `${Math.min(100, (room.progress / room.clear_at) * 100)}%`;
    meter.appendChild(fill);
    row.appendChild(meter);
    row.appendChild(el('span', 'meter-label', `${room.progress}/${room.clear_at}`));
    wrap.appendChild(row);

    /* Entertainment requirement from mcp-client.md: the intent lands before the
       hand is usable. One beat, then the cards. */
    if (!handRevealed()) {
      const actions = el('div', 'actions');
      const begin = el('button', 'btn', 'Begin — reveal hand');
      begin.dataset.action = 'reveal';
      actions.appendChild(begin);
      const flee = el('button', 'btn danger', 'Flee');
      flee.dataset.action = 'flee';
      actions.appendChild(flee);
      wrap.appendChild(actions);
      return wrap;
    }

    wrap.appendChild(renderHand(s.hand || [], room));
  }

  if ((room.log || []).length) {
    const log = el('div', 'log');
    room.log.slice(-4).forEach((line) => log.appendChild(el('div', null, line)));
    wrap.appendChild(log);
  }

  const actions = el('div', 'actions');
  const accept = el('button', 'btn', 'Run acceptance');
  accept.dataset.action = 'acceptance';
  actions.appendChild(accept);
  const clear = el('button', 'btn ghost', 'Clear room');
  clear.dataset.action = 'clear';
  if (room.clear_at !== undefined && room.progress < room.clear_at) {
    clear.setAttribute('aria-disabled', 'true');
    clear.title = `Needs ${room.clear_at - room.progress} more progress.`;
  }
  actions.appendChild(clear);
  const end = el('button', 'btn ghost', 'End turn');
  end.dataset.action = 'end-turn';
  actions.appendChild(end);
  const flee = el('button', 'btn danger', 'Flee');
  flee.dataset.action = 'flee';
  actions.appendChild(flee);
  wrap.appendChild(actions);

  // No `Esc map` here. The single-room lock means route() puts you straight
  // back in the room on the next payload, so the shortcut could only ever
  // flicker — and an affordance that does nothing is worse than one that is
  // absent. Leaving a room is Clear or Flee, which are both on screen.
  wrap.appendChild(keyHint([
    ['1-9', 'play card'], ['E', 'end turn'], ['A', 'acceptance'], ['C', 'clear'],
  ]));
  return wrap;
}

function renderHand(hand, room) {
  const wrap = el('div');
  const fan = el('div', 'hand');
  const playable = hand.filter((c) => c.playable);
  const benched = hand.filter((c) => !c.playable);
  // Cards only need to overlap once the row would otherwise run out of width.
  fan.style.setProperty('--overlap', playable.length > 4 ? '-16px' : '2px');

  playable.forEach((card, i) => {
    const mid = (playable.length - 1) / 2;
    const offset = i - mid;
    const btn = el('button', `card t-${card.type} r-${card.rarity}`);
    btn.style.setProperty('--rot', `${offset * 3.4}deg`);
    btn.style.setProperty('--ty', `${Math.abs(offset) * 5}px`);
    btn.dataset.action = 'play';
    btn.dataset.card = card.id;
    btn.setAttribute('aria-label',
      `${card.title}, costs ${card.cost} energy, advances ${card.progress}. Press ${i + 1}.`);

    btn.appendChild(el('span', 'band'));
    btn.appendChild(el('span', 'cost', String(card.cost)));
    btn.appendChild(el('span', 'notch'));
    btn.appendChild(el('span', 'title', card.title));
    btn.appendChild(el('span', 'body', card.body));
    const foot = el('span', 'foot');
    foot.appendChild(el('span', null, card.type));
    foot.appendChild(el('span', null, `+${card.progress}`));
    btn.appendChild(foot);
    if (i < 9) btn.appendChild(el('span', 'key', String(i + 1)));
    fan.appendChild(btn);
  });

  if (!playable.length) {
    fan.appendChild(el('p', 'sub',
      `Nothing playable with ${room.energy} energy. End the turn to refill.`));
  }
  wrap.appendChild(fan);

  if (benched.length) {
    const bench = el('div', 'bench');
    benched.forEach((card) => {
      const chip = el('span', 'bench-chip', `(${card.cost}) ${card.title} — ${card.reason}`);
      bench.appendChild(chip);
    });
    wrap.appendChild(bench);
  }
  return wrap;
}

function keyHint(pairs) {
  const wrap = el('div', 'status');
  pairs.forEach(([key, what]) => {
    const span = el('span');
    span.appendChild(el('span', 'kbd', key));
    span.append(document.createTextNode(` ${what}`));
    wrap.appendChild(span);
  });
  return wrap;
}

/* ------------------------------------------------------------------ event -- */

function screenEvent() {
  const room = ui.state.room;
  const event = room.event || {};
  const wrap = el('div', 'screen');
  wrap.appendChild(el('span', 'facet-tab', 'Event'));
  wrap.appendChild(el('h1', 'hero-title', event.title || room.name));
  wrap.appendChild(el('p', 'sub', event.body || ''));

  const owned = new Set((ui.state.pool || []).map((c) => c.id));
  const focus = ui.state.focus || 0;

  const panel = el('div', 'panel');
  (event.choices || []).forEach((choice) => {
    const needs = choice.requires || {};
    const locked = (needs.card && !owned.has(needs.card))
      || (needs.focus !== undefined && focus < needs.focus);

    const btn = el('button', `choice${choice.greedy ? ' greedy' : ''}`);
    btn.dataset.action = 'choice';
    btn.dataset.choice = choice.id;
    if (locked) btn.setAttribute('aria-disabled', 'true');
    btn.appendChild(el('div', 'lb', choice.label + (locked ? ' \u2014 locked' : '')));
    btn.appendChild(el('div', 'cq', choice.consequence));
    panel.appendChild(btn);
  });
  wrap.appendChild(panel);
  wrap.appendChild(el('p', 'sub',
    'A room with no checkable acceptance is not a fight, so it is not given an intent. '
    + 'Every choice here spends the floor \u2014 there is no way to look and leave.'));
  return wrap;
}

/* ----------------------------------------------------------------- reward -- */

function screenReward() {
  const reward = ui.state.pending_reward || {};
  const wrap = el('div', 'screen');
  wrap.appendChild(el('span', 'facet-tab', 'Reward'));

  const skip = el('button', 'skip-hero');
  skip.dataset.action = 'skip';
  skip.id = 'skip-hero';
  skip.appendChild(el('div', 'skip-kicker', 'Skipping is skilled play'));
  skip.appendChild(el('div', 'skip-main', 'Take nothing'));
  skip.appendChild(el('span', 'skip-payout', `+◈${reward.skip_payout || 0} focus`));
  wrap.appendChild(skip);

  wrap.appendChild(el('div', 'offers-label', 'Or take one'));
  const offers = el('div', 'offers');
  (reward.offers || []).forEach((offer) => {
    const btn = el('button', `card t-${offer.type || 'skill'} r-${offer.rarity}`);
    btn.dataset.action = 'take';
    btn.dataset.offer = offer.id;
    btn.appendChild(el('span', 'band'));
    if (offer.cost !== undefined) btn.appendChild(el('span', 'cost', String(offer.cost)));
    btn.appendChild(el('span', 'notch'));
    btn.appendChild(el('span', 'title', offer.title || offer.ref || offer.name));
    btn.appendChild(el('span', 'body', offer.body || ''));
    const foot = el('span', 'foot');
    foot.appendChild(el('span', null, offer.rarity || offer.kind || ''));
    foot.appendChild(el('span', null,
      (offer.rooms && offer.rooms.length) ? `${offer.rooms.length} rooms` : 'any room'));
    btn.appendChild(foot);
    offers.appendChild(btn);
  });
  wrap.appendChild(offers);

  if (ui.state.over_soft_cap) wrap.appendChild(el('p', 'sub', SOFT_CAP_WARN));
  wrap.appendChild(el('div', 'status', ''));
  const stats = ui.state.rewards || {};
  wrap.appendChild(el('p', 'sub',
    `Run so far: ${stats.taken || 0} taken, ${stats.skipped || 0} skipped. `
    + 'A high skip rate is health, not disengagement.'));
  return wrap;
}

/* --------------------------------------------------------------- campfire -- */

function screenCampfire() {
  const room = ui.state.room;
  const wrap = el('div', 'screen');
  wrap.appendChild(el('span', 'facet-tab', 'Campfire'));
  wrap.appendChild(el('h1', 'hero-title', 'Campfire'));
  wrap.appendChild(el('p', 'sub',
    'One action, then the floor is spent. With nothing to heal, the choice is Smith or Prune — '
    + 'both still cost the same non-renewable floor.'));

  const panel = el('div', 'panel');
  (room.options || []).forEach((option) => {
    const btn = el('button', 'choice');
    btn.dataset.action = 'camp';
    btn.dataset.option = option.id;
    btn.appendChild(el('div', 'lb', option.name));
    btn.appendChild(el('div', 'cq', option.blurb));
    panel.appendChild(btn);
  });
  wrap.appendChild(panel);

  wrap.appendChild(el('div', 'offers-label', 'Pick a card'));
  const shelf = el('div', 'shelf');
  // The pool, not the hand: at a campfire nothing is legal or illegal, every
  // card you own is a candidate to smith or prune.
  (ui.state.pool || []).forEach((card) => {
    const btn = el('button', 'entity');
    btn.dataset.action = 'camp-card';
    btn.dataset.card = card.id;
    btn.appendChild(el('span', 'sil card-sil'));
    btn.appendChild(el('span', 'nm', card.title + (card.upgraded ? '+' : '')));
    btn.appendChild(el('span', 'dt', `cost ${card.cost} · ${card.type}`));
    if (ui.campCard === card.id) btn.style.borderColor = 'var(--facet)';
    shelf.appendChild(btn);
  });
  wrap.appendChild(shelf);
  return wrap;
}

/* ------------------------------------------------------------------- shop -- */

function screenShop() {
  const wrap = el('div', 'screen');
  wrap.appendChild(el('span', 'facet-tab', 'Merchant'));
  wrap.appendChild(el('h1', 'hero-title', 'The Merchant'));
  wrap.appendChild(el('p', 'sub',
    `You hold ◈${ui.state.focus} focus. Card removal costs ◈${ui.state.removal_cost} `
    + 'and rises every time it is used — that is what gives the deck cap teeth.'));
  // The merchant obeys the same cap the reward screen does, so it has to say so
  // before you spend. Discovering the limit from a refusal toast after choosing
  // is the shop format's "upfront caption" rule read backwards.
  if (ui.state.over_soft_cap) wrap.appendChild(el('p', 'sub', SOFT_CAP_WARN));

  const shelf = el('div', 'shelf');
  (ui.wares || []).forEach((ware) => {
    const detail = ware.detail || {};
    const btn = el('button', 'entity');
    btn.dataset.action = 'buy';
    btn.dataset.ware = ware.id;
    if (!ware.affordable) btn.setAttribute('aria-disabled', 'true');
    const kindClass = ware.kind === 'relic' ? 'relic' : ware.kind === 'potion' ? 'potion' : 'card-sil';
    btn.appendChild(el('span', `sil ${kindClass}`));
    btn.appendChild(el('span', 'nm', detail.name || detail.title || ware.ref));
    btn.appendChild(el('span', 'dt', `◈${ware.price} · ${ware.kind}`));
    shelf.appendChild(btn);
  });
  wrap.appendChild(shelf);

  const actions = el('div', 'actions');
  const leave = el('button', 'btn ghost', 'Leave');
  leave.dataset.action = 'leave-shop';
  actions.appendChild(leave);
  wrap.appendChild(actions);
  return wrap;
}

/* ------------------------------------------------------------ deck, badges -- */

function screenDeck() {
  const s = ui.state;
  const wrap = el('div', 'screen');
  wrap.appendChild(el('span', 'facet-tab', 'Deck'));
  wrap.appendChild(el('h1', 'hero-title', 'The deck'));
  wrap.appendChild(el('p', 'sub', 'Objects are filed by how they are spent, not by what they are.'));

  const groups = [
    ['Cards · invoked, cost budget', s.pool || [], 'card-sil',
      (c) => [c.title + (c.upgraded ? '+' : ''),
        `cost ${c.cost} · ${(c.rooms || []).length ? c.rooms.join(', ') : 'any room'}`]],
    ['Dealt skills · the config this run wrote', s.cards || [], 'card-sil',
      (c) => [c.name, `${c.plays} plays`]],
    ['Relics · always on, cost nothing', s.relics || [], 'relic',
      (r) => [r.name, r.rule]],
    ['Powers · fire on an event', s.powers || [], 'power',
      (p) => [p.name, `on ${p.event}`]],
    ['Potions · consumed once', (s.potions || []).filter(Boolean), 'potion',
      (p) => [p.name, p.spent_on]],
    ['Curses · cost you, unwanted', (s.curses || []).filter(Boolean), 'curse',
      (c) => [c.name, c.cost]],
  ];

  groups.forEach(([title, items, silClass, project]) => {
    const panel = el('div', 'panel');
    panel.style.marginBottom = '12px';
    const head = el('div', 'offers-label', `${title}  (${items.length})`);
    head.style.marginTop = '0';
    panel.appendChild(head);
    const shelf = el('div', 'shelf');
    items.forEach((item) => {
      const [name, detail] = project(item);
      const row = el('div', 'entity');
      row.appendChild(el('span', `sil ${silClass}`));
      row.appendChild(el('span', 'nm', name || '—'));
      row.appendChild(el('span', 'dt', detail || ''));
      shelf.appendChild(row);
    });
    if (!items.length) shelf.appendChild(el('span', 'dt', 'none'));
    panel.appendChild(shelf);
    wrap.appendChild(panel);
  });

  const actions = el('div', 'actions');
  const back = el('button', 'btn ghost', 'Back to map');
  back.dataset.action = 'screen-map';
  actions.appendChild(back);
  wrap.appendChild(actions);
  return wrap;
}

function screenBadges() {
  const wrap = el('div', 'screen');
  wrap.appendChild(el('span', 'facet-tab', 'Badges'));
  wrap.appendChild(el('h1', 'hero-title', 'What made this run yours'));
  wrap.appendChild(el('p', 'sub',
    'Every badge is a pure read of the save, so none of them can be granted for free.'));

  const list = ui.badges || ui.state.badges || [];
  if (!list.length) {
    wrap.appendChild(el('div', 'panel', 'None yet. Keep climbing.'));
  }
  list.forEach((badge) => {
    const row = el('div', 'badge');
    row.style.marginBottom = '10px';
    row.appendChild(el('span', 'star', '★'));
    const body = el('div');
    body.appendChild(el('div', 'nm', badge.name));
    body.appendChild(el('div', 'bl', badge.blurb));
    row.appendChild(body);
    wrap.appendChild(row);
  });

  const actions = el('div', 'actions');
  const back = el('button', 'btn ghost', 'Back to map');
  back.dataset.action = 'screen-map';
  actions.appendChild(back);
  wrap.appendChild(actions);
  return wrap;
}

/* ----------------------------------------------------------------- render -- */

const SCREENS = {
  title: screenTitle, map: screenMap, room: screenRoom, event: screenEvent,
  reward: screenReward, campfire: screenCampfire, shop: screenShop,
  treasure: screenTreasure, deck: screenDeck, badges: screenBadges,
};

function render() {
  setFacet(ui.screen);
  renderChrome();
  const stage = $('#stage');
  stage.innerHTML = '';
  stage.appendChild((SCREENS[ui.screen] || screenTitle)());

  // reward.md: focus lands on Skip, because the default has to be refusal.
  if (ui.screen === 'reward') {
    const skip = $('#skip-hero');
    if (skip) skip.focus();
  }
  bridge.reportSize();
}

/* ----------------------------------------------------------------- events -- */

async function onAction(action, dataset, target) {
  const s = ui.state;
  switch (action) {
    case 'load':
      await callTool('spire_get_run', {});
      await callTool('spire_map_refresh', {});
      break;
    case 'new-run':
      await callTool('spire_new_run', { seed: Math.floor(Date.now() / 60000) % 64 });
      break;
    case 'node': {
      const id = dataset.node;
      if (ui.marker) {
        await callTool('spire_annotate_node', { node: id, mark: ui.marker });
        return;
      }
      const node = ui.map.nodes.find((n) => n.id === id);
      if (!node || !node.legal) { toast('Not reachable from where you stand.'); return; }
      ui.selected = id;
      render();
      break;
    }
    case 'marker':
      ui.marker = ui.marker === dataset.mark ? null : dataset.mark;
      render();
      break;
    case 'enter':
      if (!ui.selected) { toast('Pick a node first.'); return; }
      await callTool('spire_enter_node', { node: ui.selected });
      ui.selected = null;
      break;
    case 'reveal':
      ui.revealedFor = (ui.state && ui.state.room) ? ui.state.room.id : null;
      await callTool('spire_list_hand', {});
      render();
      break;
    case 'play': {
      target.classList.add('committing');
      await new Promise((r) => setTimeout(r, 180));
      const played = await callTool('spire_play_card', { card: dataset.card });
      // The commit animation ends with the card off-screen, and only a re-render
      // puts the hand back. A call that returns without absorbing — `ui.busy`,
      // or a bridge error — used to leave the card sitting at that end state,
      // invisible, with nothing to say why.
      if (!played) {
        target.classList.remove('committing');
        render();
      }
      break;
    }
    case 'end-turn':
      await callTool('spire_end_turn', {});
      break;
    case 'acceptance':
      await callTool('spire_run_acceptance', {});
      break;
    case 'clear': {
      const room = s && s.room;
      if (room && room.clear_at !== undefined && room.progress < room.clear_at) {
        toast(`Needs ${room.clear_at - room.progress} more progress.`);
        return;
      }
      const before = s && s.floor;
      const result = await callTool('spire_clear_or_flee', { action: 'clear' });
      if (result && result.ok) {
        fanfare('ROOM CLEARED');
        bridge.updateModelContext(
          `Cleared ${room ? room.name : 'the room'}; floor is now ${ui.state ? ui.state.floor : before + 1}.`,
        );
      }
      break;
    }
    case 'flee': {
      if (!window.confirm('Flee this room? The clean-room streak resets.')) return;
      await callTool('spire_clear_or_flee', { action: 'flee' });
      toast('Fled. Streak reset.');
      break;
    }
    case 'skip': {
      const result = await callTool('spire_reward_resolve', { skip: true });
      if (result && result.ok) {
        toast(`Skipped. +◈${result.focus_gained} focus.`);
        bridge.updateModelContext(`Skipped the card reward; gained ${result.focus_gained} focus.`);
      }
      break;
    }
    case 'take':
      await callTool('spire_reward_resolve', { take: dataset.offer });
      break;
    case 'camp':
      ui.campOption = dataset.option;
      if (!ui.campCard && dataset.option !== 'dig') { toast('Pick a card below, then choose again.'); return; }
      await callTool('spire_campfire', { option: dataset.option, card: ui.campCard });
      ui.campCard = null;
      ui.campOption = null;
      break;
    case 'camp-card':
      ui.campCard = dataset.card;
      render();
      break;
    case 'buy':
      // One call. The buy reply carries the shelf repriced against the focus
      // that is left, so the second `spire_shop_list` this used to fire was a
      // round trip for data already in hand — and, while it was the *only*
      // list call in the client, the reason the shelf appeared to work at all.
      await callTool('spire_shop_buy', { ware: dataset.ware });
      break;
    case 'leave-shop':
      await callTool('spire_clear_or_flee', { action: 'clear' });
      break;
    case 'choice': {
      // The choice IS the clear for an event room — its effects only run if the
      // id travels with it.
      const result = await callTool('spire_clear_or_flee', {
        action: 'clear', choice: dataset.choice,
      });
      if (result && result.ok) {
        const said = (result.resolution || []).join(' ');
        if (said) toast(said);
        bridge.updateModelContext(`Event resolved: ${dataset.choice}. ${said}`.trim());
      }
      break;
    }
    case 'screen-deck': ui.screen = 'deck'; render(); break;
    case 'screen-badges':
      await callTool('spire_badges', {});
      ui.screen = 'badges';
      render();
      break;
    case 'screen-map': ui.screen = 'map'; render(); break;
    default: break;
  }
}

document.addEventListener('click', (event) => {
  const target = event.target.closest('[data-action]');
  if (!target) return;
  if (target.getAttribute('aria-disabled') === 'true') return;
  event.preventDefault();
  onAction(target.dataset.action, target.dataset, target);
});

/* One screen, everything relevant, no submenus — and a keyboard that can reach
   all of it, because a game you have to aim a mouse at is slower than one you
   do not. */
document.addEventListener('keydown', (event) => {
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  const key = event.key;

  if (key === 'Escape') {
    if (ui.screen === 'deck' || ui.screen === 'badges') { ui.screen = 'map'; render(); }
    return;
  }
  if (ui.screen === 'room') {
    // The intent beat is a rule, not a screen. Before Begin the room offers
    // only Begin and Flee, but this listener is on `document` and was keyed on
    // the screen alone — so `a` shelled out the repo's real lint or test, with
    // a 300s timeout and the whole client frozen behind `ui.busy`, before the
    // player had seen the hand. The one key that stays live is Begin itself.
    if (!handRevealed()) {
      if (key.toLowerCase() === 'b') {
        event.preventDefault();
        onAction('reveal', {}, document.body);
      }
      return;
    }
    if (/^[1-9]$/.test(key)) {
      const cards = document.querySelectorAll('.hand .card[data-action="play"]');
      const card = cards[Number(key) - 1];
      if (card) { event.preventDefault(); card.click(); }
      return;
    }
    const map = { e: 'end-turn', a: 'acceptance', c: 'clear' };
    const action = map[key.toLowerCase()];
    if (action) { event.preventDefault(); onAction(action, {}, document.body); }
    return;
  }
  if (ui.screen === 'reward' && key.toLowerCase() === 's') {
    event.preventDefault();
    onAction('skip', {}, document.body);
  }
});

/* ------------------------------------------------------------------- boot -- */

function applyHostTheme(context) {
  const theme = context && context.theme;
  if (theme === 'dark' || theme === 'light') {
    document.documentElement.setAttribute('data-theme', theme);
  } else {
    document.documentElement.removeAttribute('data-theme');
  }
}

bridge.ontoolresult = (params) => {
  absorb(unwrap(params));
};

bridge.oncontextchange = (context) => {
  applyHostTheme(context);
  render();
};

$('#btn-theme').addEventListener('click', () => {
  const now = document.documentElement.getAttribute('data-theme');
  const next = now === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  $('#theme-label').textContent = next === 'dark' ? 'Dark' : 'Light';
});

async function boot() {
  const context = await bridge.connect();
  applyHostTheme(context);
  bridge.observeSize();
  render();
  // The host pushes the initiating tool's result on its own, but a cold open
  // (fullscreen, a reload, the standalone harness) has to ask.
  await callTool('spire_get_run', {});
  if (ui.state) await callTool('spire_map_refresh', {});
}

boot();

/* Exposed for the demo harness and the screenshot pass, which drive the real
   client rather than a copy of it. */
window.__spire = { ui, bridge, render, absorb, callTool };
