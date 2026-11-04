CREATE SCHEMA IF NOT EXISTS operations;

CREATE TABLE operations.settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    revision bigint NOT NULL CHECK (revision > 0),
    value jsonb NOT NULL,
    author_context jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE operations.incidents (
    incident_id text PRIMARY KEY,
    fingerprint text NOT NULL UNIQUE,
    dependency text NOT NULL,
    error_family text NOT NULL,
    version text NOT NULL,
    severity text NOT NULL,
    state text NOT NULL DEFAULT 'open',
    revision bigint NOT NULL DEFAULT 1,
    occurrences bigint NOT NULL DEFAULT 0,
    episode bigint NOT NULL DEFAULT 1,
    unscoped_occurrences bigint NOT NULL DEFAULT 0,
    uncorrelated_occurrences bigint NOT NULL DEFAULT 0,
    first_seen timestamptz NOT NULL DEFAULT now(),
    last_seen timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    summary jsonb NOT NULL DEFAULT '{}',
    affected jsonb NOT NULL DEFAULT '[]',
    diagnosis_count integer NOT NULL DEFAULT 0,
    diagnostic_state text NOT NULL DEFAULT 'pending',
    recovery_action text,
    recovery_lineage text,
    last_observation jsonb
);
CREATE INDEX incidents_open ON operations.incidents(state, last_seen DESC);
CREATE TABLE operations.consumer_checkpoints (
    consumer text PRIMARY KEY,
    checkpoint text,
    coverage jsonb NOT NULL DEFAULT '{}',
    freshness jsonb NOT NULL DEFAULT '{}',
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE operations.incident_events (
    event_id text PRIMARY KEY,
    incident_id text NOT NULL REFERENCES operations.incidents,
    observed_at timestamptz NOT NULL DEFAULT now()
);
-- Only an explicit, existing root incident establishes a causal relationship.
-- Incidents without that evidence continue to notify independently.
CREATE TABLE operations.incident_causes (
    incident_id text NOT NULL REFERENCES operations.incidents,
    episode bigint NOT NULL,
    root_incident_id text NOT NULL REFERENCES operations.incidents,
    root_episode bigint NOT NULL,
    PRIMARY KEY(incident_id,episode,root_incident_id,root_episode)
);
CREATE TABLE operations.incident_targets (
    incident_id text NOT NULL REFERENCES operations.incidents,
    episode bigint NOT NULL,
    target_id text NOT NULL,
    target jsonb NOT NULL,
    state text NOT NULL DEFAULT 'open',
    PRIMARY KEY(incident_id,episode,target_id)
);
CREATE TABLE operations.diagnostics (
    attempt_id text PRIMARY KEY,
    incident_id text NOT NULL REFERENCES operations.incidents,
    incident_revision bigint NOT NULL,
    settings_revision bigint NOT NULL,
    route_id text NOT NULL,
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    last_observed_at timestamptz,
    receipt jsonb,
    decision jsonb,
    error_code text
);
-- An Operations control budget limits repeated wake/observe commands; it is
-- not a model/physical attempt and never replaces the domain recovery lineage.
CREATE TABLE operations.control_lineages (
    control_id text PRIMARY KEY,
    data_generation text NOT NULL,
    material_digest text NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL CHECK (max_attempts > 0)
);
CREATE TABLE operations.recoveries (
    attempt_id text PRIMARY KEY,
    incident_id text NOT NULL REFERENCES operations.incidents,
    lineage_id text NOT NULL,
    control_id text,
    data_generation text,
    policy_mode text NOT NULL DEFAULT 'reserved',
    incident_revision bigint NOT NULL,
    action text NOT NULL,
    target jsonb NOT NULL,
    target_id text,
    attempt_number integer,
    state text NOT NULL,
    claim_token text,
    owner_token text,
    lease_until timestamptz,
    last_observed_at timestamptz,
    due_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    result jsonb,
    observation jsonb
);
CREATE TABLE operations.notification_outbox (
    notification_id text PRIMARY KEY,
    incident_id text NOT NULL REFERENCES operations.incidents,
    kind text NOT NULL,
    episode bigint NOT NULL,
    incident_revision bigint NOT NULL,
    channel text NOT NULL,
    target text NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    attempts integer NOT NULL DEFAULT 0,
    due_at timestamptz NOT NULL DEFAULT now(),
    owner_token text,
    lease_until timestamptz,
    inflight_state text,
    receipt jsonb,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX notification_due ON operations.notification_outbox(due_at)
    WHERE status IN ('pending', 'sending');
CREATE INDEX notification_incident_episode ON operations.notification_outbox(incident_id,episode,kind,channel,target);

-- The latest projection of each source identity is retained so revisions can
-- subtract their old contribution. Source ledgers remain the authority.
CREATE TABLE operations.statistic_events (
    family text NOT NULL,
    event_id text NOT NULL,
    revision bigint NOT NULL,
    digest text NOT NULL,
    occurred_at timestamptz NOT NULL,
    module text NOT NULL,
    actor_id text NOT NULL DEFAULT '',
    route_id text NOT NULL DEFAULT '',
    state text NOT NULL DEFAULT '',
    metrics jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(family,event_id)
);
CREATE TABLE operations.statistic_buckets (
    grain text NOT NULL CHECK (grain IN ('minute','hour')),
    bucket timestamptz NOT NULL,
    family text NOT NULL,
    module text NOT NULL,
    actor_id text NOT NULL DEFAULT '',
    route_id text NOT NULL DEFAULT '',
    state text NOT NULL DEFAULT '',
    metric text NOT NULL,
    total double precision NOT NULL DEFAULT 0,
    samples bigint NOT NULL DEFAULT 0,
    unknown bigint NOT NULL DEFAULT 0,
    PRIMARY KEY(grain,bucket,family,module,actor_id,route_id,state,metric)
);
CREATE INDEX statistic_buckets_range ON operations.statistic_buckets(grain,bucket);
CREATE TABLE operations.statistic_producers (
    producer text PRIMARY KEY,
    watermark jsonb,
    last_seen timestamptz NOT NULL DEFAULT now(),
    complete boolean NOT NULL DEFAULT false,
    detail jsonb
);
