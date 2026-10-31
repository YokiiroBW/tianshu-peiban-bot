# Media package v1 — video subscription integration

State: locally accepted runtime contract. Real Platform HTTP, AssetLibrary HTTPS/PostgreSQL/filesystem and Emby/Jellyfin jointly completed a synthetic two-part publication on 2026-10-09. Exact revisions, focused recovery checks and remaining deployment limits are recorded in `docs/development/video-subscription-delivery-2026-10-09.md` in the coordinator workspace. This new package does not change historical candidate-v1 or authorize production deployment. Producer: Platform.Media; consumer: AssetLibrary. Coordinator: video-subscription-20261009.

The manifest file shape and exact layout retain the historical candidate-v1 schema and examples, including strict UTF-8, duplicate-key rejection, original-byte SHA256, stable BV/CID and episode numbering. The bundled inspector-semantics.md freezes the reused byte, shape and layout rules; its read-only execution restriction applies to the old inspector, not this authenticated publication protocol. Runtime code must bind this v1 package, never promote the old inspector's grants_file_operation=false into write permission.

## Authentication and staging

Use a dedicated service-publish credential resolved to a trusted principal. Existing service-read credentials do not gain write access. The host independently maps principal + library_id to publish capability and staging_ref to an immutable ready package under a trusted inbound root. No HTTP request accepts absolute paths or claimed permissions. The producer seals/renames the complete package before handoff and retains source bytes until an indexed receipt is observed.

## HTTP

All bodies are JSON, errors contain fixed code strings without sensitive paths or exception text. Prepare has a 2 MiB transport limit and a decoded 1 MiB manifest limit. Missing/invalid authorization is 401/403, unknown or inaccessible operation is 404, revision/idempotency conflict is 409, malformed input is 400, temporary unavailable is 503. Bodies reject unknown fields.

* POST `/assetlink/v1/media/packages/prepare`: `{idempotency_key,manifest_base64,manifest_digest}`. Key is 1–200 printable ASCII characters. Digest is lowercase SHA256 of decoded original UTF-8 bytes. The caller/key bind to that digest; exact replay returns the original operation, different content conflicts.
* POST `/assetlink/v1/media/packages/{operation_id}/publish`: `{confirmation_digest,expected_version}`. Accepted operation returns 202 and its view. Platform may automatically confirm its already-authorized subscription; there is no extra user confirmation popup.
* GET `/assetlink/v1/media/packages/{operation_id}`: returns the durable view. Lost/unknown HTTP outcomes are reconciled with this endpoint rather than repeating a file operation.
* POST `/assetlink/v1/media/packages/{operation_id}/cancel`: `{expected_version}`. Cancellation before publication prevents commit. After publication it does not delete the product; return actual state.

View: `{operation_id,package_id,library_id,status,version,manifest_digest,confirmation_digest,scope_revision,target_relative_path,expires_at,issues,receipt}`. operation_id/package_id/library_id use UUID strings; version is a positive integer; digests are lowercase SHA256. UTC instants are ISO8601 strings, nullable until applicable. issues is an array of `{code,location}` with optional/null relative location; never absolute paths. Receipt is null until publication evidence exists.

States: `prepared/rejected/queued/publishing/published/index_pending/indexed/cancelled/failed/manual_review`. `published` proves final files, `indexed` additionally proves asset entries. Unknown commit outcomes must remain reconcilable, never become false success. Receipt: `{operation_id,package_id,library_id,target_relative_path,published_at,indexed_at,files:[{path,kind,cid,size_bytes,sha256,entry_id}]}`. entry_id is nullable until indexed. A platform job with configured media servers is completed only after their independent verification.

## Durable behavior

Confirm binds principal, manifest digest, current scope revision and target. Recheck authorization at execution and before final commit. No database transaction covers copying or remote IO. Persist lease/generation and recover ownership after restart; late generations cannot commit. Copy to private staging on the destination volume, validate complete size/hash set, atomically publish without replacing existing destinations. Keep an ownership marker/journal outside the manifest package so a crash after rename can be distinguished from an unrelated pre-existing directory. Never infer ownership from matching content alone.

Published content survives index failure. Retry only incremental indexing, with no partial whole-library scan and no redownload. Replay returns the same receipt. Cross-subscription media deduplication belongs to Platform; AssetLibrary does not merge arbitrary existing targets or grant overwrite rights.

Required joint acceptance: original-byte manifest positive/negative cases; denied caller; replay/conflict; changed scope; cancellation; stale lease; interrupted copy; existing target; crash after rename; indexing failure/retry; real HTTP producer/consumer and persisted restart. Synthetic files/upstream are labelled explicitly. NAS durability and actual media servers are separate validation evidence.

## Owned version updates and additional parts

Prepare additionally accepts the optional pair `base_operation_id` and `base_version`. They must identify the current indexed operation for the same caller/library/target. Omit both for initial publication; the pair is part of the idempotency binding. A stale base conflicts. The host owns a unique current-version mapping per target and checks it again at commit.

An update supplies a complete new manifest/package. Every existing video retains its CID, episode number and path. By default its length and SHA256 also remain unchanged. New CIDs receive new stable episode numbers; deleting old video or changing layout is rejected. Metadata/cover may be updated under this explicit version operation. Platform groups selected parts into one package job and keeps previous sealed bytes available for later complete versions; a new part does not independently publish to an unrelated directory.

Prepare optionally accepts `replace_video_cids`, default `[]`, a unique array of canonical CID strings. A nonempty list requires the current owned base pair. Only listed existing CIDs may change video length/hash; CID/episode/path still remain fixed, and listed CIDs must exist in both versions. The replacement set is bound into the idempotency fingerprint and confirmation digest. Platform uses this for an explicit user quality-policy change or re-download, never as an implicit fallback or permission to overwrite unrelated media. The previous tree remains recoverable in the version backup. This permits changing from an explicitly selected lower quality to a higher quality in the same library without inventing a new target library.

Explicit quality replacement applies to both single-part and multipart packages. A single-part update retains its one CID and fixed single layout; adding parts requires an existing multipart layout and cannot silently convert a single package.

Platform subscriptions use the multipart layout from their first publication, including a one-part source, so later parts retain a stable target and episode path. Manual single-video downloads may use the single layout. Existing single publications are not silently migrated into subscription directories.

Copy the complete new version to private staging on the destination volume. Persist the update journal and ownership before moving the owned old tree to private backup, then move the validated new tree to the final path without overwriting. Recover both rename windows from durable journal and fingerprints; stale leases cannot finish a new generation. Preserve the old tree as recoverable backup. A brief missing final directory during this two-rename update is possible and must not be advertised as atomic directory exchange. Never replace unrelated targets. Old receipts remain readable but cannot be used as the current base. Retry failed indexing against the committed new version only.

Additional acceptance covers initial multi-P package, later part append, metadata-only update, old-CID removal/undeclared hash/episode changes rejected, explicit quality replacement, stale base, simultaneous updates and restart at both rename boundaries.
