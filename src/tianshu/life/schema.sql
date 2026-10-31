CREATE SCHEMA IF NOT EXISTS life;
CREATE TABLE life.actor_state (
    actor_id text PRIMARY KEY, revision bigint NOT NULL DEFAULT 1, timezone text NOT NULL DEFAULT 'Asia/Shanghai',
    world_id text NOT NULL DEFAULT 'default', room_id text NOT NULL DEFAULT 'room', activity_id text,
    outfit_id text, room_controls jsonb NOT NULL DEFAULT '{"window":0,"sheer":0.5,"curtain":0,"desk_light":false,"ceiling_light":false}',
    weather_location text, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE life.goals (
    id text PRIMARY KEY, actor_id text NOT NULL, revision bigint NOT NULL DEFAULT 1,
    title text NOT NULL, motivation text NOT NULL, state text NOT NULL,
    priority double precision NOT NULL DEFAULT 0.5, progress double precision NOT NULL DEFAULT 0,
    matter_id text, scope_ids text[] NOT NULL, source_refs jsonb NOT NULL DEFAULT '[]',
    due_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE life.activities (
    id text PRIMARY KEY, actor_id text NOT NULL, goal_id text REFERENCES life.goals(id),
    revision bigint NOT NULL DEFAULT 1, title text NOT NULL, kind text NOT NULL,
    state text NOT NULL CHECK(state IN ('planned','running','paused','dormant','completed','cancelled')),
    next_due_at timestamptz, protected_until timestamptz, resume_condition text,
    checkpoint_step integer NOT NULL DEFAULT 0, checkpoint_position double precision NOT NULL DEFAULT 0,
    checkpoint_unit text NOT NULL DEFAULT 'step', checkpoint_data jsonb NOT NULL DEFAULT '{}',
    waiting_reason text, action_id text, last_receipt_id text, last_result_revision bigint NOT NULL DEFAULT -1, matter_id text, scope_ids text[] NOT NULL,
    source_refs jsonb NOT NULL DEFAULT '[]', created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX activity_one_running ON life.activities(actor_id) WHERE state='running';
CREATE TABLE life.commitments (
    id text PRIMARY KEY, actor_id text NOT NULL, revision bigint NOT NULL DEFAULT 1,
    title text NOT NULL, due_at timestamptz, timezone text NOT NULL, state text NOT NULL,
    target_ref text, person_id text, matter_id text, capability_id text, arguments jsonb NOT NULL DEFAULT '{}',
    recurrence text CHECK(recurrence IN ('daily','weekly')), weekdays integer[] NOT NULL DEFAULT '{}',
    grace_seconds integer NOT NULL DEFAULT 3600, scope_ids text[] NOT NULL, source_refs jsonb NOT NULL DEFAULT '[]',
    context jsonb NOT NULL, reason text, action_id text, last_receipt_id text, last_result_revision bigint NOT NULL DEFAULT -1, last_expression_id text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX commitments_due ON life.commitments(due_at) WHERE state IN ('scheduled','waiting');
CREATE TABLE life.contact_preferences (
    actor_id text NOT NULL, person_id text NOT NULL DEFAULT '', revision bigint NOT NULL DEFAULT 1,
    timezone text NOT NULL DEFAULT 'Asia/Shanghai', quiet_start text NOT NULL DEFAULT '23:00',
    quiet_end text NOT NULL DEFAULT '08:00', cooldown_seconds integer NOT NULL DEFAULT 1800,
    daily_quota integer NOT NULL DEFAULT 3, unanswered_limit integer NOT NULL DEFAULT 3,
    responsiveness double precision NOT NULL DEFAULT 0.5, enabled boolean NOT NULL DEFAULT true,
    availability text NOT NULL DEFAULT 'unknown', available_until timestamptz,
    scope_ids text[] NOT NULL, PRIMARY KEY(actor_id,person_id)
);
CREATE TABLE life.contact_candidates (
    id text PRIMARY KEY, actor_id text NOT NULL, revision bigint NOT NULL DEFAULT 1,
    target_ref text NOT NULL, person_id text, kind text NOT NULL, summary text NOT NULL,
    due_at timestamptz NOT NULL, expires_at timestamptz NOT NULL, state text NOT NULL,
    explicit boolean NOT NULL DEFAULT false, delivery_kind text NOT NULL,
    action_id text, context jsonb NOT NULL, source_refs jsonb NOT NULL DEFAULT '[]',
    payload jsonb NOT NULL DEFAULT '{}', reason text, expression_id text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE life.contacts (
    expression_id text PRIMARY KEY, actor_id text NOT NULL, person_id text,
    conversation_id text NOT NULL, candidate_id text, delivered_at timestamptz,
    replied_at timestamptz, signature jsonb NOT NULL, receipt jsonb NOT NULL
);
CREATE INDEX contacts_person_time ON life.contacts(actor_id,person_id,delivered_at DESC);
CREATE TABLE life.contact_replies(input_id text PRIMARY KEY);
CREATE TABLE life.plan_watermarks (
    actor_id text PRIMARY KEY, revision bigint NOT NULL DEFAULT 1,
    change_watermark text NOT NULL DEFAULT '', planned_watermark text NOT NULL DEFAULT '',
    next_due_at timestamptz, last_planned_at timestamptz, reason text
);
CREATE TABLE life.outfits (
    id text PRIMARY KEY, actor_id text NOT NULL, revision bigint NOT NULL DEFAULT 1,
    description text NOT NULL, appearance text NOT NULL, activity_kinds text[] NOT NULL DEFAULT '{}',
    reference jsonb, source_refs jsonb NOT NULL DEFAULT '[]', scope_ids text[] NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE life.diaries (
    id text PRIMARY KEY, actor_id text NOT NULL, day date NOT NULL, revision bigint NOT NULL DEFAULT 1,
    state text NOT NULL DEFAULT 'draft', current_revision bigint NOT NULL DEFAULT 0,
    published_revision bigint, generation_id text, context jsonb, reason text, material_key text,
    generation_recipe jsonb, last_reviewed_at timestamptz, last_changed_at timestamptz,
    episode_ids text[] NOT NULL, source_refs jsonb NOT NULL,
    scope_ids text[] NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(actor_id,day)
);
CREATE TABLE life.diary_revisions (
    diary_id text NOT NULL REFERENCES life.diaries(id), revision bigint NOT NULL,
    content text NOT NULL, model_attempt_id text, recipe jsonb NOT NULL,
    disclosure_ref jsonb, publication_metadata jsonb,
    created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(diary_id,revision)
);
CREATE TABLE life.works (
    id text PRIMARY KEY, actor_id text NOT NULL, revision bigint NOT NULL DEFAULT 1,
    title text NOT NULL, synopsis text NOT NULL DEFAULT '', outline text NOT NULL DEFAULT '',
    characters text NOT NULL DEFAULT '', scope_ids text[] NOT NULL, source_refs jsonb NOT NULL DEFAULT '[]',
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE life.chapters (
    id text PRIMARY KEY, work_id text NOT NULL REFERENCES life.works(id), position integer NOT NULL,
    revision bigint NOT NULL DEFAULT 1, title text NOT NULL, plan text NOT NULL DEFAULT '',
    state text NOT NULL DEFAULT 'planned', current_revision bigint NOT NULL DEFAULT 0,
    published_revision bigint, generation_id text, generation_context jsonb, generation_recipe jsonb, reason text, review_state text, review_note text,
    UNIQUE(work_id,position)
);
CREATE TABLE life.chapter_revisions (
    chapter_id text NOT NULL REFERENCES life.chapters(id), revision bigint NOT NULL,
    content text NOT NULL, work_revision bigint NOT NULL, model_attempt_id text,
    recipe jsonb NOT NULL, disclosure_ref jsonb, publication_metadata jsonb, created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(chapter_id,revision)
);
CREATE TABLE life.readings (
    id text PRIMARY KEY, actor_id text NOT NULL, revision bigint NOT NULL DEFAULT 1,
    source_ref jsonb NOT NULL, requested jsonb NOT NULL, covered jsonb NOT NULL DEFAULT '[]',
    next_position jsonb, state text NOT NULL, scope_ids text[] NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE life.routines (
    actor_id text PRIMARY KEY,revision bigint NOT NULL DEFAULT 1,enabled boolean NOT NULL DEFAULT true,
    timezone text NOT NULL DEFAULT 'Asia/Shanghai',wake_at text NOT NULL DEFAULT '08:00',sleep_at text NOT NULL DEFAULT '23:00',
    idle_seconds integer NOT NULL DEFAULT 5400,context jsonb NOT NULL,next_due_at timestamptz NOT NULL,last_tick_at timestamptz
);
