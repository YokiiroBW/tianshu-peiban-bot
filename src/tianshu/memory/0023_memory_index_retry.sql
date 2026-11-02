-- Logical computations and their immutable model identities, separate from queue delivery.
CREATE TABLE memory.index_jobs (
    index_id text PRIMARY KEY,
    kind text NOT NULL CHECK(kind IN ('claim','hypothesis','episode','matter','reflection')),
    object_id text NOT NULL,
    revision bigint NOT NULL,
    profile_key text NOT NULL,
    profile jsonb,
    state text NOT NULL DEFAULT 'pending'
        CHECK(state IN ('pending','running','observing','retry_wait','succeeded','obsolete','revoked','cancelled','failed')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count>=0),
    next_due timestamptz,
    last_error text,
    lease_owner text,
    lease_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(kind,object_id,revision,profile_key)
);
CREATE INDEX index_jobs_due ON memory.index_jobs(next_due,index_id)
    WHERE state IN ('pending','running','observing','retry_wait');
CREATE TABLE memory.index_attempts (
    index_id text NOT NULL REFERENCES memory.index_jobs,
    number integer NOT NULL CHECK(number>0),
    intent_id text NOT NULL,
    step_id text NOT NULL UNIQUE,
    model_attempt_id text NOT NULL UNIQUE,
    status text NOT NULL DEFAULT 'prepared',
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(index_id,number)
);

-- A bounded ordinary consistency scan, not a periodic full-table anti-join.
CREATE TABLE memory.index_scan (
    kind text PRIMARY KEY,
    after_id text NOT NULL DEFAULT '',
    after_revision bigint NOT NULL DEFAULT 0
);
INSERT INTO memory.index_scan(kind) VALUES('claim'),('episode'),('matter'),('reflection');
CREATE INDEX claim_missing_index ON memory.claim_revisions(claim_id,revision)
    WHERE state='active' AND current_status='current' AND (embedding IS NULL OR lexical='');
CREATE INDEX episode_missing_index ON memory.episodes(episode_id,revision)
    WHERE active AND (embedding IS NULL OR lexical='');
CREATE INDEX matter_missing_index ON memory.matters(matter_id,revision)
    WHERE active AND (embedding IS NULL OR lexical='');
CREATE INDEX reflection_missing_index ON memory.reflections(proposal_id,revision)
    WHERE active AND (embedding IS NULL OR lexical='');
