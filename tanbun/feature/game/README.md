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
MultiBattle UI. The balance UI starts practice from the selected enemy trial
snapshot: enemy HP/attack remain exactly as calculated, while the player's
initial values, damage and encounter settings use the current unsaved draft.
Player selection supplies only the displayed name/avatar. Synthetic correct/
incorrect quizzes require no saved dungeon or generated study-plan quizzes.
The legacy saved-dungeon practice endpoint remains available. Practice turn endpoints share readable
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
