# Adventure rights

One right per server-clock half-hour slot (:00/:30), no accumulation. GET is
read-only; POST consumes the authenticated user's current slot under a User-node
write lock. Admin reset clears only the target user's consumed-slot marker.

`adventure_consumed_slot` and `adventure_access_lock` are internal Cypher-managed
User properties, intentionally absent from public/editable account schemas.
No new schema/index install is required; no orphan nodes are created. Deleting a
User also removes these fields. They must not be copied as learning history.

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

# Battle timing

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
