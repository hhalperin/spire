# Template — facet format

Copy to `formats/<facet>.md` when adding a surface.

## Facet name

**Mode purpose:** one sentence.  
**Player question this screen answers:** …  
**Primary action (exactly one):** …  
**Forbidden on this screen:** …

## Layout (regions)

```
┌─────────────────────────────────────────┐
│ chrome                                  │
├─────────────────────────────────────────┤
│ HERO                                    │
│                                         │
├──────────────────┬──────────────────────┤
│ SECONDARY        │ SUPPORTING           │
├──────────────────┴──────────────────────┤
│ ACTIONS                                 │
└─────────────────────────────────────────┘
```

Describe each region: content types, max items, empty states.

## Type & accent

- Facet accent token: `--facet-…`
- Display label: e.g. `MAP`
- Body hierarchy: …

## Interaction rules

1. …
2. …

## Motion

- Enter: …
- Success: …
- Cancel / back: …

## Copy voice

Examples of good / bad microcopy.

## Content bindings

Which schema fields populate which regions (link `content-schema.md`).

## Background

Every screen stands somewhere. Name the scene, or say plainly that this surface
is an overlay and has none — an unanswered slot here is how a facet ships with
text sitting on a busy patch.

- **Scene** — the entry in `content/scenes.json` → `screens.<screen>`. Add one
  there first; the JSON is the source of the background, not a description of it.
- **Safe area** — the rectangle(s) content occupies, in normalised frame
  coordinates. Silhouette crossing it is attenuated rather than clipped.
- **Modifier** — if this screen is an existing place under different pressure
  (elite, calm), use a modifier instead of declaring a new scene.
- **Light** — where the one warm source sits, and how far it reaches.
- **Layers** — which of the nine the scene uses.

Then run `node tools/scenes.mjs` and look at it, and `node tools/shoot.mjs`,
which samples real pixels inside every safe rectangle and fails if the
background steps harder than the legibility ceiling behind your text.

## Wireframe

`wireframes/<facet>.html`

## Do / Don’t

| Do | Don’t |
| --- | --- |
| … | … |
