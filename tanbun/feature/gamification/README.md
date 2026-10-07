# Resource growth foundation

`GET /user/me/resource-growth` returns the authenticated user's resource XP,
level, Power, recent XP log and the currently applied rules. It includes owned
resources and other users' resources that the requester has reviewed.

## Separate facts from current structure

- `ResourceXpEvent` snapshots the resource ID/name, subject, day, source and XP.
  It deliberately has no relationship to a deletable Sentence, Quiz or Resource.
  Account deletion removes its user's events.
- A successful exposure earns 1 XP. A quiz answer earns 5 XP, with 2 more for a
  correct answer. Each subject/source earns XP once per Japan-calendar day.
  A wrong answer followed by a correct answer earns only the missing bonus.
- Answer/exposure writes and XP writes share a transaction. A User write lock
  serializes awards, and the event key also has a uniqueness constraint.
- User and resource levels share a per-level cost of `current level * coefficient`.
  The default coefficient is 10; cumulative XP to reach level L is
  `coefficient * L * (L - 1) / 2`. Power does not change this threshold.
  Admins can GET/PUT `/admin/settings/gamification` to change
  `level_xp_coefficient` (an integer from 1 to 10000). The setting is persistent
  and applies to all users and resources on their next fetch. Existing earned
  XP and Power remain unchanged; only levels and progress are recalculated.
- Power is recalculated from current outgoing `TO` (logic) and
  `REF|RESOLVED|QUOTERM` (reference) relationships. Duplicate source/destination
  pairs in each category count once. Self-links, retired destinations,
  unresolved placeholders, detail/hierarchy edges and text length do not count.
- Imports do not award resource XP. A changed file can change Power.

## Migration boundary

This first stage records resource XP from deployment onward. Historical activity
is not automatically replayed. User XP sums the same `ResourceXpEvent` ledger
as resource XP (1 per exposure, 5 per answer, 2 per correct answer, daily deduplication).
`today_xp` sums events earned on today's Japan-calendar date. Other users' activity
never contributes, but the user's own reviews of borrowed resources do.
Knowledge quantity, quiz creation and Power award no user XP. The legacy
`xp.knowledge` and `xp.quiz_creation` fields remain zero for API compatibility;
they are not included in `xp_details`. Existing levels are recalculated without
these old awards. No activity records are deleted.
User XP retains events for deleted resources. Historical answers without ledger
events are not added; attributing older answers requires a separate migration policy.

The normal schema installation command (`poetry run task schema-install`, also
part of `task start`) installs the event indexes and unique key constraint.

## Profile bookshelf

`GET /user/{user_id}/resource-growth` exposes owned resources only, with cumulative
XP by activity, Power and the last reviewed day. It omits activity subjects and
the recent XP log; those remain available only via the authenticated `me` route.
Both endpoints share the same calculation. Profiles show review-only user Lv
and explain its breakdown using the shared resource ledger.
