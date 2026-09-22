# DEP-D release acceptance

Verdict: **incomplete**
Mode: local; runtime: product
Elapsed: 0.0 seconds

| Case | Status | Reason |
|---|---|---|
| runtime_binding | dependency_missing | control_adapter_missing |
| configuration_loading | dependency_missing | prerequisite_runtime_binding |
| health_and_auth | dependency_missing | service_endpoint_missing |
| web_login_csrf | dependency_missing | credential_environment_missing |
| dialogue_model_reply | dependency_missing | prerequisite_web_login_csrf |
| memory_candidate | dependency_missing | prerequisite_dialogue_model_reply |
| memory_finalized | dependency_missing | prerequisite_memory_candidate |
| chat_archive | dependency_missing | prerequisite_dialogue_model_reply |
| memory_backlog_boundary | dependency_missing | prerequisite_dialogue_model_reply |
| unknown_no_resend | dependency_missing | prerequisite_dialogue_model_reply |
| restart_recovery | dependency_missing | prerequisite_unknown_no_resend |
| source_revocation | dependency_missing | prerequisite_dialogue_model_reply |
| model_revocation | dependency_missing | prerequisite_dialogue_model_reply |
| timeout_cancel | dependency_missing | prerequisite_dialogue_model_reply |
| log_causality | dependency_missing | prerequisite_dialogue_model_reply |
| failure_truthfulness | dependency_missing | prerequisite_unknown_no_resend |
| abnormal_readiness | dependency_missing | prerequisite_runtime_binding |
| browser_rendering | not_run | http_suite_does_not_render_ui |
| real_model_quality | not_run | recorded_model_only |
| observation_24h | not_run | use_observe_command |

Candidate acceptance is not durable memory or archive completion.
Recorded models do not establish model quality. HTTP cookie checks do not establish browser rendering.
Local runs do not establish Linux container or NAS acceptance.
Evidence hash: dde05ecb8b6a823fd34c474acb6f8fb339daa4c8261e2804dde228ac19cfcabb
