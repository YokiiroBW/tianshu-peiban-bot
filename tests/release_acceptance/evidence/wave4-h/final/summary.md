# DEP-D release acceptance

Verdict: **incomplete**
Mode: local; runtime: product
Elapsed: 53.593 seconds

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
| timeout_cancel | pass | assertions_satisfied |
| log_causality | pass | assertions_satisfied |
| failure_truthfulness | pass | assertions_satisfied |
| abnormal_readiness | pass | assertions_satisfied |
| browser_rendering | not_run | http_suite_does_not_render_ui |
| real_model_quality | not_run | recorded_model_only |
| observation_24h | not_run | use_observe_command |
| model_revocation | pass | assertions_satisfied |

Candidate acceptance is not durable memory or archive completion.
Recorded models do not establish model quality. HTTP cookie checks do not establish browser rendering.
Local runs do not establish Linux container or NAS acceptance.
Evidence hash: 673239044db6a281d64002f40fb25f69955b710e7a098e90b09c18f341740f60
