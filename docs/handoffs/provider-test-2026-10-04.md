# Provider refusal projection contract — local candidate

Base: `c32f5d4ee18c9191ec76966715e85654567d7036`. Authorized exclusive changes: `contracts/provider-self-service/v1/{schema.json,examples.json,README.md}` and the corresponding existing joint test.

The existing public provider test object accepts optional `error_code` from a fixed enum. Existing required fields, execution outcomes and requests remain compatible; upstream status, raw body and provider-specific reason are excluded. The new refusal example and all previous examples pass schema validation.

The joint test accepts explicit candidate product paths instead of requiring historical worktree names. A local synthetic HTTP 400 `MissingSessionID` verifies gateway classification, Platform receipt privacy, public fixed-code projection and persistence after Platform restart. All 4 joint tests and the 1 contract/example test passed against the matching candidate Gateway, Platform and Companion sources. No external provider was contacted.

Products contain their own short handoffs. The coordinator owns integration, the contracts bundle deployment and any actual provider verification. No root main changes, task-board changes, production database migration or default model change were made by this task.
