CREATE SCHEMA IF NOT EXISTS models;
CREATE TABLE models.quota_groups (
    quota_group text PRIMARY KEY,
    concurrency integer NOT NULL CHECK(concurrency > 0),
    interactive_reserve integer NOT NULL CHECK(interactive_reserve >= 0),
    background_max integer NOT NULL CHECK(background_max >= 0),
    rpm integer CHECK(rpm > 0),
    tpm integer CHECK(tpm > 0),
    max_queue integer NOT NULL DEFAULT 128 CHECK(max_queue > 0),
    max_wait_seconds double precision NOT NULL DEFAULT 60 CHECK(max_wait_seconds > 0),
    unknown_hold_seconds double precision NOT NULL DEFAULT 300 CHECK(unknown_hold_seconds > 0),
    unit_limits jsonb NOT NULL DEFAULT '{}',
    interactive_rpm_reserve integer NOT NULL DEFAULT 0 CHECK(interactive_rpm_reserve>=0),
    background_rpm_max integer CHECK(background_rpm_max>=0),
    interactive_unit_reserve jsonb NOT NULL DEFAULT '{}',
    background_unit_max jsonb NOT NULL DEFAULT '{}',
    revision bigint NOT NULL DEFAULT 1,
    CHECK(interactive_reserve <= concurrency),
    CHECK(background_max <= concurrency)
);
CREATE TABLE models.providers (
    provider_id text PRIMARY KEY,
    name text NOT NULL,
    base_url text NOT NULL,
    credential_ref text NOT NULL,
    protocol text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    revision bigint NOT NULL DEFAULT 1
);
CREATE TABLE models.routes (
    route_id text PRIMARY KEY,
    provider_id text NOT NULL REFERENCES models.providers,
    model text NOT NULL,
    quota_group text NOT NULL REFERENCES models.quota_groups,
    verified_capabilities text[] NOT NULL DEFAULT '{}',
    verification jsonb,
    parameters jsonb NOT NULL DEFAULT '{}',
    enabled boolean NOT NULL DEFAULT true,
    revision bigint NOT NULL DEFAULT 1
);
CREATE TABLE models.bindings (
    function text NOT NULL,
    service_class text NOT NULL CHECK(service_class IN ('interactive','due','elastic')),
    actor_id text NOT NULL DEFAULT '',
    route_id text NOT NULL REFERENCES models.routes,
    revision bigint NOT NULL DEFAULT 1,
    PRIMARY KEY(function,service_class,actor_id)
);
CREATE TABLE models.attempts (
    attempt_id text PRIMARY KEY,
    intent_id text NOT NULL,
    principal_id text NOT NULL,
    actor_id text,
    conversation_id text,
    function text NOT NULL,
    service_class text NOT NULL,
    route_id text NOT NULL,
    route_revision bigint NOT NULL,
    provider_id text NOT NULL,
    model text NOT NULL,
    protocol text NOT NULL,
    quota_group text NOT NULL,
    request_digest text NOT NULL,
    source_refs jsonb NOT NULL DEFAULT '[]',
    context_fingerprint text NOT NULL,
    persona_revision bigint,
    status text NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled','unknown')),
    dispatch text NOT NULL CHECK(dispatch IN ('not_started','submitted','unknown')),
    queued_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    deadline_at timestamptz NOT NULL,
    hold_until timestamptz,
    estimated_tokens bigint,
    charged_tokens bigint,
    estimated_units jsonb NOT NULL DEFAULT '{}',
    charged_units jsonb NOT NULL DEFAULT '{}',
    billing_account text,
    usage jsonb,
    provider_handle text,
    first_output_ms double precision,
    error_code text,
    result jsonb,
    output_observed boolean NOT NULL DEFAULT false,
    cancel_requested boolean NOT NULL DEFAULT false,
    broker_instance text,
    revision bigint NOT NULL DEFAULT 1
);
CREATE INDEX attempts_resource ON models.attempts(quota_group,status,hold_until);
CREATE INDEX attempts_rate ON models.attempts(quota_group,started_at) WHERE started_at IS NOT NULL;
CREATE INDEX attempts_owner ON models.attempts(principal_id,queued_at DESC);
CREATE INDEX attempts_sources ON models.attempts USING gin(source_refs jsonb_path_ops);
CREATE TABLE models.cancellations (
    attempt_id text PRIMARY KEY,
    requested_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE models.embedding_profile (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    route_id text NOT NULL REFERENCES models.routes,
    model_version text NOT NULL,
    dimensions integer NOT NULL DEFAULT 1024 CHECK(dimensions=1024),
    normalization text NOT NULL,
    query_instruction text NOT NULL,
    route_fingerprint text NOT NULL,
    revision bigint NOT NULL DEFAULT 1
);
