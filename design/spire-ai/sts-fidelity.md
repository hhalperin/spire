# Slay the Spire fidelity ledger

Status: living. Scope: Slay the Spire (2019) in Parts 1-5; Slay the Spire 2
(Early Access) in Part 6.

This is the reference we port against. Part 1 and Part 2 document what the
original game actually does. Part 3 ranks which properties are load-bearing.
Part 4 is the ledger, one row per system, recording whether we emulate it,
adapt it, or refuse it, and why. Part 5 names what gets built first. Part 6 does
the same job for the sequel, which shipped after this document was first written
and changed several things worth porting.

The ledger is the point. A mechanics dump with no decision attached to it is
trivia. Every row here has a verdict.

## Part 1. The combat layer

### Turn structure

Strictly alternating. The player takes a full turn, then every enemy acts, then
a round-bookkeeping phase runs, then the next player turn begins. The player
always moves first.

The original separates **end of turn** from **end of round**. They are distinct
hooks firing at different times, and duration debuffs tick on the round hook.
Getting this wrong changes how long every debuff lasts.

Order at the start of a player turn, in the shipped game's sequence:

1. Each living enemy runs its end-of-turn triggers.
2. `atEndOfRound` fires on player powers, then enemy powers. Vulnerable, Weak
   and Frail tick down here.
3. Per-turn counters reset.
4. Start-of-turn relics, then cards, then powers, then orbs.
5. The turn counter increments.
6. **Block is cleared.** Skipped entirely under Barricade or Blur. Calipers
   reduces it to losing exactly 15 instead of all of it.
7. The draw is queued, and constructing that draw is what recharges energy.
8. The End Turn button is enabled.

Two details matter for any port. Energy refill is bound to the start-of-turn
draw rather than being its own step. Block is cleared before the draw, so
start-of-turn block effects survive the clear.

### Energy

Base 3 for all four characters. At turn start energy is **set**, not added, so
unspent energy is discarded. Ice Cream is the relic that makes it additive.

X-cost cards have internal cost -1. They consume the entire pool, are legal to
play at 0 energy, and still count as a card played, which advances every
cards-played counter.

### Piles

Four locations. Draw pile is an ordered hidden stack. Hand caps at 10. Discard
pile is unordered and viewable. Exhaust is terminal.

Default draw is 5 per turn. When the draw pile empties mid-draw the game draws
what is there, shuffles the discard pile into the draw pile, then finishes the
draw, so drawing is seamless across a reshuffle. If both piles are empty the
draw is a no-op.

Overflow has **asymmetric** rules, and this is a detail most secondary sources
get wrong. Cards you would *draw* into a full hand are not drawn at all and stay
on top of the draw pile. Cards *generated* into a full hand go to the discard
pile.

Exhaust matters for three reasons. It thins the deck inside a single combat,
which raises the quality of every later draw. It is the only in-combat way to
delete Status and Curse cards. And it is a trigger resource that whole
archetypes are built on.

Powers are a special case. On play a Power card vanishes to no pile at all, so
it does not fire on-Exhaust effects.

### Block, HP, and damage

Block absorbs damage before HP and is cleared at the start of its owner's turn.
Enemy block therefore survives your whole turn and must be chewed through.

The damage pipeline is fixed and runs **once per hit**. Base damage, then
additive modifiers such as Strength, then multiplicative ones such as Weak, then
the target's multipliers such as Vulnerable, then a clamp, then **a single floor
operation at the very end**.

That single floor is what produces the game's learnable arithmetic quirks. Two
Strength plus a 6-damage Strike into a Vulnerable target is `floor((6+2) * 1.5)`
which is 12, not 11. Weak against a three-hit attack costs about 40 percent
rather than 25, because the floor applies per hit.

HP loss is a **separate channel** that ignores block entirely and is not scaled
by any modifier. Without a second channel one defensive stat would solve
everything.

HP persists across combats and is the run's real currency. Block does not.

### Powers, debuffs, and clog cards

Three separate mechanisms get loosely called "status".

**Powers** live on a creature and are wiped at end of combat. Four stacking
semantics. Intensity, where the stack count is the magnitude and never decays,
covers Strength and Poison. Duration, where the count is turns remaining and
decays at end of round, covers Vulnerable and Weak. Counter, consumed on a
trigger, covers Artifact. Non-stacking booleans cover Barricade.

Debuffs applied by an enemy get a grace turn, so a debuff does not tick down
during the round it landed and you experience its full advertised duration.

**Status cards** are cards added to your deck that are deleted at end of
combat. Burn, Dazed, Wound, Slimed, Void. Only Slimed is playable.

**Curse cards** are permanent until removed outside combat. They are the clog
mechanic with teeth.

### Intents

Seventeen intent values. What the player sees is deliberately uneven.

Attack shows the **exact post-modifier damage number** and the hit count.
Block shows that block is coming but never the amount. Debuff shows that a
debuff is coming but not which one or how much. Unknown covers splitting,
exploding, summoning, reviving, and doing nothing.

The damage number is live. It is recomputed through the whole pipeline whenever
state changes, including your own Vulnerable, so applying Weak to an enemy makes
its displayed number drop immediately.

Almost nothing can change an intent after it is shown. Runic Dome is the
explicit exception, and it charges you a permanent energy boost in exchange for
hiding all intents. That price tag is the clearest evidence that the game treats
information as a resource.

### Randomness

Thirteen named RNG streams, all seeded from the run seed. Five of them are
**re-seeded to `seed + floor` on entering each room**, covering enemy HP rolls,
enemy AI, shuffles, and random card effects.

That per-floor reseed is the mechanism that makes precise reasoning possible. No
amount of extra shuffling on floor 7 perturbs floor 8. Shuffle order on a floor
is a pure function of the seed, the floor, and the deck size.

### Card anatomy

Cost, then five types, then six rarities.

Types are Attack, Skill, Power, Status, Curse. The Attack and Skill split is
load-bearing because a large number of effects key off it.

Rarities are Basic, Common, Uncommon, Rare, Special, Curse. Reward rates are 3
percent rare after a normal fight and 10 percent after an elite, modified by a
run-wide offset that starts at minus 5 percent, climbs 1 point per common
rolled, and resets to minus 5 whenever a rare appears. This is why the first
reward of an act is effectively never rare.

A card upgrades exactly once. Searing Blow is the only exception.

Keywords worth naming. Exhaust removes from the combat. Ethereal self-exhausts
if held at end of turn. Innate starts on top of the draw pile and **consumes** a
draw slot rather than adding one. Retain survives the end-of-turn discard.
Unplayable cannot be played from hand at all.

## Part 2. The run layer

### Shape

Three acts plus an optional fourth. Each act is a fresh procedurally generated
map, 15 floors, capped by a boss whose identity is visible from the moment you
enter the act.

Defeating an act boss heals all missing HP, reduced to 75 percent from
Ascension 5 upward. Acts 1 and 2 award a choice of one of three boss relics.
Act 3 awards no relic, so the boss relic economy closes after Act 2.

Act 4 is not procedural. It is a fixed four-node corridor and the rest site
comes first, so the elite and the final boss are fought back to back.

### Map generation

The part that matters most for us, and the part popular guides describe wrongly.

The map is a grid 15 rows tall and 7 columns wide. Generation walks **6 paths**
from the bottom row to the top. Each step moves up one row to column `x-1`, `x`,
or `x+1`, clamped at the edges. The second path is forced to start at a
different column than the first, guaranteeing at least two entrances.

Two structural constraints. Edges may not cross, enforced by clamping each new
edge against its neighbours' outgoing edges. And two branches that just split
may not rejoin one floor later, enforced by re-rolling sideways when a
prospective parent shares an ancestor fewer than 3 rows back.

> **What we implement is narrower than that.** Our generator enforces only the
> immediate case, where two children of the same node share a child. The
> stronger "shares an ancestor within 3 rows" rule is not enforced and not
> checked, so it still occurs. Enforcing the immediate rule alone gets route
> distinctness where it is visible; the wider rule was not worth a backtracking
> walker. Recorded here so nobody reads this section as a description of the
> code.

Forced floors are applied before anything random. Floor 1 is all monster and any
node on it is a legal entry. A mid-act floor is all treasure. The pre-boss floor
is all rest site. Every pre-boss node connects to the single boss node.

**Room types are not rolled per node.** The generator computes fixed quotas over
the count of assignable nodes, fills a bag, shuffles it, and deals it out. Rest
12 percent, Elite 8 percent scaled by 1.6 above Ascension 0, Shop 5 percent,
Unknown 22 percent, and monster takes the remainder at roughly 53 percent. The
consequence is that the *count* of each type per act is near-deterministic and
only the placement varies.

Dealing proceeds row by row in the original. For each node the generator takes
the first type in the bag that satisfies three rules. Rest and Elite may not
appear on floors 1 through 5. Rest, Treasure, Shop and Elite may not take a type
any parent already has. And no node may take a type already held by a sibling,
meaning any node reachable from any of its parents.

> **We deviate here, deliberately.** Dealing strictly row by row starves the top
> of the map, because the early floors are the only ones that can legally take
> Shop and Unknown and they consume every one before the upper floors are
> reached. Measured on our grid it produced a pure monster corridor on floors 11
> to 14 in 600 of 600 maps. We assign each bag item to a random legal slot
> across the whole climb instead, which preserves the totals and spreads the
> variety. The three placement rules are unchanged.

**When no type in the bag is legal the node is left empty and becomes a monster
room.** It is not re-rolled until something fits. The failure mode of an
over-constrained node is the boring default.

The sibling rule is the sharpest routing lever in the game. Any node with two
outgoing edges guarantees two different destination types.

### Traversal

You choose freely among floor 1 nodes. After that you may only move to a node
connected by an outgoing edge from where you stand. One row up, no sideways
moves, no backtracking, no skipping. All paths converge on the boss, so both the
boss and the pre-boss rest site are unavoidable. Wing Boots is the single
documented exception.

Map generation decides that a node is Unknown, not what it becomes. That is
resolved on entry.

### Unknown node resolution

Checked in order on entry, with a per-act ramp. Monster starts at 10 percent and
climbs 10 points each time it does not fire. Shop starts at 3 and climbs 3.
Treasure starts at 2 and climbs 2. Event absorbs all remaining probability. Any
outcome that fires resets its own counter. Every counter resets between acts.

So the first Unknown of an act is an event roughly 85 percent of the time, and
pressure builds toward monsters as you visit more of them.

### Rewards, relics, economy

Three cards offered after a fight, never duplicates, each rarity rolled
independently, and **Skip is always present**.

Relics are permanent run-long modifiers, drawn without replacement so each
appears at most once per run. Boss relics are uniformly "large upside, real
cost". Snecko Eye draws two extra cards and randomises every card cost. Coffee
Dripper grants energy and removes your ability to rest.

Potions occupy 3 slots, 2 above Ascension 11. They are free to use, cost no
energy, and exist so a player can survive one fight they were not built for.

Gold starts at 99. The shop sells cards, relics, potions, and **one card
removal**. Removal starts at 75 gold and rises 25 **every time it is used
anywhere in the run**. Removing four cards costs 450 gold, which is several
relics of purchasing power.

That escalation is the economic core of the deck-size problem. Cards arrive free
and constantly. Removal is metered and compounding.

### Rest sites

One action per site, then you move on. Rest heals 30 percent of max HP. Smith
upgrades one card. Relics add Lift, Toke, Dig and Recall.

The tension is that both options spend the same non-renewable floor. Rest buys
survival now. Smith buys permanent power later. The pre-boss rest site is
guaranteed, so players plan the back half of an act around whether that site
needs to be a heal.

### Events

A short vignette and two to four buttons, each a trade. The recurring shapes are
paying HP for permanent power, paying gold for deck quality, taking a curse for
a strong reward, escalating risk with a stop button, and no-opt-out gambles.

Event pools are metered. Act events are once per run. Shrines are once per act.
Many events have entry gates on gold, HP or relic count, so being poor or
healthy literally changes which events exist for you.

### Ascension

Twenty cumulative levels, unlocked one at a time by winning at the level below.
Level 1 makes elites 1.6 times more common, which tells you what the designers
consider the real difficulty knob. Levels 2 through 4 raise damage, 7 through 9
raise HP and block, 17 through 19 change movesets, and 20 adds a second Act 3
boss. In between sit resource squeezes at 5, 6, 10, 11, 12, 13, 14, 15 and 16.

Two properties make it work. It is gated behind demonstrated mastery, so a
player at A12 has by construction won at A11. And **it grants nothing**. The
only content locked behind Ascension is Ascender's Bane, a curse. The moment
hard mode pays out, the choice stops being voluntary.

The order also front-loads modifiers that change decisions and back-loads ones
that change execution.

## Part 3. Load-bearing versus cosmetic

Ranked. Load-bearing means remove it and the thing stops feeling like Slay the
Spire no matter how faithful everything else is.

1. **Randomness is resolved and disclosed before the player commits.** Not
   determinism. Ordering. The roll happens, you are shown the result, then you
   spend. This is the property with no substitute.
2. **A per-turn budget small enough that the turn is a closed, solvable
   problem.** 3 energy and 5 cards. The specific numbers are cosmetic. The
   property that a competent player can exhaust the search tree and know they
   found the line is not.
3. **A persistent depleting resource that carries across encounters.** HP is
   what makes a won fight still cost something and the map a budget.
4. **Rewards are permanent additions to something you own, with a real ongoing
   cost, plus first-class refusal.** The deck is a liability as well as an asset.
5. **A fully visible risk gradient on a map you route yourself.**
6. **Permadeath with a bounded, small cost.** A loss averages 23 minutes.
7. **Run-scoped power, with meta-progression that adds variety rather than
   strength.**
8. **Player-chosen difficulty that adds constraints and grants no rewards.**
9. **The final exam is known from the start.** The boss portrait is visible from
   floor one, so a boss loss is a construction error you can name.
10. **One screen, everything relevant, no submenus.**
11. **Feedback that never gates input.** Juice must be free.

Cosmetic, and replaceable wholesale. The dungeon fantasy, the art, every
specific number, every card and relic name, the enemy roster, the score formula.
Also cosmetic, and this surprises people: **the card metaphor itself**. Cards are
one implementation of "a bounded, randomised, player-curated set of moves with
individual costs". And **combat**. Nothing in the loop requires violence. It
requires an opposing force whose next action is disclosed and whose defeat has a
checkable condition.

### Corrections worth recording

Secondary sources are wrong about several things that change an implementation.

| Claim in circulation | What the shipped game does |
| --- | --- |
| Block is removed at the end of each turn | Removed at the **start** of its owner's turn |
| Drawn overflow goes to the discard pile | Drawn overflow is **not drawn**, and stays on the draw pile. Generated overflow goes to discard |
| Duration debuffs tick at the start of a turn | They tick at **end of round**, with a grace turn when an enemy applied them |
| Map room types are rolled per node | They are dealt from a **shuffled fixed-quota bag**, and an over-constrained node falls back to monster |
| Ascension 15 reduces rest healing | A15 worsens events. **A5** is the post-boss heal reduction. Campfire rest is 30 percent at every level |
| The appeal is determinism | The appeal is **disclosure ordering**. The enemy move is rolled by an RNG. It is shown to you before you spend |

One playtest result deserves its own line, because it is the strongest available
guidance for building an intent surface. Mega Crit shipped exact numbers only
after trying a middle option where attacks were bracketed by icon into damage
ranges. The bracketed version tested **worse than both extremes**, because
players could not tell whether a range meant the damage was randomised or merely
unknown to them, and they stalled. Partial information is worse than either full
information or none.

## Part 4. The port ledger

Verdicts. **Emulate** means port the mechanism faithfully. **Adapt** means keep
the property, change the mechanism, because the domain differs. **Refuse** means
we are not building it, with the reason recorded so nobody re-litigates it by
accident. **Defer** means wanted, blocked on a prerequisite.

| StS system | Verdict | Reasoning |
| --- | --- | --- |
| Disclosure before commit | **Emulate**, top priority | The one property with no substitute. Our intent must be the output of a deterministic sensor such as a test run, a type check, a lint pass or a CI job, showing an exact number before the player spends anything |
| Intents on judgment-call work | **Refuse** | A room whose acceptance is a human judgement has no honest intent. Presenting a guess as an intent reproduces the bracketed-damage failure, which tested worse than showing nothing |
| Per-turn energy budget | **Emulate** | Energy is the session's attention budget. Set not added, so it expires unused |
| Small closed turn | **Emulate** | The room must be solvable in one sitting with the budget on screen |
| Draw, discard, exhaust piles | **Defer** | Wanted, and it needs a real card layer with costs and legality first. Today's "hand" is the room-legal subset of a permanent collection |
| Player HP, block, damage math | **Refuse** | No honest analogue in project work. A health bar the player knows is fictional makes every routing decision fictional too. Nothing damages the player, and no meter carries between rooms |
| A per-room progress meter | **Adapt** | Distinct from HP and worth stating, because the two get confused. A room shows how much of its acceptance is met, it fills upward as you play cards, and it resets with the room. The demo calls the field `clearAt` rather than `maxHp` so the code does not imply hit points either |
| A persistent depleting resource | **Open** | Pillar 3 is genuinely unfilled and we should not fake it. The only honest candidates are measurable. Calendar time to a deadline, CI minutes, reviewer capacity, error budget. Until one is bound, risk and reward stays advisory. This is the most important open question in the design |
| Buffs and debuffs | **Refuse for now** | They only mean something on top of damage math we are refusing |
| Status cards | **Refuse** | A per-combat clog card needs piles to clog |
| Curse cards | **Adapt** | A curse is a real standing liability. A known-bad rule you agreed to carry. Permanent until explicitly removed, which is already how relics work here |
| Card rarity | **Adapt** | Rarity should gate offer probability, including the rare-drought offset. Today it only colours a notch |
| Card upgrade | **Adapt** | One upgrade per card, bought at a campfire, which is exactly Smith |
| Relics as run-long modifiers | **Adapt** | Ours are standing policy lines rather than combat hooks. Keep that. Add the `ascension_min` gate that the schema already declares and nothing reads |
| Potions, the vocabulary | **Adapt** | A one-shot diagnostic really is a potion, and naming it one is useful now. The Deck facet lists them for that reason |
| Potions, the mechanic | **Defer** | Slots, drop rates, and spending one mid-room need an economy that does not exist yet. Nothing is consumable in the demo. Splitting this row from the one above because shipping the panel while the ledger said "refuse potions" read as a contradiction, and fairly so |
| Gold economy | **Defer** | Needed before removal can cost anything |
| Escalating removal cost | **Emulate** | This is the mechanism that gives the deck cap teeth. Free removal makes bloat costless and the whole lean-deck tension collapses |
| Map generation, branching, commit to edge | **Emulate fully** | Load-bearing pillar 5, fully specified, pure arithmetic, and completely checkable. This is what we build first |
| Node type quotas | **Emulate fully** | Including the quota bag rather than per-node rolls, the three placement rules, and the monster fallback |
| Unknown node ramp | **Emulate fully** | Including per-outcome counters that reset on fire and between acts |
| Per-floor RNG isolation | **Emulate fully** | `seed + floor` reseeding is what makes a run reproducible and reasoning precise. Cheap to implement, and everything downstream inherits the property |
| Acts and a known boss | **Adapt** | Act templates already exist. The boss must be visible from the first floor of the act, because that is what makes the act a plan |
| Rest versus Smith | **Adapt** | With no HP there is nothing to heal, so the two options become Smith, which upgrades a card, and Prune, which removes one. Both still spend the same non-renewable floor, which preserves the actual tension |
| Events with tradeoffs | **Emulate** | The schema already describes this. It needs an engine and the effect verbs implemented |
| Ascension ladder | **Already shipped, adapt later** | Ours has 5 rungs against 20 and gates tooling strictness rather than enemy stats. It correctly grants nothing. Worth moving toward per-level rungs |
| Deck soft cap | **Emulate, with two fixes** | The cap exists at 12. It needs the live count on screen rather than in a doc, and it needs a payout for refusal |
| A payout for refusal | **Emulate** | Singing Bowl is the cleverest object in the original for our purposes. It turns declining a reward into an incrementing positive number. Discipline that only avoids future harm never feels like a win, so refusal needs a counter that goes up |
| Permadeath | **Adapt to scoped permadeath** | We cannot and must not destroy a repository. The honest substitute discards the run's deck and relics while never touching code. Without something real being lost, skipping stops being skilled play |
| Meta-progression restraint | **Emulate** | Unlocks may add variety. They may never add strength |

### The two things most likely to kill the feel

Recording these because they are failure modes, not tasks.

**A dishonest intent.** If a room's next action is a model's guess dressed as a
telegraph, the player learns to distrust the surface and the entire disclosure
pillar collapses. Any room without a checkable acceptance is not a fight and
should not be presented as one.

**An invisible deck cost.** In the original you can open the draw pile and count
your 31 cards, so bloat is inspectable. A skill added to a repo genuinely costs
attention, but that cost is invisible, so a player who skips gets no
confirmation they were right. The cap must be a live number and refusal must pay.

## Part 5. What gets built first

The map layer, because it is the foundational data structure. Rooms, intents,
combat and rewards all hang off a node, so the node and edge shape decides the
shape of everything after it. Getting a data structure wrong late is a rewrite.
Getting it right early is cheap.

It is also the honest place to start. It needs no HP, no economy, and no model
judgement. It is arithmetic over a seed, which means it is fully verifiable, and
the invariants above read directly as tests.

Concretely, in `scripts/mapgen.py`, stdlib only.

1. A 15 by 7 grid with 6 walked paths, at least two entrances, no crossing
   edges, and no rejoin within 3 rows.
2. Forced floors. Monster on floor 1, treasure at mid-act, rest before the boss,
   every pre-boss node wired to a single boss node.
3. Quota-bag room assignment with the row, parent and sibling rules, and the
   monster fallback when nothing legal remains.
4. Unknown resolution with per-outcome ramp counters, frozen per node so
   re-entering never rerolls.
5. Commit-to-edge traversal as pure functions over the generated map.
6. Per-floor RNG isolation via `seed + floor`.

Everything in that list is checkable by script over hundreds of seeds, which is
what `mapgen.py verify` does.

## Part 6. Slay the Spire 2

Scope note: Early Access, patched continuously. Everything below is a property
of the shipped sequel as of this writing, not of a datamine or a roadmap. The
sequel matters here for one reason — it is Mega Crit revisiting their own
interface with six years of telemetry, so where StS2 changed something, that is
a designer with better evidence than ours disagreeing with StS1.

The verdicts use the same vocabulary as Part 4: **Emulate**, **Adapt**,
**Refuse**, **Defer**.

### What actually changed in the interface

**The intent vocabulary got wider and flatter.** StS1 shipped seventeen intent
values. StS2 keeps the grammar and adds legibility: the attack glyph scales
across five tiers of menace rather than one weapon, multi-hit reads as `3x4`,
and — the change that matters most — **multiple intents render side by side
rather than stacked**. A monster that will attack *and* buff shows you both, in
one row, at once.

The full set: attack (exact post-modifier number), defend, buff, debuff,
status-card (grey card, purple border, count), affliction (grey card, orange
border), heal, summon, death blow, cowardly, stunned, sleeping (with a counter),
unknown.

**The map got a draw tool.** You can annotate the map before committing to an
edge: mark elites, rest sites, shops, or trace two candidate routes and compare
their elite counts. Community mods extended it with sticker sets. This is the
first officially-sanctioned admission that *route planning is a distinct activity
from route walking*, and that the map should support it.

**Runs end with Badges.** Small end-of-run cards noting what was unique about
that run. Mega Crit's framing: "little reminders to let you know what was unique
about each run."

**Rest sites grew to nine options** — Rest, Smith, Dig, Lift, Cook, Clone, Hatch,
Mend, Kindle — most gated behind a relic or a card. Unavailable options are
greyed rather than hidden.

**Monsters got per-monster speech-bubble colours**, character and enemy
animations were reworked, and the map paths were redrawn for legibility. Acts
went from three-plus-one to three. Multiplayer for up to four players landed.

### The ledger, part two

| StS2 system | Verdict | Reasoning |
| --- | --- | --- |
| Intent icon taxonomy, thirteen kinds | **Emulate, bound to sensors** | Shipped. `content/enemies.json` declares the vocabulary and every intent carries a `sensor` naming the deterministic check behind it. An intent with no sensor is dropped before it reaches the client, and the client says so out loud rather than hedging |
| Exact numbers on attacks, nothing on blocks | **Emulate** | Already Part 3's top finding. StS2 changing nothing here is the strongest confirmation available that the bracketed middle was the wrong answer |
| Multiple intents side by side, not stacked | **Emulate** | Free legibility win. A room can telegraph two facts without either hiding the other |
| Attack glyph scaled by tier | **Adapt** | Our tiers are sensor magnitudes, not damage. The glyph scales; the number under it is a count of failing checks, which is a real quantity |
| Map annotation / draw tool | **Emulate** | The single most portable thing in the sequel. Routing a codebase is exactly the decision the map exists to support, and marking a node before committing to an edge is the same act. Shipped: `spire_annotate_node`, persisted in `game.annotations` |
| Run badges | **Emulate** | Aimed at the gap Part 4 named as most in need of a payout. `content/objects.json` defines them as pure reads of the save — Ascetic counts skipped rewards, Lean Deck counts cards held, Cartographer counts annotations. Nothing is granted; every badge is earned or absent |
| Nine rest-site options | **Adapt to three** | With no HP there is nothing to Rest, Mend or Cook. Smith and Prune remain, and Dig is kept as the relic-gated third because "an option you can see and cannot take yet" is the part of the nine that carries design weight. Options with no cost are not choices |
| Greyed-out rather than hidden options | **Emulate** | Shipped at the campfire. Seeing the door you cannot open is information |
| Per-monster speech-bubble colour | **Adapt** | Ours is the room-type palette, already scaled and already paired with a glyph and a label. A room says what kind of work it is by its colour *and* its chip |
| Clearer map paths | **Emulate** | Acted on beyond the sequel's own change: our client now climbs bottom-to-top instead of running left-to-right. A spire is climbed. The horizontal graph was a wireframe convention that read as a flowchart |
| Five characters, new resources (Stars, Forge, Doom) | **Refuse** | Character-specific resource systems need a combat layer we deliberately do not have. Our classes are repo archetypes, not movesets |
| Orbs, Channel/Evoke/Focus | **Refuse** | Same reason. The Defect's name is borrowed; its mechanics are not |
| 144 FPS, reworked animations | **Not applicable** | We are an HTML surface in a sandboxed iframe with a 2-3 beat motion budget. Juice must stay free, and free means cheap |
| Four-player multiplayer | **Refuse** | `non-goals.md` already refuses it, and this does not amend that. One active room is the product |
| Three acts instead of three-plus-one | **Refuse** | Our acts are unbounded on purpose (`sts-emulation-decisions.tsv`, row 26): a codebase does not finish, so a terminus would be the dishonest part |

### What the sequel does not fix

The pillar Part 4 marked **Open** — "a persistent depleting resource", the thing
HP does in both games and nothing does here — is still open. StS2 keeps HP, so it
offers no help. That remains the most important unfilled question in this design,
and no amount of intent-icon fidelity substitutes for it.
