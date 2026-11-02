"""Real 0020-to-0021 migration cases; written for the centralized repair rerun."""

from importlib.resources import files

import pytest
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from tianshu.runtime.migrations import Migration, migrate


def baseline():
    return (
        Migration("0010_identity", files("tianshu.identity").joinpath("schema.sql").read_text()),
        Migration("0011_sources", "\n".join(files("tianshu.sources").joinpath(part).read_text()
            for part in ("schema.sql", "functions.sql"))),
        Migration("0020_memory", files("tianshu.memory").joinpath("schema.sql").read_text()),
    )


def upgrade():
    return (Migration("0021_memory_consolidation",
        files("tianshu.memory").joinpath("0021_memory_consolidation.sql").read_text()),)


async def source(conn, identifier, scope, *, revoked=False):
    state = "unsupported" if revoked else "active"
    await conn.execute("INSERT INTO sources.originals(source_id,source_key,owner_id,head_revision,state) VALUES(%s,%s,'synthetic_owner',1,%s)",
                       (identifier, "synthetic:" + identifier, state))
    await conn.execute("INSERT INTO sources.versions(source_id,revision,intake_epoch,scope_ids,scope_epochs,audience,"
        "origin_kind,evidence_unit,content,locator,coverage,occurred_at,state) "
        "VALUES(%s,1,1,%s,%s,'{}','human',%s,'synthetic migration evidence','{}','{}',now(),%s)",
        (identifier, [scope], Jsonb({scope: 1}), identifier, state))
    return {"source_id": identifier, "revision": 1, "locator": {}}


async def legacy_pair(conn, identifier, claim_refs, matter_refs, *, cache=True, field=True):
    claim_id, matter_id = "claim_" + identifier, "matter_" + identifier
    claim_groups = [[ref] for ref in claim_refs] or [[]]
    await conn.execute("INSERT INTO memory.claims(claim_id,current_revision,actor_id,principal_id) "
        "VALUES(%s,1,'synthetic_actor','synthetic_owner')", (claim_id,))
    await conn.execute("INSERT INTO memory.claim_revisions(claim_id,revision,subject_id,predicate,value,assertion,polarity,"
        "conditions,observed_at,state,source_refs,support_groups,scope_groups,scope_ids,matter_id,evidence_units,content,lexical,contradictions) "
        "VALUES(%s,1,'synthetic_person','schedule','\"exam\"','reported',true,'{}',now(),'active',%s,%s,%s,"
        "ARRAY['synthetic_claim_scope'],%s,ARRAY['synthetic_unit'],'synthetic legacy claim','synthetic legacy claim',ARRAY['old_unequal_value'])",
        (claim_id, Jsonb(claim_refs), Jsonb(claim_groups), Jsonb([["synthetic_claim_scope"] for _ in claim_groups]),
         matter_id if field else None))
    await conn.execute("INSERT INTO memory.matters(matter_id,actor_id,principal_id,title,state,summary,source_refs,scope_ids,observed_at) "
        "VALUES(%s,'synthetic_actor','synthetic_owner','synthetic legacy matter','planned','synthetic summary',%s,"
        "ARRAY['synthetic_matter_scope'],now())", (matter_id, Jsonb(matter_refs)))
    if cache:
        await conn.execute("INSERT INTO memory.matter_claims(matter_id,claim_id) VALUES(%s,%s)", (matter_id, claim_id))
    return claim_id, matter_id


async def test_0021_empty_install_and_idempotent_replay_keep_integrated_schema(database):
    await migrate(database, baseline())
    await migrate(database, upgrade())
    await migrate(database, upgrade())
    async with database.transaction(read_only=True) as conn:
        applied = await (await conn.execute("SELECT count(*) AS n FROM runtime.schema_migrations WHERE name='0021_memory_consolidation'")).fetchone()
        links = await (await conn.execute("SELECT count(*) AS n FROM memory.semantic_links")).fetchone()
        columns = await (await conn.execute("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='memory' "
            "AND ((table_name='consolidation_state' AND column_name='budget_failure') "
            "OR (table_name='episodes' AND column_name IN ('speaker_id','consolidation_partition_id')))")).fetchall()
    assert applied["n"] == 1 and links["n"] == 0
    assert {(row["table_name"], row["column_name"]) for row in columns} == {
        ("consolidation_state", "budget_failure"), ("episodes", "speaker_id"), ("episodes", "consolidation_partition_id")}


async def test_0021_preserves_proven_old_associations_and_rebuilds_only_usable_links(database):
    await migrate(database, baseline())
    async with database.transaction() as conn:
        for identifier in ("synthetic_claim_scope", "synthetic_matter_scope"):
            await conn.execute("INSERT INTO identity.scopes(scope_id,kind,label) VALUES(%s,'public','synthetic migration scope')", (identifier,))
        first = await source(conn, "synthetic_claim_first", "synthetic_claim_scope")
        second = await source(conn, "synthetic_claim_second", "synthetic_claim_scope")
        revoked = await source(conn, "synthetic_claim_revoked", "synthetic_claim_scope", revoked=True)
        matter = await source(conn, "synthetic_matter_original", "synthetic_matter_scope")
        expected = {
            await legacy_pair(conn, "both", [first, second, revoked], [matter]),
            await legacy_pair(conn, "cache_only", [first], [matter], field=False),
            await legacy_pair(conn, "field_only", [second], [matter], cache=False),
        }
        await legacy_pair(conn, "revoked", [revoked], [matter])
        await legacy_pair(conn, "claim_unproven", [], [matter])
        await legacy_pair(conn, "matter_unproven", [first], [])
    await migrate(database, upgrade())
    async with database.transaction(read_only=True) as conn:
        links = await (await conn.execute("SELECT * FROM memory.semantic_links ORDER BY from_id")).fetchall()
        cache = await (await conn.execute("SELECT claim_id,matter_id FROM memory.matter_claims")).fetchall()
        history = await (await conn.execute("SELECT matter_id,revision,recorded_at,change_kind FROM memory.matter_revisions")).fetchall()
        claims = await (await conn.execute("SELECT claim_id,recorded_at,contradictions FROM memory.claim_revisions")).fetchall()
        by_id = {row["from_id"]: row for row in links}
        assert all(row["relation_kind"] == "about_matter" and row["recorded_at"] is None for row in links)
        assert {(row["from_id"], row["to_id"]) for row in links} == expected
        assert {(row["claim_id"], row["matter_id"]) for row in cache} == expected
        combined = by_id["claim_both"]
        assert len(combined["support_groups"]) == 2
        assert {frozenset(ref["source_id"] for ref in group) for group in combined["support_groups"]} == {
            frozenset((first["source_id"], matter["source_id"])), frozenset((second["source_id"], matter["source_id"]))}
        assert all(ref in (first, second, matter) for group in combined["support_groups"] for ref in group)
        assert combined["scope_groups"] == [["synthetic_claim_scope", "synthetic_matter_scope"]] * 2
        checked = await (await conn.execute("SELECT sources.check_groups(%s,%s::text[]) AS allowed",
            (Jsonb(combined["support_groups"]), ["synthetic_claim_scope", "synthetic_matter_scope"]))).fetchone()
    assert checked["allowed"] == [True, True]
    assert len(history) == 6 and all(row["revision"] == 1 and row["recorded_at"] is None and row["change_kind"] == "legacy" for row in history)
    assert all(row["recorded_at"] is None and row["contradictions"] == ["old_unequal_value"] for row in claims)


async def test_0022_keeps_original_request_and_allows_one_new_active_batch_beside_unknown(database):
    await migrate(database, baseline()+upgrade())
    original={"attempt_id":"original-unknown-attempt","budget":{"deadline_at":"2026-11-02T03:40:00Z"}}
    async with database.transaction() as conn:
        await conn.execute("INSERT INTO memory.model_steps(step_id,request,state,error_code) "
            "VALUES('original-step',%s,'unknown','timeout_unknown')",(Jsonb(original),))
        await conn.execute("INSERT INTO memory.consolidation_state(partition_id,actor_id,principal_id,scope_key,authority_bound) "
            "VALUES('partition','actor','principal','scope','{}')")
        await conn.execute("INSERT INTO memory.consolidation_batches(batch_id,partition_id,schema_version,status,manifest,context_fingerprint,step_ids) "
            "VALUES('original-batch','partition','memory.consolidation.v1','waiting_model','{}','fixed',ARRAY['original-step'])")
    runtime=(Migration("0022_memory_runtime",files("tianshu.memory").joinpath("0022_memory_runtime.sql").read_text()),)
    await migrate(database,runtime)
    await migrate(database,runtime)
    async with database.transaction() as conn:
        step=await (await conn.execute("SELECT * FROM memory.model_steps WHERE step_id='original-step'")).fetchone()
        assert step["request"] == original and step["request_timeout_seconds"] == 120
        assert step["state"] == "unknown" and step["error_code"] == "timeout_unknown"
        await conn.execute("UPDATE memory.consolidation_batches SET status='quarantined' WHERE batch_id='original-batch'")
        await conn.execute("INSERT INTO memory.consolidation_batches(batch_id,partition_id,schema_version,status,manifest,context_fingerprint) "
            "VALUES('following-batch','partition','memory.consolidation.v1','prepared','{}','new')")
    with pytest.raises(UniqueViolation):
        async with database.transaction() as conn:
            await conn.execute("INSERT INTO memory.consolidation_batches(batch_id,partition_id,schema_version,status,manifest,context_fingerprint) "
                "VALUES('duplicate-active','partition','memory.consolidation.v1','ready','{}','new')")
