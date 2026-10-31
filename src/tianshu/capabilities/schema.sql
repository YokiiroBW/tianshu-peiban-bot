CREATE SCHEMA IF NOT EXISTS capability_media;
CREATE SCHEMA IF NOT EXISTS capability_plugins;
CREATE SCHEMA IF NOT EXISTS capability_infrastructure;
CREATE SCHEMA IF NOT EXISTS capability_connections;
SET search_path TO capability_media,public;

                CREATE TABLE IF NOT EXISTS documents(kind TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,body TEXT NOT NULL,PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS members(subscription_id TEXT NOT NULL,bvid TEXT NOT NULL,PRIMARY KEY(subscription_id,bvid));
                CREATE TABLE IF NOT EXISTS member_parts(subscription_id TEXT NOT NULL,bvid TEXT NOT NULL,cid TEXT NOT NULL,PRIMARY KEY(subscription_id,bvid,cid));
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,dedupe TEXT UNIQUE NOT NULL,state TEXT NOT NULL,created DOUBLE PRECISION NOT NULL,updated DOUBLE PRECISION NOT NULL,generation INTEGER NOT NULL DEFAULT 0,owner TEXT,lease DOUBLE PRECISION NOT NULL DEFAULT 0,body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(state,lease,created);
                CREATE INDEX IF NOT EXISTS jobs_order ON jobs(CAST(created AS INTEGER) DESC,id DESC);
                CREATE INDEX IF NOT EXISTS jobs_audio_recording ON jobs((body::jsonb #>> '{recording_id}'),(body::jsonb #>> '{destination_directory}'),created,id) WHERE (body::jsonb #>> '{media_kind}')='audio';
                CREATE TABLE IF NOT EXISTS events(job_id TEXT NOT NULL,state TEXT NOT NULL,code TEXT NOT NULL,at DOUBLE PRECISION NOT NULL);
                CREATE TABLE IF NOT EXISTS episodes(bvid TEXT NOT NULL,cid TEXT NOT NULL,number INTEGER NOT NULL,PRIMARY KEY(bvid,cid),UNIQUE(bvid,number));
                CREATE TABLE IF NOT EXISTS scan_leases(id TEXT PRIMARY KEY,owner TEXT NOT NULL,generation INTEGER NOT NULL,expires DOUBLE PRECISION NOT NULL);
                CREATE TABLE IF NOT EXISTS download_sources(bvid TEXT NOT NULL,cid TEXT NOT NULL,quality TEXT NOT NULL,state TEXT NOT NULL,owner TEXT,generation INTEGER NOT NULL DEFAULT 0,lease DOUBLE PRECISION NOT NULL DEFAULT 0,body TEXT,PRIMARY KEY(bvid,cid,quality));

            ;
SET search_path TO capability_plugins,public;

SET search_path TO capability_plugins,public;
CREATE TABLE IF NOT EXISTS plugin_state (
                instance_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                revision INTEGER NOT NULL, PRIMARY KEY(instance_id,key));
SET search_path TO capability_plugins,public;
CREATE TABLE IF NOT EXISTS plugin_secrets (
                instance_id TEXT NOT NULL, name TEXT NOT NULL, value BYTEA NOT NULL,
                PRIMARY KEY(instance_id,name));
SET search_path TO capability_plugins,public;
CREATE TABLE IF NOT EXISTS plugin_logs (
                id TEXT PRIMARY KEY, instance_id TEXT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL, document TEXT NOT NULL);
SET search_path TO capability_infrastructure,public;
CREATE TABLE IF NOT EXISTS control_operations (
                operation_id TEXT PRIMARY KEY, client_id TEXT UNIQUE NOT NULL,
                node_id TEXT NOT NULL, revision INTEGER NOT NULL, kind TEXT NOT NULL,
                stack_id TEXT, container_id TEXT, lock_key TEXT NOT NULL,
                semantics TEXT NOT NULL, payload TEXT NOT NULL, document TEXT NOT NULL,
                state TEXT NOT NULL, settled INTEGER NOT NULL DEFAULT 0,
                created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL,
                owner TEXT, lease_until DOUBLE PRECISION NOT NULL DEFAULT 0,
                sent INTEGER NOT NULL DEFAULT 0, next_attempt DOUBLE PRECISION NOT NULL DEFAULT 0);
SET search_path TO capability_infrastructure,public;
CREATE TABLE IF NOT EXISTS control_plan_uses (
                node_id TEXT NOT NULL, plan_id TEXT NOT NULL, operation_id TEXT NOT NULL,
                PRIMARY KEY(node_id,plan_id));
SET search_path TO capability_infrastructure,public;
CREATE TABLE IF NOT EXISTS control_plans (
                node_id TEXT NOT NULL, plan_id TEXT NOT NULL,
                operation_id TEXT UNIQUE NOT NULL, document TEXT NOT NULL,
                PRIMARY KEY(node_id,plan_id));
SET search_path TO capability_infrastructure,public;
CREATE TABLE IF NOT EXISTS observations (
                node_id TEXT NOT NULL, operation TEXT NOT NULL, revision INTEGER NOT NULL,
                latest TEXT NOT NULL, sample TEXT, PRIMARY KEY(node_id,operation));
SET search_path TO capability_plugins,public;
CREATE TABLE IF NOT EXISTS plugin_packages(id TEXT PRIMARY KEY,document TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plugin_instances(id TEXT PRIMARY KEY,document TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plugin_operations(id TEXT PRIMARY KEY,document TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plugin_notifications(id TEXT PRIMARY KEY,document TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plugin_schedules(id TEXT PRIMARY KEY,document TEXT NOT NULL);
SET search_path TO public;

CREATE SCHEMA IF NOT EXISTS capabilities;
CREATE TABLE IF NOT EXISTS capabilities.actions(
 id text PRIMARY KEY,intent text UNIQUE NOT NULL,semantics text NOT NULL,principal_id text NOT NULL,
 kind text NOT NULL,instance_id text,created_at double precision NOT NULL,document text NOT NULL,
 action jsonb NOT NULL,attempt jsonb NOT NULL,receipt jsonb NOT NULL,result_revision bigint NOT NULL DEFAULT 0,result_observed_at timestamptz);
CREATE INDEX IF NOT EXISTS capability_actor_actions ON capabilities.actions((action->>'actor_id'),created_at,id);
CREATE TABLE IF NOT EXISTS capabilities.attempts(id text PRIMARY KEY,action_id text NOT NULL REFERENCES capabilities.actions(id),attempt jsonb NOT NULL,receipt jsonb NOT NULL);

CREATE TABLE IF NOT EXISTS capabilities.configuration(kind text NOT NULL,id text NOT NULL,revision bigint NOT NULL,body jsonb NOT NULL,secret bytea,PRIMARY KEY(kind,id));
CREATE TABLE IF NOT EXISTS capabilities.artifacts(id text PRIMARY KEY,media_id text NOT NULL,principal_id text NOT NULL,actor_id text,scopes text[] NOT NULL,audience text[] NOT NULL,body jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS artifacts_media_scope ON capabilities.artifacts(media_id,created_at DESC);

CREATE TABLE IF NOT EXISTS capabilities.oauth_states(state text PRIMARY KEY,principal_id text NOT NULL,connection_id text NOT NULL,expires_at timestamptz NOT NULL,body jsonb NOT NULL);

CREATE TABLE IF NOT EXISTS capabilities.speech_cache(key text PRIMARY KEY,result jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS capabilities.image_work(action_id text PRIMARY KEY REFERENCES capabilities.actions(id),body jsonb NOT NULL,submitted boolean NOT NULL DEFAULT false,provider_handle text);

CREATE TABLE IF NOT EXISTS capabilities.consumptions(id text PRIMARY KEY,action_id text NOT NULL,source_id text NOT NULL,source_revision bigint NOT NULL,body jsonb NOT NULL);

CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS capabilities.reaction_embedding_profiles(id text PRIMARY KEY,dimensions integer NOT NULL,model text NOT NULL,active boolean NOT NULL DEFAULT true);
CREATE TABLE IF NOT EXISTS capabilities.reaction_assets(id text PRIMARY KEY,pack_id text NOT NULL,scopes text[] NOT NULL,actors text[] NOT NULL,revision bigint NOT NULL,body jsonb NOT NULL,search_text text NOT NULL,embedding vector,profile_id text REFERENCES capabilities.reaction_embedding_profiles(id));
CREATE INDEX IF NOT EXISTS reaction_lexical ON capabilities.reaction_assets USING gin(to_tsvector('simple',search_text));

CREATE TABLE IF NOT EXISTS capabilities.plugin_consumptions(id text PRIMARY KEY,instance_id text NOT NULL,event_id text NOT NULL,context jsonb NOT NULL,event jsonb NOT NULL);

CREATE TABLE IF NOT EXISTS capability_connections.connections(kind text PRIMARY KEY,revision bigint NOT NULL,body text NOT NULL);

CREATE TABLE IF NOT EXISTS capabilities.device_readings(connection_id text NOT NULL,entity_id text NOT NULL,revision bigint NOT NULL,body jsonb NOT NULL,PRIMARY KEY(connection_id,entity_id));

CREATE TABLE IF NOT EXISTS capabilities.command_inputs(id text PRIMARY KEY,principal_id text NOT NULL,fingerprint text NOT NULL,body bytea NOT NULL);

CREATE TABLE IF NOT EXISTS capabilities.maintenance(name text PRIMARY KEY,owner text NOT NULL,lease_until timestamptz NOT NULL);

CREATE TABLE IF NOT EXISTS capabilities.action_controls(id text PRIMARY KEY,action_id text NOT NULL REFERENCES capabilities.actions(id),attempt_id text NOT NULL);

CREATE TABLE IF NOT EXISTS capabilities.source_dependencies(owner_kind text NOT NULL,owner_id text NOT NULL,source_id text NOT NULL,source_revision bigint NOT NULL,refs jsonb NOT NULL,PRIMARY KEY(owner_kind,owner_id,source_id,source_revision));
CREATE INDEX IF NOT EXISTS capability_source_dependants ON capabilities.source_dependencies(source_id,owner_kind,owner_id);

CREATE INDEX IF NOT EXISTS reaction_original_media ON capabilities.reaction_assets((body->'media'->>'media_id'));
CREATE INDEX IF NOT EXISTS capability_consumption_source ON capabilities.consumptions(source_id);

CREATE INDEX IF NOT EXISTS capability_source_revision_dependants ON capabilities.source_dependencies(source_id,source_revision,owner_kind,owner_id);

CREATE TABLE IF NOT EXISTS capabilities.execution_owners(id text PRIMARY KEY,role text NOT NULL,expires_at timestamptz NOT NULL,stopped_at timestamptz);
ALTER TABLE capabilities.actions ADD COLUMN IF NOT EXISTS execution_owner text;
CREATE INDEX IF NOT EXISTS capability_running_owner ON capabilities.actions(execution_owner) WHERE receipt->>'execution'='running';

CREATE TABLE IF NOT EXISTS capabilities.oauth_refresh_leases(connection_id text PRIMARY KEY,fingerprint text NOT NULL,owner text NOT NULL,state text NOT NULL,expires_at timestamptz NOT NULL);
