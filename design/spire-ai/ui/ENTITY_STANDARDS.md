# Entity standards

How a real codebase is rendered as a run. One vocabulary, one visual language,
one rule for deciding which object a thing becomes.

Companion to [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md), which owns tokens and motion,
and to [../sts-fidelity.md](../sts-fidelity.md), which owns what we port from
Slay the Spire and what we refuse.

## The deciding rule

Most mapping arguments come from asking "what does this feel like?" instead of
the question that actually settles it.

> **Ask how the thing is spent.** Not what it is.

| How it is spent | Object |
| --- | --- |
| Costs budget each time you invoke it, and you choose when | **Card** |
| Costs nothing and applies always, without being invoked | **Relic** |
| Costs nothing and fires on an event, without you choosing | **Power** |
| Consumed once, then gone | **Potion** |
| Costs you every turn and you did not want it | **Curse** |
| Has to be defeated once, and then it is done | **Enemy** |

Everything below follows from that table. When a new kind of thing shows up,
apply the rule rather than adding a category.

## Codebase to run

### Rooms, the work itself

| In the repo | Object | Tier | Cleared by |
| --- | --- | --- | --- |
| A bug with a reproduction | Monster | monster | A failing test that goes green |
| A feature with a written Done | Monster | monster | A vertical slice shipped |
| A refactor with callers to migrate | Monster | monster | One definition, all callers moved |
| A flaky suite, a perf cliff, a migration, an auth hole | Elite | elite | The specific gate named on the room |
| An architecture lock, a launch, a compliance gate | Boss | boss | The act's acceptance |
| A design fork, a scope trap, a prioritisation call | Event | event | Picking one branch |
| Unread work, an unlabelled issue, an unknown | Unknown node | unknown | Resolves on entry |

**The hard constraint.** A room must have a *checkable* acceptance. If clearing
it is a human judgement call, it is not a fight and must not be given an intent.
See the intent rule below.

### Objects you own

| In the repo | Object | Why, by the deciding rule |
| --- | --- | --- |
| A skill or playbook in `.claude/skills/` | **Card** | You invoke it deliberately and it costs attention |
| A rule in `CLAUDE.md` | **Relic** | Always on, never invoked, costs nothing per use |
| A hook in `settings.json` | **Power** | Fires on an event without you choosing |
| Installed standing tooling, a linter, CI, a type checker | **Relic** | Passive and permanent once installed |
| A one-shot diagnostic, `git bisect`, a profiler run, a spike, a colleague's hour | **Potion** | Consumed once and gone. Cannot be hoarded |
| Accepted tech debt, a deprecated API you still call, a TODO you agreed to carry | **Curse** | Costs you continuously and you did not want it |
| An MCP server or a generator you call on demand | **Card** | Invoked, costs budget |

Two distinctions people get wrong.

**Tooling splits by permanence, not by being a tool.** A linter that runs on
every commit is a relic. A profiler you run once to find one bottleneck is a
potion. Same category of software, different object, because they are spent
differently.

**A skill is not a relic.** Adding a skill costs attention every time the agent
loads and considers it, which is exactly what makes deck size a real cost. A
rule in `CLAUDE.md` is cheap to carry and expensive to violate. Filing skills as
relics would erase the deck-bloat tension that makes skipping meaningful.

### Run scoping

The deck, relics, powers, potions and curses are **run state**. They live in
`.spire/`. The code is not run state and is never destroyed by a loss. Losing a
run discards what you assembled, not what you built. That is the honest
substitute for permadeath, recorded in the fidelity ledger.

## Visual language

Every object gets a silhouette, an accent source, and a required metadata line.
Silhouette carries the meaning, so the objects stay distinguishable in
greyscale and for colour-blind readers. Accent is secondary.

No two node kinds may share a silhouette, which is why Shop is a hexagon and
Unknown owns the diamond. An earlier draft gave both a diamond and broke its own
rule. Node shapes follow [formats/map.md](formats/map.md).

| Object | Silhouette | Glyph | Accent from | Required metadata |
| --- | --- | --- | --- | --- |
| Card | Tall rounded rectangle, cost pip top-left, rarity notch top-right | none | Room types it is legal in | cost, legal rooms, plays |
| Relic | Circle with a ring | `●` | Class accent | the rule, in one line |
| Power | Hexagon | `⚡` | Facet accent of its event | hook event, trigger |
| Potion | Downward flask, narrow base | `▽` | Its own effect colour | what it is spent on, uses left |
| Curse | Torn rectangle, dashed border | `✖` | Danger | why it is carried, what it costs |
| Monster | Circle | `✦` | Room type | room type, acceptance |
| Elite | Larger circle, double ring | `✸` | Room type, intensified | room type, acceptance, reward |
| Boss | Largest circle, filled ring | `☠` | Ink | act, acceptance |
| Rest | Rounded square | `▲` | Campfire warm | options available |
| Shop | Hexagon | `◆` | Shop violet | currency held |
| Treasure | Small square | `▮` | Gold | what it contains |
| Event | Diamond, dashed border | `◇` | Event green | its choices and their costs |
| Unknown | Diamond, solid border | `?` | Muted until resolved | prior, once resolved the real glyph |

### Room type palette

Room type is the single most repeated signal in the UI, so it gets its own scale
and nothing else may use these hues as a primary.

```css
--room-bug:      #8f2d2d;  /* something is broken */
--room-feature:  #3a5f7a;  /* something is missing */
--room-refactor: #5c4d7a;  /* something is tangled */
--room-design:   #4a4a6a;  /* something is undecided */
--room-docs:     #4a6741;  /* something is unexplained */
--room-infra:    #8a4b2e;  /* something is unreliable */
--room-orient:   #6b645c;  /* nothing is known yet */
```

Pair every use with the glyph or the label. Never colour alone.

### Rarity

Rarity encodes **how broadly the card applies**, not how much you like it.

| Rarity | Notch | Meaning |
| --- | --- | --- |
| Common | Outline | Legal in most room types. The backbone of a deck |
| Uncommon | Filled, class accent | Legal in two or three room types |
| Rare | Filled, gold | Narrow and decisive. Often a trap in the wrong deck |

A card that is legal everywhere and always correct is not rare. It is a relic
that has been filed wrong.

## Rules the UI must hold

1. **Silhouette before colour.** Every object is identifiable with the palette
   removed. Test this by rendering greyscale.
2. **Cost is always visible on anything that has one.** A card without its cost
   pip is a lie about what it will take.
3. **Deck size is always on screen during a run.** Bloat is invisible in a
   repository, so the count has to be visible in the UI or skipping never feels
   like a win.
4. **Refusal shows its payout.** Skipping increments a number the player can see.
5. **An intent is only shown when a deterministic check backs it.** No sensor,
   no intent. A guess presented as a telegraph is worse than showing nothing,
   because a range players cannot resolve produces stalling rather than caution.
6. **Nothing is granted that was not earned in the run.** A starting deck is
   small and generic on purpose.
7. **Curses are never hidden.** They render in the deck at full contrast with
   their cost stated.
8. **No object may be added without a pool id.** Content lives in `content/`
   and `packs/`, never inline in a script.

   *This debt is now paid.* The rule used to convict our own demo: `demo.js`
   held its cards, enemies, events, wares and objects inline, and only bosses
   had been extracted. All of it now lives in `content/` — `enemies.json`,
   `cards.json`, `events.json`, `shop.json`, `objects.json` alongside the
   original `bosses.json` — and is read by `scripts/run.py`, which is the single
   implementation of the run loop. `tests/test_run.py` enforces the rule
   mechanically: every shop ware, treasure entry, starter card and event effect
   must resolve to a real pool id, or the suite fails.

   The browser sketch under `design/spire-ai/ui/demo/` is retained as the low-fi
   reference. The shipping client is `app/`.

## Metrics surfaces

Metrics are a **separate facet**, never chrome on a room. A room shows one
thing, which is the room.

Three panels, in this order.

| Panel | Answers | Shows |
| --- | --- | --- |
| Cost | What did this climb cost | Spend per act, spend per room, cumulative |
| Quality | Is the codebase better than when I started | Coverage, lint, test pass rate, as trends |
| Discipline | Am I playing well | Skip ratio, deck against cap, clean-room streak, elites taken |

Trends, not snapshots. A single coverage number says nothing. A direction says
everything. Any metric that cannot be measured from the repository or the run
log does not belong on this page.

## Endless

The climb has no level limit. Acts 1 through 3 are the standard ascent, act 4 is
the Heart, and every act after that is generated the same way with elite density
continuing to climb.

This is not a victory lap. It is the honest shape of the domain, because a
codebase does not finish. What changes past the Heart is the labelling and the
pressure, not the rules.

| Act | Label | Boss pool |
| --- | --- | --- |
| 1 to 3 | `Act I` to `Act III` | Per-act pools |
| 4 | `Act IV · the Heart` | The Heart of the Codebase |
| 5 and up | `Act N · endless` | The endless pool |

Beating the Heart continues the run rather than ending it. The run-complete
screen is a milestone, not a terminus.
