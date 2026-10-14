# Gold and inventory

MP adds another allocation stat: `base_mp + points.mp * mp_per_point`.
An encounter refills MP once, not at every next-turn acknowledgement. Reallocation
clamps current MP without refilling it. Legacy states/allocations default to zero
current MP/MP points, and the next encounter uses the configured maximum.

Boss challenges at achievements 5/10/15/20 cost one move and never gate exploration.
They use the latest five bands of the pinned cumulative quiz population, the live
enemy calculator with configurable HP/attack multipliers, and the ordinary combat
UI/answer history. Only a living player's actual victory records `bossClears`;
regular three-kill laps cannot unlock specialties. Snapshot writes retain the
server-owned unlock list, and repeat victories do not duplicate it.

Every boss-cleared resource joins the specialty pool. Between processed turns,
one summon spends the configured MP cost, draws a resource uniformly, then draws
a valid prepared quiz from its highest cleared band's population. The skill ID,
quiz ID and deadline are saved so reopening cannot redraw or refund MP. Its first
provisional effect is all-enemy damage on correct answers, self-damage on incorrect
or unanswered attempts; attack derives from `ceil(log(1+term_count)*coefficient)`.
The normal damage calculator, defeat rules, gold settlement and real answer/XP
storage are reused. An unanswered skill never creates an Answer or answer XP.
A separate receipt makes retries idempotent. Lost-browser skills resolve once
as unanswered retreat; they cannot skip the penalty via the saved result-screen
shortcut. Enemy remaining HP stays browser-owned, as in ordinary combat.

Live battle settlements award 10 gold per enemy times the one-based region.
The existing turn receipt and transaction include gold, preventing retry rewards.
`GameSave.economy` contains currency, owned items, the equipped weapon and the last
daily review reward date; general state writes preserve the previous server value.
`GET /game/shop` exposes the shared catalog. Dedicated revision-checked purchase,
equip and use endpoints prevent stale/double consumption and refuse active battle
or pending result feedback. Equipment bonuses derive from owned catalog entries,
and live/practice combat and allocation updates use the same equipped stats.

The saved default daily knowledge recommendation is the review quota. All its
IDs must have an exposure for the current Japan-calendar date; empty sets do not
qualify. Completion awards one non-purchasable three-step potion per day by CAS.
Normal exposure requests award immediately. Game movement defers awarding until
its state-save response, so earning a reward cannot invalidate a pending move's
revision. A subsequent game state read also reconciles completed quotas.
Move potions replenish the shared active/parked move balance up to five, leave
HP and clock-slot permission untouched, and resume a resting run. The initial
prices and quantities are provisional catalog values in `inventory.py`.

# Adventure rights

Game async tests share the session loop managed by pytest-asyncio. Use
`@mark_async_test()` and `await`, not `asyncio.run()` inside synchronous tests:
the latter unsets the current loop and breaks later tests in the full CI run.
Snapshot-only API fixtures isolate quiz preparation; preparation behavior is
tested separately with resource-backed integration tests.

One right per server-clock half-hour slot (:00/:30), no accumulation. GET is
read-only; POST consumes the authenticated user's current slot under a User-node
write lock. Admin reset clears only the target user's consumed-slot marker.

`adventure_consumed_slot` and `adventure_access_lock` are internal Cypher-managed
User properties, intentionally absent from public/editable account schemas.
No new schema/index install is required; no orphan nodes are created. Deleting a
User also removes these fields. They must not be copied as learning history.

# Dungeon quiz populations

New quiz targets and population additions prioritize fresh cached PageRank
(stable ID order when the cache is absent/stale). Existing populations remain
fixed until the administrator explicitly rebuilds populations. Each displayed
region level L owns L*5 quizzes. Each enemy owns ceil(L*quiz coefficient)
quizzes (default coefficient 1); different enemy sets may overlap, but identical
sets are not generated. Unassigned population quizzes are allowed.

Enemy types are base types + (L-1)*growth, capped at 20. Enemy HP/attack use
Power, region growth and stable per-type variation; sentence relation counts no
longer affect stats. Encounter bounds both multiply by 1+(L-1)*growth rate,
round up and cap at 20. Draw an integer uniformly from those bounds, then draw
each enemy type independently with replacement. Duplicate instances keep
independent battle IDs/HP/answers and the same type appearance.

Admin settings include adjacent read-only simulations with local comparison
history. Stat changes apply live; encounter settings apply at the next battle.
Roster changes require an explicit all-region rebuild for a chosen dungeon.
`?reroll=false` rebuilds only enemy sets; the default also replaces populations
with PageRank-ordered cumulative prefixes. Progress, HP and review XP are kept.

The stable quiz population is stored in Neo4j, scoped to a user and resource:
`User-[:HAS_DUNGEON]->Dungeon-[:BASED_ON]->Resource`, then
`Dungeon-[:HAS_REGION]->DungeonRegion-[:POPULATION_QUIZ {position}]->Quiz`.
Each region contains a cumulative ordered prefix of five additional prepared
quizzes per achievement level. `GET /game/dungeons/{resource_id}/regions/{level}/quiz-pool`
returns or creates the population after verifying that the default StudyPlan has
enough usable quizzes. The client snapshot keeps a projection for battle display;
legacy snapshot pools are validated and migrated through the matching POST route.

Frontier preparation completes only when the usable cumulative region population
is ready. Partial generation keeps the frontier pending; retries generate only
the missing quizzes. Preparation status also caps legacy completion counters by
the actual usable quiz count, so polling can queue repairs for old incomplete
frontiers instead of leaving them permanently marked complete.

HP, battles, routes and dungeon clears are shared server snapshots with revision
checks. Combat rules remain a client-side prototype; these APIs
authorize starting/resuming an event, not validate every combat action. Learning
XP is still managed by its existing ledger. Reset does not heal, alter XP, or
restart an ongoing battle. A right consumed just before a clock boundary recovers
at that boundary. Pending events do not accumulate or auto-restart.

`visitedDungeons` is a server-maintained, deduplicated list of resource IDs,
most recent entry first (up to 1000). It survives retreat, defeat and clearing,
including saves from older clients that omit the field. Existing active runs
and cleared IDs seed history; unstored abandoned visits cannot be reconstructed.
It is part of the account snapshot and uses the same revision checks.

`unlockedDungeons` records a permanent resource-to-resource road at each
achievement milestone 3, 6, 9, ... (15 new places per road). Targets are randomly
selected from owned resources with knowledge, excluding visited/unlocked ones.
State reads backfill existing milestones under revision checks; state writes
retain server-selected roads even when an old client omits them. Dungeon travel
keeps the player's HP/remaining moves and each dungeon's saved map location.
An opened dungeon with no quizzes queues its initial five before exploration.

# Battle timing

Admin game balance includes interactive practice battles using the live
MultiBattle UI. A permanent practice panel accepts enemy ability/roster trial
snapshots or a real saved dungeon region. Snapshot enemy HP/attack stay exactly
as calculated; real dungeons retain saved enemy identities and quiz sets.
Player selection loads the displayed name/avatar and real allocated points.
Those points may be edited for the disposable trial only. Player initial values,
damage and encounter settings use the current unsaved draft.
Snapshot trials use synthetic correct/incorrect quizzes and need no dungeon.
Practice turn endpoints share readable
quiz correctness, damage ranges, encounter selection and turn transitions with
live combat, but never create answers, grant XP, consume adventure rights, or
persist outcomes. These endpoints require superuser privileges.

The browser holds the session, comparison history and parameter snapshots.
Replaying reuses the initial snapshot and seed, restarting only its timer/HP.
Input changes do not alter an active trial. Player damage uses the same live
frontend function with a seeded draw; incoming damage/questions use a seeded
server calculator. Feedback time is excluded from reported answering time.
Practice hides reporting and editable detail navigation. Only quizzes for the
selected region are carried in turn requests, not the whole knowledge map.

Admin game balance also provides a read-only one-on-one combat simulation. Damage
is `floor(attack * attack_multiplier - defense / defense_divisor)`, varied by
the configured random percentage and clamped to at least one (except a player
with zero attack). Enemies currently have no defense. The estimate returns the
nominal damage and its random range; it does not create answers, grant XP, change
HP, or persist simulated game state.

Admin > Battle configures basic seconds (default 30) and four quiz-type weights:
sent2term 1.0, term2sent 1.2, pair2rel 1.5, rel2pair 1.5. Seconds are rounded up.
`/admin/settings/battle` requires a superuser and stores config in AdminSettings.
Normal review quizzes are untimed. Changes apply to newly presented questions,
not a question already in progress.

The server issues `answerDeadline` and `answerSeconds` when entering battle or
presenting the next question. Reopening, changing devices or hiding the dialog
does not extend it. `battleFeedback` persists the result: reading feedback does
not consume the next question's time; Continue starts the new question.
Timeout damages the player once, without fabricating quiz answers or learning XP.
Answers submitted before the deadline are not penalized for network wait time.
