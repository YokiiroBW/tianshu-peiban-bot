-- Keep existing attempts, deadlines and explicit author quota values unchanged.
-- A new queued attempt has a frozen duration, but no execution deadline yet.
ALTER TABLE models.attempts ALTER COLUMN deadline_at DROP NOT NULL;
ALTER TABLE models.attempts ADD COLUMN execution_timeout_seconds double precision
    CHECK(execution_timeout_seconds > 0 AND execution_timeout_seconds < 'Infinity'::double precision);
ALTER TABLE models.attempts ADD COLUMN business_deadline_at timestamptz;
ALTER TABLE models.attempts ADD COLUMN max_queue_wait double precision
    CHECK(max_queue_wait >= 0 AND max_queue_wait < 'Infinity'::double precision);
ALTER TABLE models.quota_groups ALTER COLUMN max_wait_seconds DROP NOT NULL;
ALTER TABLE models.quota_groups ALTER COLUMN max_wait_seconds DROP DEFAULT;
