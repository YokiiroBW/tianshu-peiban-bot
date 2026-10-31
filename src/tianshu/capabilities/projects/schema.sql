CREATE SCHEMA IF NOT EXISTS capability_projects;
CREATE TABLE IF NOT EXISTS capability_projects.projects (
 project_id text PRIMARY KEY, actor_id text NOT NULL, scope_ids text[] NOT NULL,
 owner_id text NOT NULL, conversation_id text NOT NULL, label text NOT NULL, root text NOT NULL,
 directories jsonb NOT NULL DEFAULT '[]', allowed_urls jsonb NOT NULL DEFAULT '[]',
 worktrees jsonb NOT NULL DEFAULT '[]', enabled boolean NOT NULL DEFAULT true,
 revision bigint NOT NULL, content_revision bigint NOT NULL DEFAULT 0, last_action_id text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS projects_actor_page ON capability_projects.projects(actor_id,project_id);
CREATE TABLE IF NOT EXISTS capability_projects.documents (
 document_id text PRIMARY KEY, project_id text NOT NULL REFERENCES capability_projects.projects(project_id),
 locator text NOT NULL, kind text NOT NULL, current_version bigint NOT NULL,
 state text NOT NULL DEFAULT 'active', last_action_id text NOT NULL,
 UNIQUE(project_id,kind,locator)
);
CREATE INDEX IF NOT EXISTS documents_project_page ON capability_projects.documents(project_id,document_id);
CREATE TABLE IF NOT EXISTS capability_projects.document_versions (
 document_id text NOT NULL REFERENCES capability_projects.documents(document_id), version bigint NOT NULL,
 sha256 text NOT NULL, mime_type text NOT NULL, source_refs jsonb NOT NULL,
 original_ref jsonb NOT NULL, text_ref jsonb, metadata jsonb NOT NULL, units jsonb NOT NULL,
 state text NOT NULL DEFAULT 'active', imported_at timestamptz NOT NULL DEFAULT now(),
 action_id text NOT NULL, PRIMARY KEY(document_id,version)
);
CREATE TABLE IF NOT EXISTS capability_projects.directory_plans (
 plan_id text PRIMARY KEY, project_id text NOT NULL REFERENCES capability_projects.projects(project_id),
 principal_id text NOT NULL, directory text NOT NULL, project_revision bigint NOT NULL,
 manifest jsonb NOT NULL, sha256 text NOT NULL, coverage jsonb NOT NULL, result jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS capability_projects.imports (
 import_id text PRIMARY KEY, project_id text NOT NULL REFERENCES capability_projects.projects(project_id),
 action_id text NOT NULL UNIQUE, plan_id text, state text NOT NULL,
 items jsonb NOT NULL DEFAULT '[]', result jsonb NOT NULL DEFAULT '{}', updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS capability_projects.notes (
 note_id text PRIMARY KEY, project_id text NOT NULL REFERENCES capability_projects.projects(project_id),
 current_version bigint NOT NULL, state text NOT NULL, last_action_id text NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_project_page ON capability_projects.notes(project_id,note_id);
CREATE TABLE IF NOT EXISTS capability_projects.note_versions (
 note_id text NOT NULL REFERENCES capability_projects.notes(note_id), version bigint NOT NULL,
 payload jsonb NOT NULL, citations jsonb NOT NULL, source_refs jsonb NOT NULL, sha256 text NOT NULL,
 state text NOT NULL, action_id text NOT NULL, observed_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(note_id,version)
);
CREATE TABLE IF NOT EXISTS capability_projects.lessons (
 lesson_id text PRIMARY KEY, project_id text NOT NULL REFERENCES capability_projects.projects(project_id),
 current_version bigint NOT NULL, state text NOT NULL, last_action_id text NOT NULL
);
CREATE INDEX IF NOT EXISTS lessons_project_page ON capability_projects.lessons(project_id,lesson_id);
CREATE TABLE IF NOT EXISTS capability_projects.lesson_versions (
 lesson_id text NOT NULL REFERENCES capability_projects.lessons(lesson_id), version bigint NOT NULL,
 payload jsonb NOT NULL, evidence jsonb NOT NULL, source_refs jsonb NOT NULL, sha256 text NOT NULL,
 state text NOT NULL, action_id text NOT NULL, observed_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(lesson_id,version)
);
CREATE TABLE IF NOT EXISTS capability_projects.experiences (
 experience_id text PRIMARY KEY, owner_id text NOT NULL, actor_id text NOT NULL,
 scope_ids text[] NOT NULL, revision bigint NOT NULL, payload jsonb NOT NULL,
 evidence jsonb NOT NULL, source_refs jsonb NOT NULL, disclosure_ref jsonb,
 state text NOT NULL, last_action_id text NOT NULL, observed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS experiences_actor_page ON capability_projects.experiences(actor_id,experience_id);
CREATE TABLE IF NOT EXISTS capability_projects.declared_states (
 project_id text NOT NULL REFERENCES capability_projects.projects(project_id), revision bigint NOT NULL,
 payload jsonb NOT NULL, source_refs jsonb NOT NULL, evidence jsonb NOT NULL, state text NOT NULL DEFAULT 'active',
 action_id text NOT NULL, observed_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(project_id,revision)
);
CREATE TABLE IF NOT EXISTS capability_projects.continuations (
 pack_id text PRIMARY KEY, project_id text NOT NULL REFERENCES capability_projects.projects(project_id),
 principal_id text NOT NULL, actor_id text NOT NULL, scope_ids text[] NOT NULL,
 project_revision bigint NOT NULL, worktree_id text NOT NULL, facts jsonb NOT NULL,
 payload jsonb NOT NULL, source_refs jsonb NOT NULL, sha256 text NOT NULL,
 observed_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS capability_projects.experience_versions (
 experience_id text NOT NULL REFERENCES capability_projects.experiences(experience_id), revision bigint NOT NULL,
 payload jsonb NOT NULL, evidence jsonb NOT NULL, source_refs jsonb NOT NULL, disclosure_ref jsonb,
 state text NOT NULL, action_id text NOT NULL, observed_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(experience_id,revision)
);

CREATE TABLE IF NOT EXISTS capability_projects.registry_versions (
 project_id text NOT NULL REFERENCES capability_projects.projects(project_id),revision bigint NOT NULL,
 payload jsonb NOT NULL, action_id text NOT NULL, PRIMARY KEY(project_id,revision)
);
CREATE INDEX IF NOT EXISTS project_action_registry ON capability_projects.registry_versions(action_id);
CREATE INDEX IF NOT EXISTS project_action_documents ON capability_projects.document_versions(action_id);
CREATE INDEX IF NOT EXISTS project_action_notes ON capability_projects.note_versions(action_id);
CREATE INDEX IF NOT EXISTS project_action_lessons ON capability_projects.lesson_versions(action_id);
CREATE INDEX IF NOT EXISTS project_action_experience ON capability_projects.experience_versions(action_id);
