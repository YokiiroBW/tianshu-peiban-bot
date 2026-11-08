-- A persona is one editable document. Old revision rows remain migration evidence;
-- normal reads and saves use only persona_profiles from this point onward.
ALTER TABLE companion.persona_profiles
    ADD COLUMN author_text text NOT NULL DEFAULT '',
    ADD COLUMN tone text NOT NULL DEFAULT '',
    ADD COLUMN style text NOT NULL DEFAULT '',
    ADD COLUMN address text NOT NULL DEFAULT '',
    ADD COLUMN world text NOT NULL DEFAULT '',
    ADD COLUMN archived_at timestamptz;

UPDATE companion.persona_profiles p
SET author_text = r.author_text, tone = r.tone, style = r.style,
    address = r.address, world = r.world
FROM companion.persona_revisions r
WHERE r.persona_id = p.persona_id AND r.revision = p.current_revision;

-- Only untouched, unbound copies explicitly created by the old configuration
-- import are archived. Keep differing draft contents and original IDs intact.
UPDATE companion.persona_profiles draft
SET archived_at = now()
WHERE draft.revision = 1
  AND draft.name LIKE '%（作者草稿档案·未绑定）'
  AND NOT EXISTS (SELECT 1 FROM companion.actors a WHERE a.persona_id = draft.persona_id)
  AND EXISTS (
      SELECT 1 FROM companion.persona_revisions r
      WHERE r.persona_id = draft.persona_id AND r.revision = 1
        AND r.note LIKE '配置迁移；保留原作者内容；旧来源=persona-profile:%')
  AND EXISTS (
      SELECT 1 FROM companion.persona_profiles applied
      JOIN companion.persona_revisions r ON r.persona_id = applied.persona_id AND r.revision = 1
      WHERE applied.name = replace(draft.name, '（作者草稿档案·未绑定）', '（当前已应用快照）')
        AND r.note LIKE '配置迁移；保留原作者内容；旧来源=actor:%');

UPDATE companion.persona_profiles p
SET name = regexp_replace(p.name, '（当前已应用快照）$', '')
WHERE p.name LIKE '%（当前已应用快照）'
  AND EXISTS (SELECT 1 FROM companion.persona_revisions r
      WHERE r.persona_id = p.persona_id AND r.revision = 1
        AND r.note LIKE '配置迁移；保留原作者内容；旧来源=actor:%');

UPDATE companion.persona_profiles p
SET name = '默认人格'
WHERE p.name = '默认 household 人格（已发布保留）'
  AND EXISTS (SELECT 1 FROM companion.persona_revisions r
      WHERE r.persona_id = p.persona_id AND r.revision = 1
        AND r.note LIKE '配置迁移；保留原作者内容；旧来源=actor:household;%');

ALTER TABLE companion.actors DROP CONSTRAINT actors_persona_id_persona_revision_fkey;
UPDATE companion.actors a
SET persona_revision = p.revision, revision = a.revision + 1, updated_at = now()
FROM companion.persona_profiles p
WHERE a.persona_id = p.persona_id AND a.persona_revision IS DISTINCT FROM p.revision;
