# Published role-runtime/v1

Accepted 2026-09-30 after three-owner contract/example validation and HTTPS joint tests. Fixed versions: Platform 17af1b4, Companion 9863800, Memory 870d926. Review: docs/development/reviews/role-runtime-final-gate-2026-09-30.md. Production deployment is tracked separately.

# Role runtime v1 candidate

This is a proposed contract, not a root `contracts/` publication. Platform owns the
web application intent, provider revision, source registration, and bot binding.
Companion owns the role's persona revision and turn snapshot. Memory owns the
exact actor grant and scoped memory. All remote calls use the existing distinct
HTTPS service credentials; a browser session never calls a peer directly.

`POST /api/web/roles/apply` takes `platform_apply`. `client_id` identifies one
immutable application intent; replay with different bytes is a conflict. The
response carries a `platform_role`. `pending` never counts as active. Platform
performs `core_apply(enabled=false)`, `memory_apply`, checks the exact provider
revision, then `core_apply(enabled=true)` and marks the intent active. A peer
response loss is recovered with the same stage request ID. On restart an active
role is pending until Core and Memory answer with the recorded versions.

Each Core write also carries the immutable Platform `application_id`. Companion
receives the authenticated console principal as `operator` for Persona approval
and publication history; the service credential still authenticates the call.
Companion may enable only the exact disabled snapshot created by that application, with
the same name, profile binding/version, and capabilities. A source profile edit
after the pause cannot change that snapshot or prevent its enable. Applying a
profile uses Companion's draft, approval, and publication ledger in the same
transaction as the role fact and receipt. A new role is an unpublished target
until that application succeeds.

`POST /api/web/roles/cancel` takes `platform_cancel` for a pending role. It
records a new intent, removes the web admission, and reconciles the current
Core and Memory versions to explicit disabled facts. A lost disable receipt is
replayed with its original request body. Version conflicts trigger a fresh
read and compare-and-swap attempt. Platform reports `disabled` only after both
owners confirm denial. This recovery does not require a usable provider or a
live source profile; the pinned Core snapshot supplies the disable payload.
Old apply/retry receipts cannot reactivate the canceled role. A corrected
configuration can then be submitted as a new application.

For a first explicit adoption of a deployment role, Core may list the actor
only in `legacy_roles` because no runtime fact exists yet. Cancel must create
and confirm a version-zero-to-one disabled Core fact using the original
published Persona and binding before Memory denial can complete, even when a
pending correction had selected a different profile. That proposed profile is
never applied by cancellation. If neither a runtime role nor the expected
legacy role can be confirmed, cancel remains
pending. For an uncreated new dynamic actor, cancel creates no Core role.

`POST /internal/v1/role-runtime/manage` on Companion accepts `operation:list`
or `operation:apply` plus `core_apply`. Only its registered Platform caller can
write. `POST /internal/v1/role-runtime/authorize` on Memory accepts `status`
or `memory_apply`; only the configured Platform `role_admin` caller can write.
Each write uses `expected_version` and an idempotency key. An enabled grant is
only usable by a caller explicitly configured with `allow_runtime_roles:true`;
the origin and exact actor scope checks still apply.

For a production rollout, quiesce writes and take a consistent backup of the
Platform base DB plus `<database_path>.roles.sqlite`, the complete provider
catalog directory including its key, the Companion DB, and the Memory DB plus
`role_grants_database_path`. Restore these as one set. Provision unique service
tokens, trusted CA paths and the absolute grant path before enabling the page.
Existing deployment roles appear as `legacy_roles` in the view. An operator may
explicitly adopt one with its current actor ID and `profile_id/profile_version`
both null. This retains its published persona and static source/BOT binding;
the selected provider becomes role-specific. Adoption is not automatic and no
production legacy role is changed by this task. A managed disable overrides
the old static role/grant while retaining its history and configuration.

The joint test uses real HTTPS owners and the read-only Gateway baseline. Its
isolated gateway fixture allows only 127.0.0.1 as a synthetic recorded model
target; production Gateway target policy remains unchanged.
