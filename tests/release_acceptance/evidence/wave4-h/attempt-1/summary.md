# DEP-D release acceptance

Verdict: **failed**
Mode: local; runtime: product
Elapsed: 31.266 seconds

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
| unknown_no_resend | pass | assertions_satisfied |
| restart_recovery | pass | assertions_satisfied |
| source_revocation | pass | assertions_satisfied |
| timeout_cancel | fail | real_gateway_timeout_missing |
| log_causality | pass | assertions_satisfied |
| failure_truthfulness | fail | causal_event_missing |
| abnormal_readiness | fail | log_fault_readiness_not_failed_companion |
| browser_rendering | not_run | http_suite_does_not_render_ui |
| real_model_quality | not_run | recorded_model_only |
| observation_24h | not_run | use_observe_command |
| model_revocation | fail | model_revoke_unconfirmed |

Candidate acceptance is not durable memory or archive completion.
Recorded models do not establish model quality. HTTP cookie checks do not establish browser rendering.
Local runs do not establish Linux container or NAS acceptance.
Evidence hash: ea90984b98a666e2ab1a92371aeaab931e8be669efffd4d12dd61d7fa9f5fc7a
