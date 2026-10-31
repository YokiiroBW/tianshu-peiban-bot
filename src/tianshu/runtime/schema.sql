CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS runtime;
CREATE TABLE runtime.job_keys (
    key text PRIMARY KEY,
    task text NOT NULL,
    job_id bigint UNIQUE,
    subject_id text,
    matter_id text,
    cancelled_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX job_keys_subject ON runtime.job_keys(subject_id, matter_id);
CREATE TABLE runtime.projection_cursors (
    consumer text NOT NULL,
    subject_id text NOT NULL,
    source_revision bigint NOT NULL DEFAULT 0,
    cursor jsonb NOT NULL DEFAULT '{}',
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (consumer, subject_id)
);
