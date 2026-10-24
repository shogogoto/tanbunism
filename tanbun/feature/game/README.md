# Game systems

## Skill compositions

Skills own their name, MP cost, animation/SE and enabled state. Ordered
`template_ids` reference one or more shared effect templates. Templates define
targets, effect amounts, answer behavior and status duration/stacking, not MP or
presentation. The admin edits two sortable tables without category-specific
tables. Existing standalone effects are converted to templates in the editing
draft without changing their values.

One quiz resolves all components with the same correctness; MP or an item use is
consumed once. Counter effects retaliate at most once. Current combat resolves
attack, recovery and status components for either actor; specialty/end actions
use the same component expansion. Status stacks are keyed by template identity.
Stored catalogs and version-5 skill files retain references rather than copied
effect values. Versions 1–4, singular template references and in-progress flattened
skill snapshots remain readable. Equipment and movement recovery remain
standalone effects; multi-effect items are battle-only.

Effect-template variants reference one independent `base_template_id`. Only
`override_fields` (any effect setting, including kind, state details, target count
and answer behavior) override that base;
the API returns effective values while storage and JSON export keep only the
identity, parent reference and explicit differences. Resolution is independent
of display order. Missing parents, cycles, multiple inheritance levels and
invalid override values are rejected. Battle actions still freeze their resolved
effects, so later balance edits do not alter actions already in progress.
The admin lists only common settings. A family panel selects one variant at a
time for editing, supports adding/copying variants and generates named tiers
(beginner/intermediate/advanced) or 1/3/5/all-target variants. Tiers start with the
common values; no balance multipliers are invented. Overrides can be reset, or
variants detached while retaining current values. Existing templates are not
automatically grouped. Reordering a common row moves its whole family without
changing IDs or skill references. Changing effect kind removes inactive fields.

## Gambit capacity and timed effects

Gambit capacity is derived from the player's learning level: one initial slot,
then one per multiple of `gambit_levels_per_slot` (default 5, maximum 10 slots).
Saving too many rules is rejected; older saved lists are retained but only their
unlocked prefix is executed. Practice uses the selected player's level and draft
balance, without changing their actual state.

Skills support fixed or percentage amounts. Recovery percentages use maximum
HP/MP; attack percentages use the attacker's attack; stat changes use the original
stat rather than the already modified value. Buffs affect player attack/defense;
debuffs currently affect enemy attack (enemies have no defense stat).
Correct answers attach effects, starting on the next turn for `duration_turns`.
`stack_limit=1` refreshes that skill on that target; larger limits preserve each
stack's independent expiry. Effects live only in the battle marker, so victory,
defeat and retreat remove them. Enemy gambit lists are not yet enabled.

Poison is an `ailment` applied only to correctly answered enemy targets. Starting
next turn, each active stack contributes fixed damage or a percentage of maximum
enemy HP at the end of that enemy's result. Poison does not change attack/defense
and cannot reduce HP below one; quiz correctness remains required for a kill.
Poison ticks are included in the turn receipt and shared by actual/practice combat.

Registered material IDs also support project-local enemy/boss artwork, retaining
the legacy `icon` field for compatibility. Arbitrary asset URLs are not accepted.

## Gold and inventory

Completed five-place regions expose a treasure chest after their last knowledge
place. `/game/chests/open` validates the current endpoint, live phase and revision;
opening is free and also allowed at zero remaining moves. Gold and potion counts
come from the current admin game balance. The receipt in
`economy.opened_chests[resourceId][region]` and the reward are saved in one CAS,
so revisiting/retrying cannot grant twice. General snapshots preserve receipts
as part of server-owned economy. Existing maps need no migration; unclaimed
boxes use the latest settings, while opened boxes retain their original contents.
`@chest:<region>` is a facility location and valid retreat checkpoint, but never
a knowledge place or achievement increment.

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

The current client uses `/admin/game/catalog` to define skills, items and enemy
appearances. References and target/effect combinations are validated together;
legacy reward item IDs remain present. `/game/gambits` stores up to ten ordered
rules with a revision check outside battle. After ordinary turn settlement,
`battle/gambit/start` chooses the first executable rule and spends MP or one item
once. Ordinary quiz correctness does not gate this action. Definitions contain
effects and animation presets; `player_skills` grants direct access, while
`specialty_skills` defines dedicated quizzes, time, wrong-answer scaling and Power
growth. Items and enemies reference the same effects independently. Legacy skill
flags migrate to these consumer lists on read; explicit empty lists stay empty.
Specialty creation/equipment/fusion is managed by `/game/specialties`. Each source
resource supplies one dedicated quiz with its own timer. State application needs
all answers correct; numeric effects use the configured wrong-answer multiplier.
Practice uses disposable copies and does not write outcomes or rewards.

The following manual specialty flow remains only for older clients.
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

Twenty distinct quizzes answered via the review answer API in a JST day award
one additional three-step potion, regardless of correctness. Review Answers
are tagged `source=review`; game Answers and repeated attempts do not count.
`quiz_reward_day` independently prevents duplicate awards. The answer response
reports `step_recovery_awarded`; state reads retry any unclaimed completion.
Answers recorded before this tagging was introduced cannot be distinguished
from game Answers and do not count toward this new reward.

Quiz preparation status is stored on the default StudyPlan and returned by both
the study-plan preparation API and dungeon preparation API. Insufficient counts
alone never imply a running job: queued, running, ready, failed, and exhausted
are distinct. Failed/exhausted jobs stop automatic retries for the same target;
`POST /game/dungeons/{resource_id}/preparation/retry` explicitly retries them.
The worker has its own Neo4j scope and a 15-minute execution/queue timeout;
expired queued/running metadata is exposed as failed after process interruption.

Admin `POST /admin/users/{user_id}/game/dungeons/{resource_id}/regions/align`
repairs old map roads into one entrance-rooted chain per frozen region. It keeps
discovery order, current location, player stats, and quiz pools. Active and parked
maps are supported; battle/results block the operation. CAS prevents overwriting
concurrent exploration, and already aligned maps do not advance the revision.

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

Player maximum HP/MP, attack and defense are derived from the current admin
balance, allocated points and equipped weapon whenever state is read or written.
These calculated values are returned by the API but omitted from DB snapshots;
legacy cached values are ignored, including in parked dungeons. Current HP/MP
remain saved and are clamped when their maxima decrease, never healed when they
increase. An in-flight turn's answer duration/deadline are kept until the next
turn; balance changes do not restart its clock. Practice keeps its draft balance.
持ち時間 is controlled by game balance and quiz settings only; player allocation
no longer grants seconds, and legacy seconds points are ignored.

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
For scheduled battles the server reception deadline determines timeout; reading
the result never consumes time for the next question.

New clients request `scheduled: true` on normal/boss/practice starts. Each character
has a speed stat; player speed is derived from current base settings and allocated
points. Enemies can override the common speed in their catalog definition.
`speed_formula` selects `linear` (12 / speed) or `sqrt` (12 / sqrt(speed)). It is
sealed in the encounter, together with persistent virtual waits and deterministic
tie entropy. No real-time scheduling loop or least common multiple is involved.
The server supplies the current actor, one sealed quiz and ten forecast positions.
Forecasts use copies and are recalculated after defeat or speed changes.
Both outgoing/incoming damage and enemy HP are server-owned. Client defeated IDs
are ignored for this protocol, and action receipts plus revision CAS prevent
double settlement. Existing batched encounters continue under their old protocol.
Direct gambit skills replace the player's normal action; specialty quiz gambits
retain their separate end-action flow. Status durations currently count resolved
actions. Practice uses the identical scheduling/damage transition without writes.
Enemy speed uses `(enemy_speed + log(1 + Power) * power_speed)` multiplied by
achievement growth (`region_speed_growth_percent`) and the same stable individual
variation as HP/attack. Both new growth coefficients default to zero. Enemy catalog
overrides still apply to live encounters; generated practice trials retain their
previewed speed alongside HP/attack.

Enemy MP, special attack and special defense use the same Power/growth/variation
formula, with independent `enemy_*`, `power_*` and `region_*_growth_percent`
settings. Live enemies, bosses and generated practice trials carry these values;
boss attack scaling also applies to special attack. CTB encounters persist current
enemy MP in `schedule.enemy_mp`. Enemy actions spend MP even when prevented by a
correct answer, but not on retreat; insufficient MP falls back to the free normal
attack. Special attacks use special attack/defense instead of physical stats.
