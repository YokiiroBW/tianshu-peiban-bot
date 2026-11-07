CREATE TABLE IF NOT EXISTS identity.role_administrators (
    actor_id text PRIMARY KEY REFERENCES identity.actors,
    person_id text NOT NULL REFERENCES identity.people,
    principal_id text NOT NULL REFERENCES identity.principals,
    enabled boolean NOT NULL DEFAULT true,
    revision bigint NOT NULL DEFAULT 1
);
