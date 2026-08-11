# Format — Active-room chrome

**Mode purpose:** Global lock reminding you one room is active.  
**Player question:** “Can I start something else?” → No.  
**Primary action:** Return to room (or Flee from inside room).  
**Forbidden:** Dismissing the banner without fleeing/clearing; starting a second room.

## Layout

Persistent top banner on Map and any non-room facet while `active_room != null`:

```
┌─────────────────────────────────────────┐
│ ● ROOM ACTIVE: Flaky Suite · bug        │
│   [ Return ]            text: Flee…     │
└─────────────────────────────────────────┘
```

## Rules

1. Banner uses combat accent bar (left edge).  
2. Map nodes non-interactive under lock.  
3. Flee always confirms.  
4. Banner is the single-task policy made visible — entertainment + discipline.

## Do / Don’t

| Do | Don’t |
| --- | --- |
| Name the room | Vague “busy” spinner |
| One-click return | Allow shadow multitask |
| Confirm flee | Silent cancel |

## Background

None. The active-room banner is an overlay, not a screen — it sits on top of whatever scene the room behind it declared, and must stay readable against every one of them. That is why the banner carries its own opaque surface rather than trusting the background beneath it.

If you ever make the banner translucent, it needs a safe rectangle in `content/scenes.json` for every scene it can appear over, and `tools/shoot.mjs`'s contrast check has to sample there.
