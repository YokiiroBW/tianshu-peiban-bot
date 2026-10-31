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
CREATE TABLE runtime.processes (
    instance_id text PRIMARY KEY,
    role text NOT NULL,
    ready boolean NOT NULL,
    registration jsonb NOT NULL,
    heartbeat_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE runtime.commands (
    principal_id text NOT NULL,
    operation text NOT NULL,
    client_request_id text NOT NULL,
    body_hash text NOT NULL,
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(principal_id,operation,client_request_id)
);
CREATE TABLE runtime.credentials (
    reference text PRIMARY KEY,
    nonce bytea NOT NULL,
    ciphertext bytea NOT NULL,
    revision bigint NOT NULL DEFAULT 1,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE runtime.projection_cursors (
    consumer text NOT NULL,
    subject_id text NOT NULL,
    source_revision bigint NOT NULL DEFAULT 0,
    cursor jsonb NOT NULL DEFAULT '{}',
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (consumer, subject_id)
);
