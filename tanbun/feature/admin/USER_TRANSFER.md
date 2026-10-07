# User data transfer

The admin user list supports moving learning data into an empty account while
retaining both User nodes. This is a move, not a copy or an account merge.

1. Back up Neo4j. Stop imports, quiz preparation, reviews and PageRank jobs for
   both accounts. The tool refuses queued/running PageRank jobs.
2. Choose **データ移行** on the source user and select the destination.
3. Inspect the counts and blockers. Type both email addresses to confirm.
4. Execute. Preview contents are checked again inside a dedicated transaction;
   changed contents require a new preview. Failures roll back the entire move.
5. Check resources, answers, XP and StudyPlans after signing in as the destination.

Transferred data includes folder/resource ownership, created/learned quizzes,
answers, quiz reports, StudyPlans, notifications, review settings, daily sets and
achievement history. Exposure/XP `user_id` values, their idempotency keys, report
keys and owner-scoped Resource keys are rewritten. Resource and Sentence IDs do
not change. An audit node records source, target, acting admin, date and counts.

Email, password, OAuth accounts, username, profile, admin permissions and browser
push subscriptions stay with their original User. Moving an admin's data does
not grant admin permissions to the destination. Browser-local history and
preferences are not moved. Empty destination recommendation/day/achievement
caches may be discarded; destination notifications are preserved.

Existing destination learning data, unsupported User relationships or `user_id`
records, malformed idempotency keys and duplicate source titles prevent transfer.
This first implementation does not merge overlapping accounts. User activity
must remain stopped during the operation; new activity after completion belongs
to the account that performs it. Retaining the source User is not a data backup.

API:

- `GET /admin/users/{source_id}/transfer-preview?target_id={target_id}`
- `POST /admin/users/{source_id}/transfer` with `target_id`, `preview_token`,
  `source_confirmation` and `target_confirmation`.

Both endpoints require an active superuser. No production migration is performed
by installing or deploying this feature.
