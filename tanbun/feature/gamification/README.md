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
- The resource level uses `50 * (level - 1)^2` as its cumulative XP threshold.
  Power does not change the XP threshold.
- Power is recalculated from current outgoing `TO` (logic) and
  `REF|RESOLVED|QUOTERM` (reference) relationships. Duplicate source/destination
  pairs in each category count once. Self-links, retired destinations,
  unresolved placeholders, detail/hierarchy edges and text length do not count.
- Imports do not award resource XP. A changed file can change Power.

## Migration boundary

This first stage records resource XP from deployment onward. Historical activity
is not automatically replayed. The existing user-level calculation/API remains
unchanged; it is not yet a sum of this ledger. Replacing that calculation and
attributing older activity require a separate migration, including a policy for
activity whose original resource can no longer be resolved.

The normal schema installation command (`poetry run task schema-install`, also
part of `task start`) installs the event indexes and unique key constraint.
