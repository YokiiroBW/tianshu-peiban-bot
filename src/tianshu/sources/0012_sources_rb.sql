ALTER TABLE sources.archive_records
    ADD COLUMN explicit_authorized boolean NOT NULL DEFAULT false;
-- Earlier versions created archive records only after an explicit history request.
UPDATE sources.archive_records SET explicit_authorized=true;

CREATE TABLE sources.media_supplements (
    source_id text NOT NULL,
    revision bigint NOT NULL,
    request_digest text NOT NULL,
    PRIMARY KEY(source_id, revision),
    FOREIGN KEY(source_id, revision) REFERENCES sources.versions(source_id, revision)
);

CREATE TABLE sources.evidence_sets (
    evidence_id text PRIMARY KEY,
    manifest jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE sources.evidence_members (
    evidence_id text NOT NULL REFERENCES sources.evidence_sets,
    role text NOT NULL CHECK(role IN ('required','alternative','generation')),
    group_index integer NOT NULL,
    source_id text NOT NULL,
    revision bigint NOT NULL,
    locator jsonb NOT NULL,
    ref_key text NOT NULL,
    PRIMARY KEY(evidence_id, role, group_index, ref_key),
    FOREIGN KEY(source_id, revision) REFERENCES sources.versions(source_id, revision)
);
CREATE INDEX evidence_members_reverse ON sources.evidence_members(source_id, revision);
