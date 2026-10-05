# Skills v1

Companion owns the per-role skill catalog and installed handlers. Platform uses existing role management authorization, the integration proxy and encrypted credential catalog. Skills extend the existing native tool loop and domain actions; they do not create another conversation runtime or job ledger.

`POST /internal/v1/skills/read` and `/manage` use `read_request`, `manage_request` and `response` in `schemas/skills.json`. Reads select `list`, `detail` (requires `skill_id`) or `sources`. All results carry the current actor version. Management writes use the existing operation ledger for request replay and compare `expected_version`; actor versions begin at 1.

Definitions describe capabilities; installed handlers own executable operations, argument schemas, configuration validation and result conversion. Unknown handlers remain unsupported and are never exposed as callable model tools. Metadata cannot install code or grant authority. Future aggregation categories are extension points, not implemented download services.

`skill.enable` and `skill.disable` control new role-selected conversation and proactive calls. Existing domain jobs continue completion and delivery under their original ownership; direct administrative domain management remains available through its existing interface. ComfyUI inherits its existing global connection and per-role workflow configuration. The skill catalog does not duplicate these settings.

`catalog_version` is a stable catalog content hash. Each skill `revision` covers its own effective definition, configuration and enabled state. A call pins only the selected skill; unrelated skill updates and refresh timestamps do not invalidate it. The selected revision is rechecked before domain execution.

Sources fetch bounded JSON manifests, never executable code. A refresh validates all definitions and declared operations before replacing that source's catalog entries. Source ownership prevents overwriting built-in skills or another source's entries. Skills newly discovered from a source start disabled. Updating a definition cannot silently enlarge its installed handler's operation set. Removed entries cannot start new calls; already-created jobs retain their original semantics. A failed refresh preserves the previous catalog and reports its failure explicitly.

Configuration options are validated by the installed handler. Public configuration uses an allowlisted projection: no credential references, tokens or secret-bearing URL components. Secret values use the existing credential service with purpose `companion.skills` and the configured URL origin as audience. Fetches do not follow redirects or reuse credentials across origins. Read results expose only whether credentials are configured.

Examples are synthetic contract fixtures. Publication is an implementation baseline, not a declaration of live GSCore, model, GPU or QQ acceptance. Deployment remains paused by the user.
