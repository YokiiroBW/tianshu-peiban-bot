# Actor capabilities candidate v1

Status: isolated-development candidate, not a production release. TS-309 / AL-01.
Platform owns POST /internal/v1/actor-capabilities/operate with discover, invoke,
get, reconcile and cancel. Only authenticated configured Companion service callers
may use it, through the dedicated actor.capabilities transport action.
Transport identity is never caller-controlled principal or operator.

Use the current Platform role registry on every call. role_version is exactly
Platform RoleRuntime.get(actor_id)["version"], never companion_version,
memory_version or profile_revision. discover may omit role_version to bootstrap;
its response returns the current authoritative version. If supplied it must match.
Other operations require that version. A discover result
is not a grant. Resolve the requested binding from the existing plugin binding
repository: enabled character binding, exact character_id == actor_id, current
instance, selected action in its entries, exact binding revision, effective account
availability. entries must be explicit and contain the action; a missing entries
list grants no actor actions. This does not change existing workspace management
binding behavior. binding_revision is the owning instance.revision (the revision
of the effective configuration/bindings snapshot), not binding.revision submitted
by a UI client. An arbitrary existing binding is not sufficient authorization.

Reuse the existing plugin execution ledger and its authorize callback. Recheck
actor, binding, instance/action and account authorization when queued work starts
and before privileged host capability use. For get/reconcile/cancel verify the
original invocation belongs to this actor and binding; do not allow caller-chosen
operator/admin identity. The server constructs the stable principal as
"actor-capabilities:" + actor_id, where actor_id already includes its actor: prefix,
independent of whether the originating action came from dialogue or background.

Use original intent_key and invocation_id across lost responses and recovery.
unknown effects are not successful and are not automatically resent. Preserve the
execution owner's not_applied result for a confirmed write without an effect;
none is the existing read-only receipt value, not a substitute for not_applied. Explicit
reconcile consumes the existing execution owner's procedure; do not create a second
invocation or queue. Role/binding revocation prevents queued and later privileged
work; it cannot retract a side effect that already happened.

The binding/configuration UI stays in the existing plugin panel. Reuse character
scope and entries. Plugin instance secrets stay in the existing Vault. Media account
IDs may be bound by the appropriate adapter, but a generic plugin bridge does not
magically gain Bilibili cookie actions. Do not expose unsupported account actions.
No credentials, management-only inputs, checkpoint internals or arbitrary exceptions
are returned in the actor projection. Validate result against the declared schema,
apply existing redaction, and return only the bounded receipt fields in this package.

Requests at most 256 KiB; responses at most 1 MiB; directory at most 128 capabilities.
Large declared schemas/results are rejected or reported unavailable, not silently
truncated. Local schema resolution only. A capability is listed ready only when
its real action and required adapter are available; catalog declarations are not
handlers. No caller API can expand a binding or change an account selection.

The JSON schemas describe the wire shape. Current-role, binding ownership, source
freshness, secret redaction and no-duplicate-effect behavior require runtime tests.
Deployment credentials must explicitly grant actor.capabilities to the configured
Companion service. This candidate does not modify production credentials.
Existing user memory, group observation, BotDelivery, model scheduling and published
user contracts are unchanged by this package.
