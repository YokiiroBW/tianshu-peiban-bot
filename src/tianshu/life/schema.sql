CREATE SCHEMA IF NOT EXISTS life;
CREATE TABLE life.actor_state (
    actor_id text PRIMARY KEY, revision bigint NOT NULL DEFAULT 1, timezone text NOT NULL DEFAULT 'Asia/Shanghai',
    world_id text NOT NULL DEFAULT 'default', room_id text NOT NULL DEFAULT 'room', activity_id text,
    outfit_id text, room_controls jsonb NOT NULL DEFAULT '{"window":0,"sheer":0.5,"curtain":0,"desk_light":0,"bedside_light":0,"desk_warmth":0.8,"bedside_warmth":1}',
    weather_location text, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE life.goals (
    id text PRIMARY KEY, actor_id text NOT NULL, revision bigint NOT NULL DEFAULT 1,
    title text NOT NULL, motivation text NOT NULL, state text NOT NULL,
    priority double precision NOT NULL DEFAULT 0.5, progress double precision NOT NULL DEFAULT 0,
    matter_id text, scope_ids text[] NOT NULL, source_refs jsonb NOT NULL DEFAULT '[]',
    intention_source_refs jsonb NOT NULL DEFAULT '[]',
    due_at timestamptz, criteria jsonb NOT NULL DEFAULT '{}', origin text NOT NULL DEFAULT 'authored',
    reason text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE life.goal_revisions (
    goal_id text NOT NULL REFERENCES life.goals(id), revision bigint NOT NULL, reason text NOT NULL,
    snapshot jsonb NOT NULL, source_refs jsonb NOT NULL DEFAULT '[]', generation_key text,
    created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(goal_id,revision)
);
CREATE TABLE life.goal_progress_receipts (
    goal_id text NOT NULL REFERENCES life.goals(id), action_id text NOT NULL, receipt_id text NOT NULL,
    activity_id text NOT NULL, result_revision bigint NOT NULL, satisfied boolean NOT NULL,
    source_refs jsonb NOT NULL, milestone_key text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(goal_id,action_id), UNIQUE(goal_id,milestone_key)
);
CREATE TABLE life.goal_milestones (
    goal_id text NOT NULL REFERENCES life.goals(id), milestone_id text NOT NULL, producer text NOT NULL,
    evidence_id text NOT NULL, version bigint NOT NULL, source_refs jsonb NOT NULL, satisfied boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(goal_id,milestone_id)
);
CREATE TABLE life.activities (
    id text PRIMARY KEY, actor_id text NOT NULL, goal_id text REFERENCES life.goals(id),
    revision bigint NOT NULL DEFAULT 1, title text NOT NULL, kind text NOT NULL,
    state text NOT NULL CHECK(state IN ('planned','running','paused','dormant','completed','cancelled')),
    next_due_at timestamptz, protected_until timestamptz, resume_condition text,
    checkpoint_step integer NOT NULL DEFAULT 0, checkpoint_position double precision NOT NULL DEFAULT 0,
    checkpoint_unit text NOT NULL DEFAULT 'step', checkpoint_data jsonb NOT NULL DEFAULT '{}',
    waiting_reason text, wake_condition jsonb, action_id text, last_receipt_id text, last_result_revision bigint NOT NULL DEFAULT -1, matter_id text, scope_ids text[] NOT NULL,
    source_refs jsonb NOT NULL DEFAULT '[]', context jsonb, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX activity_one_running ON life.activities(actor_id) WHERE state='running';
CREATE TABLE life.commitments (
    id text PRIMARY KEY, actor_id text NOT NULL, revision bigint NOT NULL DEFAULT 1,
    title text NOT NULL, due_at timestamptz, timezone text NOT NULL, state text NOT NULL,
    target_ref text, person_id text, matter_id text, capability_id text, arguments jsonb NOT NULL DEFAULT '{}',
    recurrence text CHECK(recurrence IN ('daily','weekly')), weekdays integer[] NOT NULL DEFAULT '{}',
    grace_seconds integer NOT NULL DEFAULT 3600, missed_policy text NOT NULL DEFAULT 'skip' CHECK(missed_policy IN ('skip','once','reschedule')),
    wake_condition jsonb, scope_ids text[] NOT NULL, source_refs jsonb NOT NULL DEFAULT '[]',
    context jsonb NOT NULL, reason text, action_id text, frozen_action jsonb, last_receipt_id text, last_result_revision bigint NOT NULL DEFAULT -1, last_expression_id text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX commitments_due ON life.commitments(due_at) WHERE state IN ('scheduled','waiting');
CREATE TABLE life.contact_preferences (
    actor_id text NOT NULL, person_id text NOT NULL DEFAULT '', revision bigint NOT NULL DEFAULT 1,
    timezone text NOT NULL DEFAULT 'Asia/Shanghai', quiet_start text NOT NULL DEFAULT '23:00',
    quiet_end text NOT NULL DEFAULT '08:00', cooldown_seconds integer NOT NULL DEFAULT 1800,
    daily_quota integer NOT NULL DEFAULT 3, unanswered_limit integer NOT NULL DEFAULT 3,
    responsiveness double precision NOT NULL DEFAULT 0.5, enabled boolean NOT NULL DEFAULT true,
    availability text NOT NULL DEFAULT 'unknown', available_until timestamptz,
    availability_event_id text, availability_occurred_at timestamptz, availability_source_refs jsonb NOT NULL DEFAULT '[]',
    scope_ids text[] NOT NULL, PRIMARY KEY(actor_id,person_id)
);
CREATE TABLE life.contact_candidates (
    id text PRIMARY KEY, actor_id text NOT NULL, revision bigint NOT NULL DEFAULT 1,
    target_ref text NOT NULL, person_id text, kind text NOT NULL, summary text NOT NULL,
    due_at timestamptz NOT NULL, expires_at timestamptz NOT NULL, state text NOT NULL,
    explicit boolean NOT NULL DEFAULT false, delivery_kind text NOT NULL,
    action_id text, context jsonb NOT NULL, source_refs jsonb NOT NULL DEFAULT '[]',
    payload jsonb NOT NULL DEFAULT '{}', reason text, expression_id text, delivery_generation bigint NOT NULL DEFAULT 1,
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
    next_due_at timestamptz, last_planned_at timestamptz, reason text,
    generation_material text, generation_key text, generation_consumed boolean NOT NULL DEFAULT false,
    decision_fingerprint text, change jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE life.plan_generations (
    generation_key text PRIMARY KEY, actor_id text NOT NULL, day date NOT NULL, material_fingerprint text NOT NULL,
    base_revision bigint NOT NULL, context jsonb NOT NULL, state text NOT NULL DEFAULT 'pending', reason text,
    created_at timestamptz NOT NULL DEFAULT now(), reviewed_at timestamptz
);
CREATE TABLE life.day_plans (
    actor_id text NOT NULL, day date NOT NULL, revision bigint NOT NULL DEFAULT 1, timezone text NOT NULL,
    state text NOT NULL DEFAULT 'open', reason text NOT NULL, context jsonb NOT NULL, scope_ids text[] NOT NULL,
    source_refs jsonb NOT NULL DEFAULT '[]', material_fingerprint text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(actor_id,day)
);
CREATE TABLE life.day_slots (
    id text PRIMARY KEY, actor_id text NOT NULL, day date NOT NULL, revision bigint NOT NULL DEFAULT 1,
    kind text NOT NULL CHECK(kind IN ('activity','free')), state text NOT NULL DEFAULT 'planned',
    starts_at timestamptz NOT NULL, ends_at timestamptz NOT NULL, activity_id text REFERENCES life.activities(id),
    reason text NOT NULL, scope_ids text[] NOT NULL, source_refs jsonb NOT NULL DEFAULT '[]',
    FOREIGN KEY(actor_id,day) REFERENCES life.day_plans(actor_id,day), CHECK(ends_at>starts_at)
);
CREATE INDEX day_slots_due ON life.day_slots(actor_id,starts_at) WHERE kind='activity' AND state='planned';
CREATE TABLE life.outfits (
    id text PRIMARY KEY, actor_id text NOT NULL, revision bigint NOT NULL DEFAULT 1,
    description text NOT NULL, appearance text NOT NULL, activity_kinds text[] NOT NULL DEFAULT '{}',
    reference jsonb, source_refs jsonb NOT NULL DEFAULT '[]', scope_ids text[] NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE life.diaries (
    id text PRIMARY KEY, actor_id text NOT NULL, day date NOT NULL, revision bigint NOT NULL DEFAULT 1,
    state text NOT NULL DEFAULT 'draft', current_revision bigint NOT NULL DEFAULT 0,
    published_revision bigint, generation_id text, generation_cancel_requested boolean NOT NULL DEFAULT false, context jsonb, reason text, material_key text,
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
CREATE TABLE life.diary_days (
    actor_id text NOT NULL, scope_key text NOT NULL, day date NOT NULL, timezone text NOT NULL,
    context jsonb NOT NULL, scope_ids text[] NOT NULL, material_revisions jsonb NOT NULL DEFAULT '{}',
    revision bigint NOT NULL DEFAULT 1, reviewed_revision bigint NOT NULL DEFAULT 0, generation_id text, generation_revision bigint,
    closed_at timestamptz NOT NULL DEFAULT now(), last_reviewed_at timestamptz, last_error text,
    updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(actor_id,scope_key,day)
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
    published_revision bigint, generation_id text, generation_cancel_requested boolean NOT NULL DEFAULT false, generation_context jsonb, generation_recipe jsonb, reason text, review_state text, review_note text,
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
