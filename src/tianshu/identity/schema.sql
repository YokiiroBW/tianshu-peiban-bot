CREATE SCHEMA identity;
CREATE TABLE identity.installation (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    initialized_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE identity.people (
    person_id text PRIMARY KEY,
    display_name text NOT NULL,
    revision bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE identity.principals (
    principal_id text PRIMARY KEY,
    username text UNIQUE NOT NULL,
    display_name text NOT NULL,
    password_hash text,
    person_id text REFERENCES identity.people,
    permissions text[] NOT NULL DEFAULT '{}',
    enabled boolean NOT NULL DEFAULT true,
    access_epoch bigint NOT NULL DEFAULT 1,
    auth_epoch bigint NOT NULL DEFAULT 1,
    revision bigint NOT NULL DEFAULT 1
);
CREATE TABLE identity.person_aliases (
    person_id text NOT NULL REFERENCES identity.people,
    alias text NOT NULL,
    PRIMARY KEY(person_id,alias)
);
CREATE INDEX person_alias_lookup ON identity.person_aliases(lower(alias));
CREATE TABLE identity.sessions (
    token_hash text PRIMARY KEY,
    principal_id text NOT NULL REFERENCES identity.principals,
    csrf_token text NOT NULL,
    access_epoch bigint NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sessions_expiry ON identity.sessions(expires_at);
CREATE TABLE identity.anonymous_sessions (
    token_hash text PRIMARY KEY,
    csrf_token text NOT NULL,
    expires_at timestamptz NOT NULL
);
CREATE TABLE identity.actors (
    actor_id text PRIMARY KEY,
    owner_id text NOT NULL REFERENCES identity.principals,
    enabled boolean NOT NULL DEFAULT true,
    access_epoch bigint NOT NULL DEFAULT 1
);
CREATE TABLE identity.actor_grants (
    actor_id text NOT NULL REFERENCES identity.actors,
    principal_id text NOT NULL REFERENCES identity.principals,
    permissions text[] NOT NULL,
    PRIMARY KEY(actor_id,principal_id)
);
CREATE TABLE identity.scopes (
    scope_id text PRIMARY KEY,
    actor_id text REFERENCES identity.actors,
    person_id text REFERENCES identity.people,
    kind text NOT NULL CHECK(kind IN ('private','group','project','system','public','publication')),
    label text NOT NULL,
    access_epoch bigint NOT NULL DEFAULT 1,
    intake_epoch bigint NOT NULL DEFAULT 1
);
CREATE TABLE identity.scope_grants (
    scope_id text NOT NULL REFERENCES identity.scopes,
    principal_id text NOT NULL REFERENCES identity.principals,
    permissions text[] NOT NULL,
    PRIMARY KEY(scope_id,principal_id)
);
CREATE INDEX scopes_actor_person ON identity.scopes(actor_id,person_id,kind);
CREATE VIEW identity.source_scope_epochs AS SELECT scope_id,intake_epoch FROM identity.scopes;
CREATE TABLE identity.scope_inclusions (
    publication_scope_id text NOT NULL REFERENCES identity.scopes,
    conversation_scope_id text NOT NULL REFERENCES identity.scopes,
    PRIMARY KEY(publication_scope_id,conversation_scope_id)
);
CREATE TABLE identity.conversations (
    conversation_id text PRIMARY KEY,
    actor_id text NOT NULL REFERENCES identity.actors,
    scope_id text NOT NULL REFERENCES identity.scopes,
    person_id text REFERENCES identity.people,
    kind text NOT NULL CHECK(kind IN ('private','group','project','system')),
    label text NOT NULL,
    adapter text NOT NULL DEFAULT 'web',
    target_ref text,
    revision bigint NOT NULL DEFAULT 1,
    UNIQUE NULLS NOT DISTINCT(actor_id,scope_id,kind,adapter,target_ref)
);
CREATE TABLE identity.connections (
    connection_id text PRIMARY KEY,
    principal_id text NOT NULL REFERENCES identity.principals,
    actor_id text NOT NULL REFERENCES identity.actors,
    namespace text NOT NULL,
    self_external_id text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    default_mode text NOT NULL DEFAULT 'reply' CHECK(default_mode IN ('reply','observe','ignore')),
    event_permissions text[] NOT NULL DEFAULT ARRAY['chat','conversation.read','conversation.send','companion.read','life.read','life.manage','life.published.read','memory.read','memory.manage','memory.share'],
    revision bigint NOT NULL DEFAULT 1
);
CREATE TABLE identity.accounts (
    connection_id text NOT NULL REFERENCES identity.connections,
    external_id text NOT NULL,
    person_id text NOT NULL REFERENCES identity.people,
    principal_id text REFERENCES identity.principals,
    revision bigint NOT NULL DEFAULT 1,
    PRIMARY KEY(connection_id,external_id)
);
CREATE TABLE identity.conversation_links (
    connection_id text NOT NULL REFERENCES identity.connections,
    external_id text NOT NULL,
    conversation_id text NOT NULL REFERENCES identity.conversations,
    mode text NOT NULL CHECK(mode IN ('reply','observe','ignore')),
    mention_required boolean NOT NULL DEFAULT false,
    reply_keywords text[] NOT NULL DEFAULT '{}',
    inherited_private_policy boolean NOT NULL DEFAULT false,
    reply_owner text NOT NULL DEFAULT 'tianshu',
    revision bigint NOT NULL DEFAULT 1,
    PRIMARY KEY(connection_id,external_id)
);
CREATE TABLE identity.events (
    connection_id text NOT NULL REFERENCES identity.connections,
    producer text NOT NULL,
    event_id text NOT NULL,
    revision bigint NOT NULL,
    content_hash text NOT NULL,
    conversation_id text NOT NULL,
    speaker_id text NOT NULL,
    disposition text NOT NULL,
    occurred_at timestamptz NOT NULL,
    PRIMARY KEY(connection_id,producer,event_id,revision)
);
CREATE TABLE identity.conversation_members (
    conversation_id text NOT NULL REFERENCES identity.conversations,
    connection_id text NOT NULL REFERENCES identity.connections,
    external_id text NOT NULL,
    person_id text NOT NULL REFERENCES identity.people,
    last_observed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(conversation_id,connection_id,external_id)
);
CREATE INDEX conversation_members_person ON identity.conversation_members(conversation_id,person_id);
CREATE TABLE identity.login_windows (
    username_hash text PRIMARY KEY,
    failures integer NOT NULL DEFAULT 0,
    window_at timestamptz NOT NULL DEFAULT now()
);
