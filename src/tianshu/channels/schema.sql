CREATE SCHEMA IF NOT EXISTS channels;
CREATE TABLE IF NOT EXISTS channels.connections(id text PRIMARY KEY,revision bigint NOT NULL,body jsonb NOT NULL,secret_ref text,updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS channels.calls(id text PRIMARY KEY,action_id text NOT NULL,attempt_id text NOT NULL,connection_id text NOT NULL,expression_id text,api_name text NOT NULL,control boolean NOT NULL DEFAULT false,expected_count integer NOT NULL DEFAULT 1,state text NOT NULL DEFAULT 'prepared',payload bytea,response bytea,result jsonb,owner_id text,submitted_at timestamptz,completed_at timestamptz,created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS channel_action_calls ON channels.calls(action_id,created_at,id);
CREATE INDEX IF NOT EXISTS channel_expression_calls ON channels.calls(expression_id,created_at,id);
CREATE TABLE IF NOT EXISTS channels.expressions(expression_id text PRIMARY KEY,action_id text UNIQUE NOT NULL,principal_id text NOT NULL,conversation_id text NOT NULL,semantics text NOT NULL);
CREATE TABLE IF NOT EXISTS channels.events(id text PRIMARY KEY,connection_id text NOT NULL,producer text NOT NULL,event_id text NOT NULL,revision bigint NOT NULL,payload bytea,state text NOT NULL DEFAULT 'accepted',outcome jsonb,created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(connection_id,producer,event_id,revision));
CREATE TABLE IF NOT EXISTS channels.owners(id text PRIMARY KEY,owner_id text NOT NULL,epoch bigint NOT NULL,expires_at timestamptz NOT NULL);
CREATE TABLE IF NOT EXISTS channels.observations(connection_id text NOT NULL,kind text NOT NULL,name text NOT NULL,body jsonb NOT NULL,observed_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(connection_id,kind,name));
CREATE TABLE IF NOT EXISTS channels.source_dependencies(call_id text NOT NULL,source_id text NOT NULL,source_revision bigint NOT NULL,refs jsonb NOT NULL,PRIMARY KEY(call_id,source_id,source_revision));
CREATE INDEX IF NOT EXISTS channel_source_calls ON channels.source_dependencies(source_id,source_revision,call_id);
CREATE TABLE IF NOT EXISTS channels.telegram_offsets(connection_id text PRIMARY KEY,update_id bigint NOT NULL);

CREATE TABLE IF NOT EXISTS channels.event_sources(event_key text NOT NULL,source_id text NOT NULL,source_revision bigint NOT NULL,refs jsonb NOT NULL,PRIMARY KEY(event_key,source_id,source_revision));
CREATE INDEX IF NOT EXISTS channel_event_sources ON channels.event_sources(source_id,source_revision,event_key);
