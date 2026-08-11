/* Spire — interactive demo logic.
   Vanilla, no build. A small state machine drives nine facets that follow the
   format contracts under ../formats/.

   Maps are NOT generated here. They come from mapdata.js, produced by
   `python3 scripts/mapgen.py emit-js`, so the branching structure, the room
   quotas and the placement rules have exactly one implementation. See
   ../../sts-fidelity.md for which Slay the Spire properties this ports and
   which it deliberately refuses. */
(function () {
  "use strict";

  /* ------------------------------ content ------------------------------ */
  var SOFT_CAP = 12;
  var SKIP_PAYOUT = 2; // the Singing Bowl port: refusing a card pays a number

  // Read from mapdata.js rather than restated here, so the ramp cannot drift
  // from scripts/mapgen.py. The rolls are generated there too.
  var RAMP_CFG = window.SPIRE_RAMP || { order: [], base: {} };
  var RAMP_ORDER = RAMP_CFG.order;
  var RAMP_BASE = RAMP_CFG.base;

  var MONSTERS = [
    {
      name: "Nit Cluster", room: "refactor", clearAt: 2,
      intent: "Will bury the real review comment under twelve style nits.",
      blurb: "Clear by fixing the nits mechanically and separating the real note.",
      acceptance: "ruff check .  →  exit 0",
      telegraph: "Telegraph: bikeshed swarm",
      turnEffect: "Three more nits arrive on the same file."
    },
    {
      name: "Regression Bug", room: "bug", clearAt: 3,
      intent: "Will reappear next release if you patch the symptom.",
      blurb: "Clear by pinning it with a failing test, then fixing the cause.",
      acceptance: "python -m pytest -q  →  exit 0",
      telegraph: "Telegraph: it came back",
      turnEffect: "The bug reproduces on a second code path."
    },
    {
      name: "Missing Test", room: "bug", clearAt: 2,
      intent: "Will let the next change break silently.",
      blurb: "Clear by covering the untested branch.",
      acceptance: "coverage on the touched file  →  no regression",
      telegraph: "Telegraph: silent breakage",
      turnEffect: "Another untested branch appears."
    },
    {
      name: "Dependency Bump", room: "infra", clearAt: 3,
      intent: "Will break the build in a transitive package you do not own.",
      blurb: "Clear by pinning the version and recording why.",
      acceptance: "lockfile committed + build green",
      telegraph: "Telegraph: transitive break",
      turnEffect: "A peer dependency warning turns into an error."
    }
  ];

  var ELITES = [
    {
      name: "Flaky Suite", room: "bug", clearAt: 4,
      intent: "Will fail CI randomly if ignored.",
      blurb: "Clear by stabilizing the suite, not by quarantining tests.",
      acceptance: "python -m pytest -q  ×3  →  exit 0",
      telegraph: "Telegraph: CI roulette",
      turnEffect: "A red test blinks green and hides itself."
    },
    {
      name: "Duplication Hydra", room: "refactor", clearAt: 4,
      intent: "Will grow a third copy while you edit the second.",
      blurb: "Clear by collapsing the copies behind one caller.",
      acceptance: "one definition, all callers migrated",
      telegraph: "Telegraph: copy three appears",
      turnEffect: "Someone copies the block into a new module."
    }
  ];

  // The boss's room type comes from content/bosses.json via the map data, so
  // acts past the Heart get a real room instead of undefined.
  var FALLBACK_BOSS_ROOM = "design";

  var ALL_CARDS = [
    { id: "c-ftf", cost: 1, title: "Failing Test First", body: "Pin the defect with a red test.", rooms: ["bug", "refactor"], progress: 2, rarity: "common" },
    { id: "c-char", cost: 1, title: "Characterization", body: "Lock current behavior in a test.", rooms: ["bug", "refactor", "feature"], progress: 1, rarity: "common" },
    { id: "c-slice", cost: 2, title: "Vertical Slice", body: "Ship one thin end-to-end path.", rooms: ["feature", "design"], progress: 2, rarity: "uncommon" },
    { id: "c-decide", cost: 1, title: "Record Decision", body: "Write the call and the spike outcome.", rooms: ["design", "infra"], progress: 2, rarity: "uncommon" },
    { id: "c-small", cost: 1, title: "Small Diff", body: "Cut the change to one reviewable unit.", rooms: ["bug", "feature", "refactor", "design", "infra"], progress: 1, rarity: "common" },
    { id: "c-runbook", cost: 1, title: "Runbook", body: "Write down how to operate it.", rooms: ["infra", "docs"], progress: 2, rarity: "common" }
  ];

  var OFFERS = [
    { id: "o1", title: "Quarantine Test", rarity: "rare", note: "Rare · often a trap" },
    { id: "o2", title: "Retry Gate", rarity: "uncommon", note: "Uncommon" },
    { id: "o3", title: "Flake Journal", rarity: "common", note: "Common" }
  ];

  var TREASURE = [
    { id: "t1", title: "Coverage Floor", rarity: "uncommon", note: "Relic · never drop coverage silently" },
    { id: "t2", title: "Small Diffs", rarity: "common", note: "Relic · one reviewable unit per room" }
  ];

  var WARES = [
    { id: "w1", title: "Characterization", kind: "card", price: 2 },
    { id: "w2", title: "Failing Test First", kind: "card", price: 2 },
    { id: "w3", title: "Coverage Floor", kind: "relic", price: 3 }
  ];

  var EVENT = {
    title: "Just One More Requirement",
    body: "A stakeholder adds a “tiny” must-have before ship. Accepting expands this feature room into an elite.",
    choices: [
      { id: "accept", label: "Accept scope", consequence: "Gain the Bloated Scope curse, permanently.", greedy: true },
      { id: "cut", label: "Cut scope", consequence: "Requires a Cut Scope card." },
      { id: "park", label: "Park in backlog", consequence: "Log it and leave. No curse." }
    ]
  };

  var ASCENSION = [
    { level: 0, blurb: "Warn only. A0 is always available." },
    { level: 5, blurb: "Lint can block Stop." },
    { level: 10, blurb: "Lint and tests can block Stop. Coverage not yet enforced. Ascension only moves when you apply." },
    { level: 15, blurb: "Coverage regressions can block Stop." },
    { level: 20, blurb: "Every room requires review evidence." }
  ];

  /* The run's objects, filed by how they are spent. See ../ENTITY_STANDARDS.md. */
  var RELICS = [
    { id: "ruff-strict", name: "Ruff Strict", rule: "Lint and format with Ruff. Resolve every warning before committing." },
    { id: "typed-public-api", name: "Typed Public API", rule: "Type-hint public functions and keep the type checker clean." },
    { id: "no-mocks-in-prod", name: "No Mocks In Prod", rule: "Never ship mock, stub, or placeholder data in production paths." },
    { id: "stdlib-only", name: "Stdlib Only", rule: "Engine scripts import the standard library and nothing else." }
  ];

  var POWERS = [
    { id: "ruff-on-edit", name: "ruff-on-edit", event: "PostToolUse", note: "Lints touched Python after an edit." },
    { id: "reward-gate", name: "reward-gate", event: "Stop", note: "Decides whether a room was cleared." },
    { id: "status-line", name: "status-line", event: "SessionStart", note: "Prints the run line." }
  ];

  var POTIONS = [
    { id: "bisect", name: "Bisect", cls: "diag", spent: "Find the commit that broke it", uses: 1 },
    { id: "profiler", name: "Profiler Run", cls: "perf", spent: "Name one bottleneck", uses: 1 },
    { id: "spike", name: "Timeboxed Spike", cls: "", spent: "Answer one design question", uses: 1 }
  ];

  var CURSES = [
    { id: "bloated-scope", name: "Bloated Scope", cost: "Every feature room costs 1 extra energy.", why: "Accepted at an event." },
    { id: "deprecated-client", name: "Deprecated Client", cost: "Infra rooms may reopen after clearing.", why: "Carried since the migration." }
  ];

  function freshDeck() {
    return [
      { id: "d1", name: "orient", plays: 0, when: "floor 0", upgraded: false },
      { id: "d2", name: "add-endpoint", plays: 0, when: "floor 0", upgraded: false },
      { id: "d3", name: "run-tests", plays: 4, when: "today", upgraded: false },
      { id: "d4", name: "characterization", plays: 1, when: "floor 2", upgraded: false }
    ];
  }

  var MAPS = window.SPIRE_MAPS || [];
  // Derived from the data rather than hardcoded, so cycling climbs can never
  // ask for a seed or act that was never emitted.
  var SEED_COUNT = Math.max(1, new Set(MAPS.map(function (m) { return m.seed; })).size);
  var ACT_COUNT = Math.max(1, new Set(MAPS.map(function (m) { return m.act; })).size);

  /* ------------------------------ state ------------------------------ */
  var DEFAULTS = {
    screen: "title", returnScreen: "map",
    seed: 0, act: 1,
    currentId: null, selectedId: null,
    // chrome.md: one room at a time. Non-null means a room is open and the map
    // is locked until it is cleared or fled.
    activeRoom: null,
    deckSize: 6, tokens: 4, taken: 1, skips: 4, focus: 0, curses: 0, actsCleared: 0,
    streak: 2, ascension: 10, ascPick: 10,
    currentEnemy: null, progress: 0, energy: 3, energyMax: 3, playedThisTurn: [],
    campMode: "prune", campPick: null, shopId: null,
    rewardMode: "card", priorSeed: 0,
    theme: "light"
  };

  var state, DECK, MAP, RAMP;

  function mapFor(seed, act) {
    for (var i = 0; i < MAPS.length; i++) {
      if (MAPS[i].seed === seed && MAPS[i].act === act) return MAPS[i];
    }
    return null;
  }

  function loadAct(seed, act) {
    var found = mapFor(seed, act);
    if (!found) {
      console.warn("No map for seed " + seed + " act " + act + "; falling back to the first.");
    }
    MAP = found || MAPS[0];
    MAP.byId = {};
    MAP.cleared = {};
    for (var i = 0; i < MAP.nodes.length; i++) {
      var n = MAP.nodes[i];
      // SPIRE_MAPS entries are shared objects reused across climbs, so a stale
      // resolution would otherwise survive a reset and desync from the ramp.
      delete n.resolved;
      MAP.byId[n.id] = n;
    }
    RAMP = {};
    for (var k = 0; k < RAMP_ORDER.length; k++) RAMP[RAMP_ORDER[k]] = 0;
    state.act = act;
    state.currentId = null;
    state.selectedId = null;
  }

  function resetState() {
    state = Object.assign({}, DEFAULTS);
    DECK = freshDeck();
    loadAct(0, 1);
  }

  /* ------------------------------ map helpers ------------------------------ */
  function nodeId(row, col) { return "r" + row + "c" + col; }
  function node(id) { return MAP.byId[id] || null; }
  function entries() {
    return MAP.nodes.filter(function (n) { return n.row === 0; });
  }
  function nextNodes(n) {
    return n.next.map(function (col) { return node(nodeId(n.row + 1, col)); })
      .filter(Boolean);
  }
  function legalNodes() {
    if (!state.currentId) return entries();
    var cur = node(state.currentId);
    return cur ? nextNodes(cur) : entries();
  }
  function isLegal(id) {
    return legalNodes().some(function (n) { return n.id === id; });
  }
  function currentFloor() {
    var cur = node(state.currentId);
    return cur ? cur.row + 1 : 0;
  }

  // Resolve an unknown node using the rolls generated by mapgen.py.
  function resolveUnknown(n) {
    if (n.resolved) return n.resolved;
    if (!n.rolls) {
      // Stale mapdata.js. Degrade to the most common outcome rather than throw.
      n.resolved = "event";
      return n.resolved;
    }
    var outcome = "event";
    for (var i = 0; i < RAMP_ORDER.length; i++) {
      var kind = RAMP_ORDER[i];
      if (n.rolls[i] < RAMP_BASE[kind] * (RAMP[kind] + 1)) {
        RAMP[kind] = 0;
        outcome = kind;
        break;
      }
      RAMP[kind] += 1;
    }
    n.resolved = outcome;
    return outcome;
  }

  function pick(list, id) {
    var h = 0;
    for (var i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) % 100000;
    return list[h % list.length];
  }

  function enemyFor(n) {
    if (n.kind === "boss") {
      return {
        name: MAP.boss.name, room: MAP.boss.room || FALLBACK_BOSS_ROOM, clearAt: 5,
        intent: MAP.boss.intent || "Will block ship until someone owns the decision.",
        blurb: actLabel(MAP.act) + " boss. Known from floor 1, so build for it.",
        acceptance: MAP.boss.acceptance || "decision recorded + checks green",
        telegraph: "Telegraph: move the goalposts",
        turnEffect: "The goalposts slide two feet to the left."
      };
    }
    var e = n.kind === "elite" ? pick(ELITES, n.id) : pick(MONSTERS, n.id);
    return Object.assign({}, e);
  }

  /* ------------------------------ elements ------------------------------ */
  var $ = function (id) { return document.getElementById(id); };
  var els = {};
  var toastTimer = null;

  var PRIORS = [
    "Prior bias: bug 36% · feature 22% · refactor 16%",
    "Prior bias: refactor 31% · bug 24% · docs 14%",
    "Prior bias: infra 29% · feature 27% · design 11%"
  ];

  function toast(msg) {
    els.toast.textContent = msg;
    els.toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { els.toast.classList.remove("show"); }, 1900);
  }

  function orbRow(container, n, max) {
    container.innerHTML = "";
    for (var i = 0; i < max; i++) {
      var o = document.createElement("span");
      o.className = "orb" + (i < n ? "" : " spent");
      container.appendChild(o);
    }
  }

  var ROMAN = { 1: "I", 2: "II", 3: "III", 4: "IV" };
  var HEART_ACT = 4;

  // The climb has no level limit. Past the Heart the acts keep numbering up.
  function actLabel(act) {
    if (act === HEART_ACT) return "Act IV · the Heart";
    if (act > HEART_ACT) return "Act " + act + " · endless";
    return "Act " + ROMAN[act];
  }
  function actShort(act) { return act <= HEART_ACT ? ROMAN[act] : String(act); }

  function bindStats() {
    var map = {
      floor: currentFloor(), deck: state.deckSize, streak: state.streak,
      tokens: state.tokens, taken: state.taken, skips: state.skips,
      focus: state.focus, cap: SOFT_CAP, act: actShort(state.act), seed: state.seed
    };
    document.querySelectorAll("[data-bind]").forEach(function (el) {
      el.textContent = String(map[el.getAttribute("data-bind")]);
    });
    $("chrome-act").textContent = actShort(state.act);
    $("chrome-floor").textContent = String(currentFloor());
    $("chrome-streak").textContent = String(state.streak);
    $("chrome-asc").textContent = String(state.ascension);
    var deckEl = $("chrome-deck");
    deckEl.textContent = state.deckSize + "/" + SOFT_CAP;
    deckEl.classList.toggle("over-cap", state.deckSize >= SOFT_CAP);
  }

  /* ------------------------------ screen switch ------------------------------ */
  function showScreen(name) {
    state.screen = name;
    document.querySelectorAll(".screen").forEach(function (s) {
      s.classList.toggle("is-active", s.getAttribute("data-screen") === name);
    });
    applyFacet(name);

    els.chrome.hidden = name === "title";
    els.chromeEnergy.hidden = name !== "combat";

    // The banner is the single-task policy made visible, so it shows on every
    // facet while a room is open, not just inside the room.
    els.banner.hidden = !state.activeRoom || name === "title";
    if (!els.banner.hidden) renderBanner(name);

    document.querySelectorAll(".jump-btn").forEach(function (b) {
      var target = b.getAttribute("data-go");
      b.classList.toggle("is-current", target === name);
      // No opening a second room while one is active. Read-only surfaces stay
      // reachable, which is what the banner is for.
      var blocked = Boolean(state.activeRoom) &&
        ROOM_SCREENS.indexOf(target) !== -1 &&
        target !== state.activeRoom.screen;
      b.disabled = blocked;
      b.title = blocked ? "A room is active. Return to it or flee first." : "";
    });

    bindStats();
    render();
    focusPrimary(name);
  }

  var ROOM_SCREENS = ["map", "intent", "combat", "event", "reward", "campfire", "shop"];

  // reward.md wants focus to land on Skip. More generally, a screen change that
  // drops focus to <body> makes a keyboard user re-tab the whole nav each time.
  var PRIMARY = {
    reward: "btn-skip", map: "btn-enter", ascension: "btn-apply-asc",
    campfire: "btn-camp-confirm", shop: "btn-buy"
  };

  function focusPrimary(name) {
    var id = PRIMARY[name];
    var el = id ? $(id) : null;
    if (el && !el.disabled) {
      el.focus({ preventScroll: true });
      return;
    }
    var screen = document.querySelector('.screen[data-screen="' + name + '"]');
    var fallback = screen && screen.querySelector("button:not([disabled])");
    if (fallback) fallback.focus({ preventScroll: true });
  }

  function bannerButton(label, cls, onClick) {
    // A real button, so Enter and Space work. An <a> without href has neither.
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "banner-btn " + cls;
    btn.textContent = label;
    btn.addEventListener("click", onClick);
    return btn;
  }

  function renderBanner(name) {
    var room = state.activeRoom;
    els.bannerText.textContent = "ROOM ACTIVE: " + room.name + " · " + room.room;
    els.bannerActions.innerHTML = "";
    if (name !== room.screen) {
      els.bannerActions.appendChild(
        bannerButton("Return to room", "", function () { showScreen(room.screen); })
      );
    } else {
      var note = document.createElement("span");
      note.className = "hint";
      note.textContent = "This is the only room you can act in.";
      els.bannerActions.appendChild(note);
    }
    els.bannerActions.appendChild(bannerButton("Flee\u2026", "flee-link", doFlee));
  }

  /* ------------------------------ renderers ------------------------------ */
  function render() {
    bindStats();
    switch (state.screen) {
      case "map": renderMap(); break;
      case "intent": renderIntent(); break;
      case "combat": renderCombat(); break;
      case "event": renderEvent(); break;
      case "reward": renderReward(); break;
      case "campfire": renderCamp(); break;
      case "shop": renderShop(); break;
      case "ascension": renderAscension(); break;
      case "deck": renderDeck(); break;
      case "metrics": renderMetrics(); break;
    }
  }

  /* ------------------------------ deck facet ------------------------------ */
  function roomChips(rooms) {
    return rooms.map(function (r) {
      return '<span class="room-chip rt-' + r + '">' + r + "</span>";
    }).join("");
  }

  function fill(el, items, build, emptyNote) {
    el.innerHTML = "";
    if (!items.length) {
      el.innerHTML = '<p class="empty-note">' + emptyNote + "</p>";
      return;
    }
    items.forEach(function (it) {
      var node = document.createElement("div");
      build(node, it);
      el.appendChild(node);
    });
  }

  function renderDeck() {
    fill($("obj-cards"), ALL_CARDS, function (el, c) {
      el.className = "o-card " + c.rarity;
      el.innerHTML =
        '<span class="cost">' + c.cost + "</span>" +
        '<span class="notch" aria-hidden="true"></span>' +
        "<h4>" + c.title + "</h4>" +
        '<div class="rooms">' + roomChips(c.rooms) + "</div>" +
        '<span class="meta">' + c.rarity + "</span>";
    }, "No cards yet.");

    fill($("obj-relics"), RELICS, function (el, r) {
      el.className = "o-relic";
      el.innerHTML =
        '<span class="sil" aria-hidden="true">\u25cf</span>' +
        '<span><span class="name">' + r.name + "</span>" +
        '<div class="rule">' + r.rule + "</div></span>";
    }, "No relics yet.");

    fill($("obj-powers"), POWERS, function (el, p) {
      el.className = "o-power";
      el.innerHTML =
        '<span class="sil" aria-hidden="true">\u26a1</span>' +
        '<span><span class="name">' + p.name + "</span>" +
        '<div class="evt">' + p.event + "</div>" +
        '<div class="grp-note" style="margin:2px 0 0">' + p.note + "</div></span>";
    }, "No powers yet.");

    fill($("obj-potions"), POTIONS, function (el, p) {
      el.className = "o-potion " + p.cls;
      el.innerHTML =
        '<div class="sil" aria-hidden="true"></div>' +
        '<div class="name">' + p.name + "</div>" +
        '<div class="uses">' + p.spent + "</div>" +
        '<div class="uses">' + p.uses + " use left</div>";
    }, "No potions held.");

    fill($("obj-curses"), CURSES.slice(0, 1 + state.curses), function (el, c) {
      el.className = "o-curse";
      el.innerHTML =
        '<div class="name">\u2716 ' + c.name + "</div>" +
        "<div>" + c.cost + "</div>" +
        '<div class="cost-line">' + c.why + "</div>";
    }, "No curses. Keep it that way.");

    $("cnt-cards").textContent = ALL_CARDS.length;
    $("cnt-relics").textContent = RELICS.length;
    $("cnt-powers").textContent = POWERS.length;
    $("cnt-potions").textContent = POTIONS.length;
    $("cnt-curses").textContent = Math.min(CURSES.length, 1 + state.curses);
  }

  /* ------------------------------ metrics facet ------------------------------ */
  function renderMetrics() {
    // Illustrative for the demo. Anything not measurable from the repo or the
    // run log has no place on this page.
    var acts = [];
    for (var a = 1; a <= Math.max(3, state.act); a++) {
      acts.push({ label: actShort(a), spend: a <= state.act ? 4 + a * 3 + (a % 2) * 2 : 0 });
    }
    var max = Math.max.apply(null, acts.map(function (x) { return x.spend; })) || 1;
    var total = acts.reduce(function (s, x) { return s + x.spend; }, 0);

    var cost = $("m-cost");
    cost.innerHTML = "";
    acts.forEach(function (x) {
      var wrap = document.createElement("div");
      wrap.className = "bar-wrap";
      wrap.innerHTML =
        '<span class="bar-value">' + (x.spend || "-") + "</span>" +
        '<div class="bar" style="height:' + Math.round((x.spend / max) * 100) + '%"></div>' +
        '<span class="bar-label">Act ' + x.label + "</span>";
      cost.appendChild(wrap);
    });
    $("m-cost-total").textContent = total + " units";
    var rooms = Math.max(1, currentFloor() + (state.actsCleared * 15));
    $("m-cost-room").textContent = (total / rooms).toFixed(1);

    var trends = [
      { name: "Test coverage", series: [61, 63, 62, 66, 71, 74], unit: "%", good: "up" },
      { name: "Lint violations", series: [22, 17, 15, 9, 4, 0], unit: "", good: "down" },
      { name: "Suite pass rate", series: [88, 91, 90, 96, 99, 100], unit: "%", good: "up" }
    ];
    var q = $("m-quality");
    q.innerHTML = "";
    trends.forEach(function (t) {
      var first = t.series[0];
      var last = t.series[t.series.length - 1];
      var delta = last - first;
      var improving = t.good === "up" ? delta > 0 : delta < 0;
      var peak = Math.max.apply(null, t.series) || 1;
      var spark = t.series.map(function (v) {
        return '<span style="height:' + Math.max(8, Math.round((v / peak) * 100)) + '%"></span>';
      }).join("");
      var row = document.createElement("div");
      row.className = "trend";
      row.innerHTML =
        '<span class="trend-name">' + t.name + "</span>" +
        '<span class="trend-val ' + (improving ? "up" : "down") + '">' +
        (delta > 0 ? "+" : "") + delta + t.unit + "</span>" +
        '<div class="spark">' + spark + "</div>";
      q.appendChild(row);
    });

    var attempts = state.taken + state.skips;
    var gauges = [
      {
        name: "Skip ratio", value: attempts ? Math.round((state.skips / attempts) * 100) : 0,
        suffix: "%", tone: "good", note: "Refusing an offer is skilled play, and it pays focus."
      },
      {
        name: "Deck against cap", value: Math.round((state.deckSize / SOFT_CAP) * 100),
        suffix: "% of " + SOFT_CAP, tone: state.deckSize >= SOFT_CAP ? "warn" : "",
        note: "Bloat is invisible in a repo, so it lives on screen."
      },
      {
        name: "Clean-room streak", value: Math.min(100, state.streak * 10),
        suffix: " (" + state.streak + " rooms)", tone: "", note: "Broken by fleeing a room."
      },
      {
        name: "Acts cleared", value: Math.min(100, state.actsCleared * 20),
        suffix: " (" + state.actsCleared + ")", tone: "", note: "The climb has no level limit."
      }
    ];
    var g = $("m-discipline");
    g.innerHTML = "";
    gauges.forEach(function (x) {
      var el = document.createElement("div");
      el.className = "gauge";
      el.innerHTML =
        '<div class="gauge-head"><span>' + x.name + "</span><strong>" +
        x.value + x.suffix + "</strong></div>" +
        '<div class="gauge-track"><div class="gauge-fill ' + x.tone +
        '" style="width:' + Math.min(100, x.value) + '%"></div></div>' +
        '<div class="gauge-note">' + x.note + "</div>";
      g.appendChild(el);
    });
  }

  var GLYPH = {
    monster: "✦", elite: "✸", rest: "▲", shop: "◆",
    // resolveUnknown() defaults to "event", so an event needs its own glyph or
    // a resolved node renders the literal text "undefined".
    treasure: "▮", event: "◇", unknown: "?", boss: "☠"
  };

  // The path runs left to right, floor 1 to boss, as formats/map.md specifies.
  // Horizontal keeps all 15 floors plus the boss inside the panel with no
  // scrolling, which is what makes every node reachable by a click.
  function layoutFloors() { return MAP.rows + 1; }
  function xPct(row) { return ((row + 0.5) / layoutFloors()) * 100; }
  function yPct(col) { return ((col + 0.5) / MAP.cols) * 100; }

  function renderMap() {
    var canvas = els.mapCanvas;
    canvas.innerHTML = "";
    canvas.style.setProperty("--lanes", MAP.cols);
    $("map-boss").textContent = MAP.boss.name;
    $("map-act").textContent = actLabel(MAP.act) + " · seed " + MAP.seed;

    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "map-edges");
    svg.setAttribute("viewBox", "0 0 100 100");
    svg.setAttribute("preserveAspectRatio", "none");
    var locked = Boolean(state.activeRoom);
    var legalIds = locked ? [] : legalNodes().map(function (n) { return n.id; });

    MAP.nodes.forEach(function (n) {
      nextNodes(n).forEach(function (t) {
        var line = document.createElementNS("http://www.w3.org/2000/svg", "line");
        line.setAttribute("x1", xPct(n.row));
        line.setAttribute("y1", yPct(n.col));
        line.setAttribute("x2", xPct(t.row));
        line.setAttribute("y2", yPct(t.col));
        line.setAttribute("vector-effect", "non-scaling-stroke");
        var cls = "map-edge";
        if (n.id === state.currentId && legalIds.indexOf(t.id) !== -1) {
          cls += " open"; // the moves available right now
        } else if (MAP.cleared[n.id] && MAP.cleared[t.id]) {
          cls += " lit"; // the path already walked
        }
        line.setAttribute("class", cls);
        svg.appendChild(line);
      });
    });
    canvas.appendChild(svg);

    MAP.nodes.forEach(function (n) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "mnode k-" + n.kind;
      btn.style.left = xPct(n.row) + "%";
      btn.style.top = yPct(n.col) + "%";
      var legal = legalIds.indexOf(n.id) !== -1;
      if (MAP.cleared[n.id]) btn.classList.add("cleared");
      if (n.id === state.currentId) btn.classList.add("current");
      if (legal) btn.classList.add("legal");
      if (n.id === state.selectedId) btn.classList.add("selected");
      btn.disabled = !legal;
      var label = n.kind === "unknown" && n.resolved ? GLYPH[n.resolved] : GLYPH[n.kind];
      btn.textContent = label;
      btn.setAttribute("aria-label",
        "floor " + (n.row + 1) + " " + n.kind + (legal ? ", reachable" : ", not reachable"));
      if (legal) {
        btn.addEventListener("click", function () {
          state.selectedId = n.id;
          renderMap();
        });
      }
      canvas.appendChild(btn);
    });

    var here = node(state.currentId);
    if (here) {
      var tag = document.createElement("span");
      tag.className = "you-are-here";
      tag.textContent = "you are here";
      tag.style.left = xPct(here.row) + "%";
      tag.style.top = "calc(" + yPct(here.col) + "% + 20px)";
      canvas.appendChild(tag);
    }

    updateMapDetail();
  }

  var KIND_TITLE = {
    monster: "Monster room", elite: "Elite room", rest: "Campfire",
    shop: "Merchant", treasure: "Chest", unknown: "Unknown node", boss: "Act boss"
  };

  function updateMapDetail() {
    if (state.activeRoom) {
      els.enter.disabled = true;
      $("map-kind").textContent = "locked";
      $("map-title").innerHTML = "<strong>" + state.activeRoom.name + " is still open</strong>";
      $("map-prior").textContent =
        "One room at a time. Return to it or flee before choosing another node.";
      return;
    }
    var n = node(state.selectedId);
    els.enter.disabled = !n || !isLegal(n.id);
    if (!n) {
      $("map-kind").textContent = "—";
      $("map-title").innerHTML = "<strong>Pick a reachable node</strong>";
      $("map-prior").textContent = state.currentId
        ? "Only nodes joined to yours by an edge are legal."
        : "Any node on floor 1 is a legal entry.";
      return;
    }
    $("map-kind").textContent = n.kind === "unknown" ? "?" : n.kind;
    $("map-title").innerHTML = "<strong>" + KIND_TITLE[n.kind] + "</strong> · floor " + (n.row + 1);
    if (n.kind === "unknown") {
      $("map-prior").textContent =
        "Resolves on entry. Event is most likely, and monster pressure climbs each time a " +
        "node resolves to something else.";
    } else if (n.kind === "elite") {
      $("map-prior").textContent = "Harder room, and the reliable source of a relic.";
    } else {
      $("map-prior").textContent = "Reachable. Entering commits you to this room.";
    }
  }

  function renderIntent() {
    var e = state.currentEnemy;
    $("intent-kind").textContent = e.room + " room";
    $("intent-name").textContent = e.name;
    $("intent-text").textContent = e.intent;
    $("intent-blurb").textContent = e.blurb;
    $("intent-acceptance").textContent = e.acceptance;
  }

  function legalCards() {
    var room = state.currentEnemy.room;
    return ALL_CARDS.map(function (c) {
      return { card: c, legal: c.rooms.indexOf(room) !== -1 };
    });
  }

  function renderCombat() {
    var e = state.currentEnemy;
    $("combat-name").textContent = e.name;
    $("combat-telegraph").textContent = e.telegraph;
    var pct = Math.max(0, Math.min(100, Math.round((state.progress / e.clearAt) * 100)));
    $("combat-clear-fill").style.width = pct + "%";
    $("combat-clear-text").textContent = "Stability " + state.progress + " / " + e.clearAt;
    orbRow(els.combatOrbs, state.energy, state.energyMax);
    orbRow(els.chromeOrbs, state.energy, state.energyMax);

    // The hand holds only cards legal in this room. Illegal ones are listed
    // separately as flat chips, because a card-shaped control that refuses to
    // be played reads as a broken game no matter what text it carries.
    var all = legalCards();
    var cards = all.filter(function (x) { return x.legal; });
    var illegal = all.filter(function (x) { return !x.legal; });
    var n = cards.length;
    els.combatHand.innerHTML = "";
    cards.forEach(function (entry, i) {
      var c = entry.card;
      var played = state.playedThisTurn.indexOf(c.id) !== -1;
      var costly = state.energy < c.cost;
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "play-card " + c.rarity;
      if (!entry.legal) btn.classList.add("illegal");
      if (costly && entry.legal) btn.classList.add("too-costly");
      var mid = (n - 1) / 2;
      btn.style.setProperty("--rot", (i - mid) * 5 + "deg");
      btn.style.setProperty("--ty", Math.abs(i - mid) * 7 + "px");
      var reason = !entry.legal
        ? "Illegal here · " + c.rooms.join("/")
        : costly ? "Needs " + c.cost + " energy" : c.rooms.join(" · ");
      btn.innerHTML =
        '<span class="cost">' + c.cost + "</span>" +
        '<span class="notch" aria-hidden="true"></span>' +
        "<h4>" + c.title + "</h4>" +
        '<div class="body">' + c.body + "</div>" +
        '<div class="rtype">' + reason + "</div>";

      // A legal card you cannot afford yet still explains itself on click.
      if (played) {
        btn.disabled = true;
      } else if (costly) {
        btn.setAttribute("aria-disabled", "true");
        btn.addEventListener("click", function () {
          toast(c.title + " costs " + c.cost + " energy and you have " +
            state.energy + ". End the turn to refill.");
        });
      } else {
        btn.addEventListener("click", function () { playCard(c, btn); });
      }
      els.combatHand.appendChild(btn);
    });

    els.combatBench.innerHTML = "";
    if (illegal.length) {
      var label = document.createElement("span");
      label.className = "bench-label";
      label.textContent = "Not legal in a " + e.room + " room";
      els.combatBench.appendChild(label);
      illegal.forEach(function (entry) {
        var chip = document.createElement("span");
        chip.className = "bench-chip";
        chip.innerHTML = entry.card.title +
          ' <span class="bench-rooms">' + entry.card.rooms.join("/") + "</span>";
        els.combatBench.appendChild(chip);
      });
    }
  }

  function playCard(c, btn) {
    if (state.energy < c.cost) return;
    state.energy -= c.cost;
    state.playedThisTurn.push(c.id);
    state.progress = Math.min(state.currentEnemy.clearAt, state.progress + c.progress);
    btn.classList.add("committing");
    $("combat-log").textContent = "Log: played " + c.title + " (+" + c.progress + " progress)";
    var done = state.progress >= state.currentEnemy.clearAt;
    setTimeout(function () {
      renderCombat();
      if (done) toast("Room stabilized — run acceptance");
    }, 320);
  }

  function renderEvent() {
    $("event-title").textContent = EVENT.title;
    $("event-body").textContent = EVENT.body;
    els.eventChoices.innerHTML = "";
    EVENT.choices.forEach(function (ch) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "choice" + (ch.greedy ? " greedy" : "");
      btn.innerHTML = '<span class="c-label">' + ch.label + "</span>" +
        '<span class="c-consequence">' + ch.consequence + "</span>";
      btn.addEventListener("click", function () { resolveEvent(ch); });
      els.eventChoices.appendChild(btn);
    });
  }

  function resolveEvent(ch) {
    if (ch.id === "accept") {
      state.curses += 1;
      state.deckSize += 1;
      toast("Bloated Scope curse added · permanent until removed");
    } else if (ch.id === "cut") {
      toast("Cut scope · slice preserved");
    } else {
      toast("Parked in backlog");
    }
    completeNode();
  }

  function renderReward() {
    var chest = state.rewardMode === "treasure";
    var offers = chest ? TREASURE : OFFERS;
    $("reward-tab").textContent = chest ? "Chest · Room cleared" : "Reward · Room cleared";
    $("skip-main").textContent = chest ? "Skip — leave it closed" : "Skip — take nothing";
    $("skip-payout").textContent = "+" + SKIP_PAYOUT + " focus";
    els.rewardOffers.innerHTML = "";
    offers.forEach(function (o) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "offer " + o.rarity;
      btn.innerHTML = '<span class="notch" aria-hidden="true"></span>' +
        "<h4>" + o.title + "</h4><span class='rarity'>" + o.note + "</span>";
      btn.addEventListener("click", function () {
        if (state.deckSize >= SOFT_CAP) {
          // The shop already refuses at the cap; reward used to let the deck
          // grow past it, so the two paths disagreed.
          toast("Deck is at the soft cap (" + state.deckSize + "/" + SOFT_CAP +
            "). Prune at a campfire before taking another card.");
          return;
        }
        state.taken += 1;
        state.deckSize += 1;
        toast("Took " + o.title);
        completeNode();
      });
      els.rewardOffers.appendChild(btn);
    });
    $("reward-caption").textContent =
      "Run stats · taken " + state.taken + " / skipped " + state.skips +
      " · focus " + state.focus + " · deck " + state.deckSize + "/" + SOFT_CAP;
  }

  function renderCamp() {
    var sorted = DECK.slice().sort(function (a, b) { return a.plays - b.plays; });
    els.campList.innerHTML = "";
    sorted.forEach(function (card) {
      var row = document.createElement("button");
      row.type = "button";
      row.className = "deck-row" + (state.campPick === card.id ? " selected" : "");
      row.setAttribute("aria-pressed", state.campPick === card.id ? "true" : "false");
      var unplayed = card.plays === 0;
      var already = state.campMode === "smith" && card.upgraded;
      row.disabled = already;
      row.innerHTML = "<span>" + card.name + (card.upgraded ? " +" : "") + "</span>" +
        '<span class="meta ' + (unplayed ? "unplayed" : "") + '">' +
        (unplayed ? "unplayed · " : "") + card.plays + " plays · " + card.when + "</span>";
      if (!already) {
        row.addEventListener("click", function () {
          state.campPick = card.id;
          renderCamp();
        });
      }
      els.campList.appendChild(row);
    });
    els.campConfirm.disabled = !state.campPick;
    els.campConfirm.textContent = state.campMode === "prune" ? "Confirm prune" : "Confirm upgrade";
    $("camp-hint").textContent = state.campMode === "prune"
      ? "Prune removes a card for good. Unplayed cards sort first."
      : "Smith upgrades one card permanently. Both options spend the same floor.";
  }

  function renderShop() {
    els.shopWares.innerHTML = "";
    WARES.forEach(function (w) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ware" + (state.shopId === w.id ? " selected" : "");
      btn.setAttribute("aria-pressed", state.shopId === w.id ? "true" : "false");
      btn.innerHTML = '<span class="price">' + w.price + "</span>" +
        '<span class="kind">' + w.kind + "</span>" +
        "<h4>" + w.title + "</h4>";
      btn.addEventListener("click", function () {
        state.shopId = w.id;
        renderShop();
      });
      els.shopWares.appendChild(btn);
    });
    els.buy.disabled = !state.shopId;
  }

  function shortAsc(level) {
    return {
      0: "warn only", 5: "lint blocks", 10: "+ tests block",
      15: "+ coverage regression", 20: "every room reviewed"
    }[level];
  }

  function renderAscension() {
    els.ascLadder.innerHTML = "";
    ASCENSION.forEach(function (r) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "rung" + (r.level === state.ascPick ? " picked" : "");
      btn.setAttribute("aria-pressed", r.level === state.ascPick ? "true" : "false");
      btn.innerHTML = '<span class="tier">A' + r.level + "</span><span>" +
        shortAsc(r.level) + "</span>" +
        (r.level === state.ascension ? '<span class="applied-tag">applied</span>' : "");
      btn.addEventListener("click", function () {
        state.ascPick = r.level;
        renderAscension();
      });
      els.ascLadder.appendChild(btn);
    });
    var picked = ASCENSION.filter(function (r) { return r.level === state.ascPick; })[0];
    $("asc-label").textContent = "A" + picked.level;
    $("asc-blurb").textContent = picked.blurb;
    els.applyAsc.textContent = "Apply A" + picked.level;
  }

  /* ------------------------------ transitions ------------------------------ */
  function completeNode() {
    if (state.currentId) MAP.cleared[state.currentId] = true;
    state.activeRoom = null;
    state.selectedId = null;
    state.streak += 1;
    var cur = node(state.currentId);
    if (cur && cur.kind === "boss") {
      // No level limit. Beating the Heart continues the climb rather than
      // ending it, which is the whole point of a codebase that keeps going.
      var nextAct = MAP.act + 1;
      state.actsCleared += 1;
      if (MAP.act === HEART_ACT) {
        toast("The Heart falls · the climb continues into the endless acts");
      } else {
        toast(actLabel(MAP.act) + " cleared · entering " + actLabel(nextAct));
      }
      if (mapFor(state.seed, nextAct)) {
        loadAct(state.seed, nextAct);
      } else {
        toast("Cleared all " + ACT_COUNT + " acts bundled in this build. The generator " +
          "is unbounded; re-run emit-js with a higher --acts to keep climbing.");
        state.currentId = null;
      }
    }
    showScreen("map");
  }

  function beginCombat() {
    if (!state.currentEnemy) state.currentEnemy = enemyFor(node(state.currentId) || entries()[0]);
    state.energy = state.energyMax;
    state.playedThisTurn = [];
    $("combat-log").textContent = "Log: combat begun · hand filtered to the room";
  }

  function enterNode() {
    if (state.activeRoom) return; // map is locked while a room is open
    var n = node(state.selectedId);
    if (!n || !isLegal(n.id)) return;
    state.currentId = n.id;

    var kind = n.kind;
    if (kind === "unknown") {
      kind = resolveUnknown(n);
      toast("Unknown resolved · " + kind);
    }

    if (kind === "monster" || kind === "elite" || kind === "boss") {
      state.currentEnemy = enemyFor(kind === n.kind ? n : Object.assign({}, n, { kind: kind }));
      state.progress = 0;
      state.energy = state.energyMax;
      state.playedThisTurn = [];
      openRoom(state.currentEnemy.name, state.currentEnemy.room, "intent");
      return;
    }
    if (kind === "event") {
      openRoom(EVENT.title, "design", "event");
      return;
    }
    if (kind === "treasure") {
      state.rewardMode = "treasure";
      openRoom("Chest", "infra", "reward");
      return;
    }
    if (kind === "rest") {
      state.campMode = "prune";
      state.campPick = null;
      syncCampModeButtons();
      openRoom("Campfire", "orient", "campfire");
      return;
    }
    if (kind === "shop") {
      state.shopId = null;
      openRoom("Merchant", "orient", "shop");
    }
  }

  function openRoom(name, room, screen) {
    state.activeRoom = { name: name, room: room, screen: screen };
    showScreen(screen);
  }

  function doFlee() {
    if (!window.confirm("Flee this room? The room stays uncleared and your streak resets.")) {
      return;
    }
    state.activeRoom = null;
    toast("Fled · streak broken, the room stays uncleared");
    state.streak = 0;
    state.currentId = null;
    state.selectedId = null;
    showScreen("map");
  }

  function runAcceptance() {
    var e = state.currentEnemy;
    if (state.progress < e.clearAt) {
      $("combat-log").textContent = "Log: acceptance failed · room not yet stable";
      toast("Acceptance failed — keep playing");
      return;
    }
    $("combat-log").textContent = "Log: acceptance passed · room clear";
    state.rewardMode = "card";
    els.fanfare.hidden = false;
    setTimeout(function () {
      els.fanfare.hidden = true;
      showScreen("reward");
    }, 640);
  }

  function endTurn() {
    state.energy = state.energyMax;
    state.playedThisTurn = [];
    $("combat-log").textContent = "Log: end turn · " + state.currentEnemy.turnEffect;
    renderCombat();
  }

  function syncCampModeButtons() {
    document.querySelectorAll('[data-action="camp-mode"]').forEach(function (b) {
      var on = b.getAttribute("data-mode") === state.campMode;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function applyFacet(name) {
    var active = document.querySelector('.screen[data-screen="' + name + '"]');
    if (!active) return;
    var facet = getComputedStyle(active)
      .getPropertyValue("--facet-" + active.getAttribute("data-facet")).trim();
    document.documentElement.style.setProperty("--facet", facet || "var(--facet-map)");
  }

  function applyTheme() {
    document.documentElement.setAttribute("data-theme", state.theme);
    $("theme-label").textContent = state.theme === "light" ? "Dark" : "Light";
    // --facet was resolved to a literal hex under the old theme, so it has to
    // be recomputed or the facet tab keeps the wrong colour and drops below AA.
    applyFacet(state.screen);
  }

  /* ------------------------------ wiring ------------------------------ */
  function onClick(e) {
    var t = e.target.closest("[data-go],[data-action]");
    if (!t) return;
    var go = t.getAttribute("data-go");
    var action = t.getAttribute("data-action");

    if (go) {
      if (go === "ascension") state.returnScreen = state.screen === "ascension" ? "map" : state.screen;
      if (go === "combat") beginCombat();
      if (go === "intent" && !state.currentEnemy) {
        state.currentEnemy = enemyFor(entries()[0]);
      }
      showScreen(go);
      return;
    }

    switch (action) {
      case "refresh-prior":
        toast(PRIORS[state.priorSeed % PRIORS.length]);
        state.priorSeed += 1;
        break;
      case "new-climb":
        var theme = state.theme;
        var seed = (state.seed + 1) % SEED_COUNT;
        resetState();
        state.theme = theme;
        state.seed = seed;
        loadAct(seed, 1);
        applyTheme();
        toast("New climb · seed " + seed);
        showScreen("map");
        break;
      case "flee": doFlee(); break;
      case "run-acceptance": runAcceptance(); break;
      case "end-turn": endTurn(); break;
      case "skip-reward":
        state.skips += 1;
        state.focus += SKIP_PAYOUT;
        state.tokens += SKIP_PAYOUT;
        toast("Skipped · +" + SKIP_PAYOUT + " focus, deck stays lean");
        completeNode();
        break;
      case "camp-mode":
        state.campMode = t.getAttribute("data-mode");
        state.campPick = null;
        syncCampModeButtons();
        renderCamp();
        break;
      case "camp-leave": completeNode(); break;
      case "shop-leave": completeNode(); break;
      case "asc-cancel": showScreen(state.returnScreen || "map"); break;
    }
  }

  function wire() {
    els = {
      chrome: $("chrome"), chromeEnergy: $("chrome-energy"), chromeOrbs: $("chrome-orbs"),
      banner: $("banner"), bannerText: $("banner-text"), bannerActions: $("banner-actions"),
      toast: $("toast"), fanfare: $("fanfare"),
      mapCanvas: $("map-canvas"), mapGraph: $("map-graph"), enter: $("btn-enter"),
      combatHand: $("combat-hand"), combatBench: $("combat-bench"), combatOrbs: $("combat-orbs"),
      eventChoices: $("event-choices"), rewardOffers: $("reward-offers"),
      campList: $("camp-list"), campConfirm: $("btn-camp-confirm"),
      shopWares: $("shop-wares"), buy: $("btn-buy"),
      ascLadder: $("asc-ladder"), applyAsc: $("btn-apply-asc")
    };

    document.querySelector(".app-shell").addEventListener("click", onClick);
    els.enter.addEventListener("click", enterNode);

    els.campConfirm.addEventListener("click", function () {
      var idx = -1;
      for (var i = 0; i < DECK.length; i++) {
        if (DECK[i].id === state.campPick) { idx = i; break; }
      }
      if (idx < 0) return;
      if (state.campMode === "prune") {
        var name = DECK[idx].name;
        DECK.splice(idx, 1);
        state.deckSize = Math.max(0, state.deckSize - 1);
        toast("Pruned " + name);
      } else {
        DECK[idx].upgraded = true;
        toast("Upgraded " + DECK[idx].name);
      }
      state.campPick = null;
      completeNode();
    });

    els.buy.addEventListener("click", function () {
      var ware = WARES.filter(function (w) { return w.id === state.shopId; })[0];
      if (!ware) return;
      if (state.tokens < ware.price) { toast("Not enough focus tokens"); return; }
      if (ware.kind === "card" && state.deckSize + 1 > SOFT_CAP) {
        toast("Soft cap — prune before buying");
        return;
      }
      state.tokens -= ware.price;
      if (ware.kind === "card") state.deckSize += 1;
      toast("Bought " + ware.title);
      state.shopId = null;
      completeNode();
    });

    els.applyAsc.addEventListener("click", function () {
      state.ascension = state.ascPick;
      toast("Applied A" + state.ascension);
      showScreen(state.returnScreen || "map");
    });

    $("btn-theme").addEventListener("click", function () {
      state.theme = state.theme === "light" ? "dark" : "light";
      applyTheme();
      toast(state.theme === "dark" ? "Dark theme" : "Light theme");
    });

    $("btn-reset").addEventListener("click", function () {
      var theme = state.theme;
      resetState();
      state.theme = theme;
      applyTheme();
      toast("Run reset");
      showScreen("title");
    });
  }

  /* ------------------------------ content audit ------------------------------ */
  function bestProgressInOneTurn(room, energy) {
    var legal = ALL_CARDS.filter(function (c) { return c.rooms.indexOf(room) !== -1; });
    var best = 0;
    // Small hand, so brute force every affordable subset.
    for (var mask = 0; mask < 1 << legal.length; mask++) {
      var cost = 0;
      var prog = 0;
      for (var i = 0; i < legal.length; i++) {
        if (mask & (1 << i)) {
          cost += legal[i].cost;
          prog += legal[i].progress;
        }
      }
      if (cost <= energy && prog > best) best = prog;
    }
    return best;
  }

  /* Every room must be clearable with the starting hand. A room whose type has
     no legal cards is not a hard fight, it is a dead end, and that is the kind
     of content mistake that should not reach a player. */
  function auditContent() {
    var rooms = {};
    MONSTERS.concat(ELITES).forEach(function (e) { rooms[e.room] = Math.max(rooms[e.room] || 0, e.clearAt); });
    MAPS.forEach(function (m) {
      if (m.boss && m.boss.room) rooms[m.boss.room] = Math.max(rooms[m.boss.room] || 0, 5);
    });
    var problems = [];
    Object.keys(rooms).forEach(function (room) {
      var perTurn = bestProgressInOneTurn(room, DEFAULTS.energyMax);
      if (perTurn === 0) {
        problems.push("no card is legal in a " + room + " room");
      } else if (perTurn * 3 < rooms[room]) {
        problems.push(room + " needs more than three turns to clear");
      }
    });
    if (problems.length) {
      console.error("Spire content audit failed:\n  " + problems.join("\n  "));
    }
    return problems;
  }

  /* ------------------------------ boot ------------------------------ */
  if (!MAPS.length) {
    document.addEventListener("DOMContentLoaded", function () {
      document.body.innerHTML =
        '<p style="font-family:sans-serif;padding:40px">mapdata.js is missing. ' +
        "Regenerate it with <code>python3 scripts/mapgen.py emit-js &gt; mapdata.js</code>.</p>";
    });
    return;
  }

  resetState();
  auditContent();
  wire();
  applyTheme();
  syncCampModeButtons();
  showScreen("title");
})();
