-- Mechanical scan metadata, separate from business progress and Action receipts.
ALTER TABLE conversation.result_deliveries
    ADD COLUMN IF NOT EXISTS rbi_reconcile_due_at timestamptz DEFAULT now(),
    ADD COLUMN IF NOT EXISTS rbi_reconcile_checked_at timestamptz,
    ADD COLUMN IF NOT EXISTS rbi_reconcile_reason text,
    ADD COLUMN IF NOT EXISTS rbi_job_ids bigint[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS rbi_history_cursor bigint NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS rbi_history_upper bigint,
    ADD COLUMN IF NOT EXISTS rbi_history_gap boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS rbi_open_job_ids bigint[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS rbi_open_cursor bigint NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS rbi_failed_native jsonb NOT NULL DEFAULT '{}';

-- A confirmed dependency wait keeps its original lower bound even if the
-- native retry is lost. This is scheduling metadata, not a failure budget.
ALTER TABLE conversation.result_consumer_progress
    ADD COLUMN IF NOT EXISTS rbi_retry_at timestamptz;

CREATE INDEX IF NOT EXISTS rbi_result_reconcile_due
    ON conversation.result_deliveries (data_generation, rbi_reconcile_due_at, receipt_id)
    WHERE state='pending' AND rbi_reconcile_due_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS rbi_result_ops_candidates
    ON conversation.result_deliveries (data_generation, rbi_reconcile_checked_at, original_job_id)
    WHERE state='pending' AND original_job_id IS NOT NULL;

-- One resumable high-water mark for pre-ledger result jobs per generation.
CREATE TABLE IF NOT EXISTS conversation.rbi_result_legacy_scan (
    data_generation text PRIMARY KEY,
    cursor_job_id bigint NOT NULL DEFAULT 0,
    upper_job_id bigint,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Recovery searches native copies only by the frozen result identity.
CREATE INDEX IF NOT EXISTS rbi_result_native_identity
    ON procrastinate_jobs ((args->'event'->>'event_id'), (args->'event'->>'action_id'),
                          (args->'event'->>'result_revision'), id)
    WHERE task_name='conversation.result';

CREATE INDEX IF NOT EXISTS rbi_result_native_scan
    ON procrastinate_jobs (id) WHERE task_name='conversation.result';
