-- Independent author projects use the existing principal and system scope.
-- Character projects retain their existing actor and conversation scene.
ALTER TABLE capability_projects.projects ALTER COLUMN actor_id DROP NOT NULL;
ALTER TABLE capability_projects.projects ALTER COLUMN conversation_id DROP NOT NULL;
ALTER TABLE capability_projects.experiences ALTER COLUMN actor_id DROP NOT NULL;
ALTER TABLE capability_projects.continuations ALTER COLUMN actor_id DROP NOT NULL;
