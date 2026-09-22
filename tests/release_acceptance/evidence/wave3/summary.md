# DEP-D release acceptance

Verdict: **incomplete**
Mode: local; runtime: product
Elapsed: 329.516 seconds

| Case | Status | Reason |
|---|---|---|
| runtime_binding | pass | assertions_satisfied |
| configuration_loading | pass | assertions_satisfied |
| health_and_auth | pass | assertions_satisfied |
| web_login_csrf | pass | assertions_satisfied |
| dialogue_model_reply | pass | assertions_satisfied |
| memory_candidate | pass | assertions_satisfied |
| memory_finalized | pass | assertions_satisfied |
| chat_archive | pass | assertions_satisfied |
| memory_backlog_boundary | pass | assertions_satisfied |
| unknown_no_resend | dependency_missing | product_control_operation_unavailable_fault |
| restart_recovery | dependency_missing | prerequisite_unknown_no_resend |
| source_revocation | dependency_missing | product_control_operation_unavailable_revoke_source |
| model_revocation | dependency_missing | product_control_operation_unavailable_fault |
| timeout_cancel | dependency_missing | product_control_operation_unavailable_fault |
| log_causality | pass | assertions_satisfied |
| failure_truthfulness | dependency_missing | prerequisite_unknown_no_resend |
| abnormal_readiness | dependency_missing | product_control_operation_unavailable_fault |
| browser_rendering | not_run | http_suite_does_not_render_ui |
| real_model_quality | not_run | recorded_model_only |
| observation_24h | not_run | use_observe_command |

Candidate acceptance is not durable memory or archive completion.
Recorded models do not establish model quality. HTTP cookie checks do not establish browser rendering.
Local runs do not establish Linux container or NAS acceptance.
Evidence hash: c14782ea0ef92c73aaefaf3fa3904e4120560f7a01d70e7127727f5a77898877
