# Avatar lifecycle

Cloudinary credentials are centralized in the **backend**, not the frontend
server. Configure Render before deploying the updated frontend:

- `CLOUDINARY_CLOUD_NAME`
- `CLOUDINARY_API_KEY`
- `CLOUDINARY_API_SECRET` (secret; never use a VITE-prefixed variable)
- `CLOUDINARY_UPLOAD_PRESET`
- `CLOUDINARY_AVATAR_FOLDER` (default `avatar`)

Frontend `VITE_CLOUD_NAME`, `VITE_CLOUDINARY_API_KEY`, `VITE_UPLOAD_PRESET` and
`VITE_CLOUD_FOLDER` must match. The old frontend signing/deletion routes return
410 and no longer accept unauthenticated requests.

Use a **signed** Cloudinary upload preset dedicated to avatars. Configure its
server-side limits: allowed formats jpg/jpeg/png/webp, maximum file size 5 MB,
overwrite disabled. Optional incoming `c_limit,w_1024,h_1024,q_auto:good`
reduces stored originals, but test custom crop coordinates first: incoming
resizing changes coordinate space. Do not enable incoming `f_auto`; format
selection is applied at delivery. Client uploads resize to 1024px and delivery
uses `q_auto,f_auto` with fixed size limits, preserving crop and version paths.
Client limits are UX safeguards, not server-side enforcement.

Every upload has a unique `avatar/<user UUID>/<upload UUID>` identity. Signing
requires login, restricts parameters and ownership, and schedules abandoned
uploads for deletion after 72 hours. Successful uploads remain referenced by
`User.avatar_url`. Replacing/removing an avatar or deleting an account schedules
cleanup in the same Neo4j transaction. Both admin deletion and FastAPI Users
deletion paths are covered. Failed DB transactions roll back the cleanup job.

The separate worker polls every 30 seconds, uses a cloud-wide queue lock plus
per-job leases (one deletion at a time even with multiple web processes),
rechecks live avatar references, invalidates the CDN and retries failures with
capped exponential backoff. Cloudinary outages do not block account deletion;
jobs survive restart. Deleted users have no edge to the cleanup job, so removing
the user does not remove pending work. Credentials must stay pointed at the same
cloud; jobs for another cloud are not processed.
Avatar replacements queue the previous asset without a grace period and attempt
deletion immediately after the profile transaction commits, before returning the
response. The worker retries failures (or a busy cleanup lease) on later passes.
Other reference removals wait at least one hour before remote deletion, allowing
already-issued upload signatures to expire. New abandoned uploads wait 72 hours.
Run `task schema-install` to install the cleanup job/queue uniqueness constraints
before starting multiple web processes (the normal deployment already does this).

Admin's Images tab automatically loads all provider pages, in a table
with hover/tap previews and bulk deletion of eligible unreferenced images.
Confirmed manual deletions use `/admin/images/delete` (100 IDs per batch)
and run immediately, rechecking references before each deletion. Non-UUID names
and recent uploads in the avatar folder are allowed; live references and other
folders remain protected. Reference checks include non-UUID legacy avatars.
Automatic cleanup retains UUID restrictions and grace periods. The old cleanup
reservation endpoint remains for compatibility. This is not a destructive
full-library sweep. Existing large originals are not re-encoded automatically.

Cloudinary references:
[signatures](https://cloudinary.com/documentation/authentication_signatures),
[widget](https://cloudinary.com/documentation/upload_widget_reference),
[optimization](https://cloudinary.com/documentation/image_optimization).
