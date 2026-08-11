# Format — Ascension

**Mode purpose:** Player-chosen difficulty for the same climb.  
**Player question:** “How strict should my gates be?”  
**Primary action:** Apply tier (after explicit confirm).  
**Forbidden:** Auto-raise, burying A0, mixing card rewards into this screen.

## Layout

```
┌─────────────────────────────────────────┐
│ chrome                                  │
├─────────────────────────────────────────┤
│ HERO: ladder A0 → A5 → A10 → A15 → A20  │
│ current tier marked                     │
├─────────────────────────────────────────┤
│ DETAIL: what this tier blocks           │
│ (lint / test / coverage / every-room)   │
├─────────────────────────────────────────┤
│ [ Apply A# ]     ghost: Cancel          │
└─────────────────────────────────────────┘
```

Hero = **ladder**. Stark ink accent — seriousness, not hype.

## Accent

`--facet-ascension` (ink).

## Interaction

1. Selecting a rung updates DETAIL only.  
2. Apply confirms; calls `/spire:ascend` engine path.  
3. A0 always available.  
4. No “recommended for you” autopick — player boast/commitment.

## Copy voice

- Good: “A10 — lint + tests can block Stop.”  
- Bad: “Optimize your QOL journey with smart difficulty.”

## Do / Don’t

| Do | Don’t |
| --- | --- |
| Show effects before apply | Raise silently |
| Keep A0 honest | Hide warn-only |
| One ladder | Separate “modes” maze |

## Background

None of its own. Ascension is a setting, not a room; it renders over whatever scene the screen it was opened from declared. Nothing here should depend on what is behind it.
