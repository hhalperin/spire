# Spire design system (v1)

Shared tokens and rules. Facet formats may **specialize** these; they may not invent a second type scale or a glow stack.

> **v1 reversed v0's default.** v0 said "ink on paper … not game-like", and that
> was the right call for a wireframe: it kept the layout honest while the layout
> was the thing being decided. It is the wrong call for a game. Slay the Spire is
> a torchlit stone corridor with parchment in your hands, and reading as a
> dashboard is the fastest way to stop feeling like one. So **stone and ember is
> the default and the paper palette is the light theme** — every contrast
> decision recorded below still holds, inverted where it has to be. Implemented
> in `app/app.css`.

## North-star look

Deckbuilder clarity in a torchlit room. Not dashboard chrome, not generic AI purple.

- Stone and ember by default; ink on paper as the light theme
- Parchment is for anything you hold — cards, and only cards
- One accent at a time (facet accent or class accent — not both competing)
- Geometry over gradients; one warm light source, no continuous glow
- Motion: 2–3 intentional beats per facet max

## Color tokens

### Dark (default)

```css
/* Stone: the spire itself. */
--stone-900: #0b0908;   /* page */
--stone-800: #131010;
--stone-700: #1b1715;   /* panels */
--stone-600: #241f1a;
--line:      #3b322a;

/* Parchment: anything you hold. */
--parchment: #e7dbc0;
--parchment-ink: #221c14;

--ink:       #f2e9d8;
--ink-soft:  #cabda4;
--muted:     #8d8171;

/* Ember is the torch — the one warm light in the room. */
--ember:     #d9832f;
--ember-hot: #f2ac57;
--gold:      #d9b464;
--blood:     #cf5648;   /* danger */
--moss:      #74b183;   /* safe */
--violet:    #ab98d8;
--steel:     #83b3d2;

/* Small text needs its own variant. The fills above measure 2.7:1-3.9:1 at
   caption sizes; these are the versions that clear AA as text. */
--moss-text:  #93c9a0;
--gold-text:  #e6c884;
--ember-text: #f0ac66;
--blood-text: #e07f72;

/* Facet accents — dark */
--facet-title: #d9b464;  --facet-map:  #83b3d2;  --facet-intent:   #d9832f;
--facet-combat: #cf5648; --facet-reward: #74b183; --facet-campfire: #f2ac57;
--facet-shop:  #ab98d8;  --facet-event: #8fb583; --facet-deck:     #cabda4;

/* Room types — dark. Never used as colour alone; always with a glyph or label. */
--room-bug: #d76a5f;   --room-feature: #83b3d2; --room-refactor: #ab98d8;
--room-design: #9a9ad0; --room-docs: #8fb583;   --room-infra: #d79a6a;
--room-orient: #9a9084;
```

### Light (the v0 paper palette, unchanged)

```css
--spire-paper: #f3efe6;
--spire-ink: #1c1916;
--spire-muted: #6b645c;
--spire-line: #c9c1b4;
--spire-panel: #ebe4d8;
--spire-danger: #8f2d2d;
--spire-safe: #2f5d3a;
--spire-focus: #1c1916; /* primary CTA = solid ink, not neon */

/* Class accents — sparse use (pip, map node ring, title chip) */
--class-defect: #3a5f7a;    /* cool steel */
--class-silent: #4a6741;    /* quiet green */
--class-ironclad: #8a4b2e;  /* iron rust */
--class-watcher: #5c4d7a;   /* dusk violet — sparingly */
--class-colorless: #6b645c; /* muted */

/* Facet accents — identify the mode at a glance */
--facet-map: #3a5f7a;
--facet-intent: #8a4b2e;
--facet-combat: #8f2d2d;
--facet-reward: #2f5d3a;
--facet-campfire: #9c5527;
--facet-shop: #5c4d7a;
--facet-ascension: #1c1916;
--facet-event: #4a6741;
```

### Theme resolution

Three states, and the order matters. An MCP App host sends `hostContext.theme`,
which the client writes to `[data-theme]`; with no host, `prefers-color-scheme`
decides; and **the dark palette is the bare `:root` default**, so a container
that paints nothing behind us can never leave the page unpainted. Never give a
colour its only definition inside a media query or a `[data-theme]` block.

**Rules**

- Any accent used as a fill behind small text must clear 4.5:1 against its own
  ground. Campfire was `#a65d2e` and measured 4.33:1 under the facet tab, so on
  paper it is `#9c5527` (4.89:1). Accents that only ever carry large text or act
  as borders are exempt.
- Accents used *as* small text need their own darker variant rather than reuse
  of the fill value. See `--safe-text`, `--gold-text`, `--campfire-text` in the
  demo stylesheet.
- Never purple-on-white gradient theme. Watcher violet is an accent pip only.
- Never make Skip look disabled. On Reward, Skip is the visual default.
- Danger only for flee / fail / curse — not for primary navigation.

## Typography

**Chosen, and shipped.** One display, one body, one mono — all SIL OFL, all
subset and inlined as base64 woff2 by `tools/build-fonts.py`, because an MCP App
renders under a deny-by-default CSP and a font CDN would be both a declared
external origin and a render-time phone-home.

| Role | Family | Where |
| --- | --- | --- |
| Display | **Fraunces** 600/700 | Facet titles, enemy names, card titles, the wordmark |
| Body | **Newsreader** 400/500 | Intents, blurbs, card text |
| Mono | **IBM Plex Mono** 400/500 | Acceptance commands, sensor chips, energy counts |

UI furniture (chips, buttons, chrome, legends) uses the system sans stack
deliberately — it is chrome, not brand.

Scale:

| Token | Size | Use |
| --- | --- | --- |
| `hero` | 46–64 | Wordmark, room-clear stamp |
| `display` | 26–34 | Facet title, enemy name |
| `title` | 22–24 | Room / enemy name |
| `body` | 15–16 | Intent, descriptions |
| `caption` | 12–13 | Meta (floor, act, energy) |
| `mono` | 13 | Commands |

## Spacing & layout chrome

- App shell max width ~1080px inside IDE panel; map may go full width.
- 8px grid. Panels: 12–16px padding.
- Top chrome always: Act · Floor · Class chip · Energy (if in room) · active-room banner.
- Bottom: facet-specific (hand / choices / CTAs) — never duplicate primary CTA top and bottom.

## Motion budget

| Beat | Duration | Where |
| --- | --- | --- |
| Card commit | 200–400ms | Combat |
| Intent reveal | 300ms fade/slide | Intent |
| Room clear | 500–700ms | Combat → Reward |
| Skip settle | 200ms | Reward |
| Map node select | 150ms | Map |

No continuous particle glow. No slot-machine reward roll.

## Components (shared)

- **Node** — map circle/diamond by kind  
- **Card** — title, cost pip, 2–3 lines body, rarity notch  
- **Intent bar** — telegraph + plain-language consequence  
- **Energy pips** — filled/empty  
- **Banner** — active room lock  
- **CTA solid** / **CTA ghost** / **CTA danger**

## Accessibility

- Contrast ≥ WCAG AA on paper/ink  
- Focus rings on all controls (ink outline, 2px)  
- Don’t convey room kind by color alone — shape + label  
- Animations respect `prefers-reduced-motion`

## Facet specialization rule

Each format doc may set:

1. **Hero region** (what owns the first viewport)  
2. **Primary action** (exactly one)  
3. **Forbidden chrome** (e.g. no hand on Map)  
4. **Copy voice** (imperative, terse, in-world)

If two facets share the same hero region pattern, merge them — they’re not distinct enough.
