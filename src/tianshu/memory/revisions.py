"""One transactional writer for manual commands and frozen consolidation proposals."""

from datetime import UTC, datetime
import json
import re

from psycopg.types.json import Jsonb

from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import ServiceClass
from . import codec
from .domain_utils import (CLAIM_PREDICATE_PATTERN, lexical, optional_time, parse_time,
                           semantic_fingerprint, unique_refs)
from .semantic_links import SemanticLinks


CLAIM_FIELDS = ("speaker_id", "subject_id", "predicate", "value", "assertion", "polarity",
                "conditions", "valid_from", "valid_until", "observed_at", "memory_kind")
def stable_id(actor_id, batch_id, alias, kind):
    return kind + "_" + body_hash({"actor": actor_id, "batch": batch_id, "local": alias, "kind": kind})[:48]


def same_scope(previous, support):
    old = {tuple(sorted(group)) for group in previous.get("scope_groups", ())}
    new = {tuple(sorted(group)) for group in support["scope_groups"]}
    return old == new


class RevisionService:
    def __init__(self, claims):
        self.claims, self.memory = claims, claims.memory
        self.sources, self.jobs = claims.sources, claims.jobs
        self.links = SemanticLinks(self.memory)

    async def support_on(self, conn, context, groups, derivation_refs=()):
        groups = tuple(unique_refs(codec.refs(group)) for group in groups)
        if not groups or any(not group for group in groups) or len(groups) > 64:
            raise AppError("memory_support_required")
        derivation_refs = unique_refs(codec.refs(derivation_refs))
        all_refs = unique_refs((*[ref for group in groups for ref in group], *derivation_refs))
        if len(all_refs) > 2048:
            raise AppError("memory_support_budget_exceeded")
        descriptions = await self.sources.describe_for_write_on(conn, context, all_refs)
        by_key = {(row["source_id"], row["revision"]): row for row in descriptions}
        scope_groups = [sorted({scope for ref in (*group, *derivation_refs)
                               for scope in by_key[(ref.source_id, ref.revision)]["scope_ids"]})
                        for group in groups]
        return {"groups": groups, "derivation_refs": derivation_refs, "source_refs": all_refs,
                "scope_groups": scope_groups, "scope_ids": sorted({scope for group in scope_groups for scope in group}),
                "descriptions": tuple(descriptions)}

    async def _claim_head_on(self, conn, context, claim_id, expected_revision):
        head = await (await conn.execute(
            "SELECT c.*,r.* FROM memory.claims c LEFT JOIN memory.claim_revisions r "
            "ON r.claim_id=c.claim_id AND r.revision=c.current_revision WHERE c.claim_id=%s FOR UPDATE OF c",
            (claim_id,),
        )).fetchone()
        if head and head["actor_id"] != context.actor_id:
            raise AppError("claim_forbidden", status=403)
        actual = head["current_revision"] if head else 0
        if type(expected_revision) is not int or expected_revision != actual:
            raise AppError("revision_conflict", status=409)
        return head

    async def _claim_values_on(self, conn, context, body, support, *, verify_assertion=True, previous=None):
        subject_id = body.get("subject_id") or context.person_id
        if previous and previous["subject_id"] != subject_id:
            raise AppError("claim_subject_immutable", status=409)
        if subject_id not in await self.claims.visible_subjects(context, conn, (subject_id,)):
            # A readable published target can name someone outside the current
            # conversation's directory. Only that actual target's immutable
            # subject is authorized here, never a model-provided subject map.
            selected = (await self.claims.selected_groups(conn, context, (previous,)))[0] if (
                previous and previous["state"] == "active") else None
            if selected is None:
                raise AppError("claim_subject_forbidden", status=403)
        predicate = body.get("predicate")
        if not isinstance(predicate, str) or not re.fullmatch(CLAIM_PREDICATE_PATTERN, predicate):
            raise AppError("invalid_claim_predicate")
        assertion, speaker_id = body.get("assertion", "reported"), body.get("speaker_id", context.person_id)
        if assertion not in {"self_confirmed", "reported", "quoted", "inferred", "observed"}:
            raise AppError("invalid_claim_assertion")
        proof_keys = {(ref.source_id, ref.revision) for group in support["groups"] for ref in group}
        proof = [item for item in support["descriptions"] if (item["source_id"], item["revision"]) in proof_keys]
        speakers = {item["speaker_id"] for item in proof if item["speaker_id"]}
        if verify_assertion and speaker_id not in speakers | {None, "actor:" + context.actor_id}:
            raise AppError("claim_speaker_mismatch", status=403)
        if (verify_assertion and speaker_id == "actor:" + context.actor_id and assertion != "inferred"
                and not any(item["origin_kind"] == "actor" for item in proof)):
            raise AppError("claim_actor_author_unproven", status=403)
        proof_by_key = {(row["source_id"], row["revision"]): row for row in proof}
        if verify_assertion and assertion == "self_confirmed" and (
            speaker_id != subject_id or any(not any(
                proof_by_key[(ref.source_id, ref.revision)]["origin_kind"] == "human"
                and proof_by_key[(ref.source_id, ref.revision)]["speaker_id"] == subject_id
                for ref in group) for group in support["groups"])
        ):
            raise AppError("claim_self_confirmation_unproven", status=403)
        value, conditions = body.get("value"), body.get("conditions", {})
        if not isinstance(conditions, dict) or len(json.dumps(wire(value), ensure_ascii=False)) > 16000:
            raise AppError("invalid_claim_value")
        polarity = body.get("polarity", True)
        if type(polarity) is not bool:
            raise AppError("invalid_claim_polarity")
        valid_from, valid_until = optional_time(body.get("valid_from")), optional_time(body.get("valid_until"))
        if valid_from and valid_until and valid_until <= valid_from:
            raise AppError("invalid_claim_validity")
        memory_kind = body.get("memory_kind", "fact")
        if memory_kind not in {"fact", "hypothesis"}:
            raise AppError("invalid_memory_kind")
        if memory_kind == "hypothesis" and assertion != "inferred":
            raise AppError("memory_hypothesis_must_be_inferred")
        return {"actor_id": context.actor_id, "speaker_id": speaker_id, "subject_id": subject_id,
                "predicate": predicate, "value": value, "assertion": assertion, "polarity": polarity,
                "conditions": conditions, "valid_from": valid_from, "valid_until": valid_until,
                "observed_at": parse_time(body.get("observed_at", max(item["occurred_at"] for item in proof))),
                "memory_kind": memory_kind}

    async def write_claim_on(self, conn, context, body, *, support, claim_id, expected_revision,
                             change_kind="create", current_status="current", reason="", scope_fork_id=None,
                             effects=None):
        previous = await self._claim_head_on(conn, context, claim_id, expected_revision)
        values = await self._claim_values_on(conn, context, body, support,
            verify_assertion=change_kind != "reinforce" and current_status not in {"retracted", "history_only"},
            previous=previous)
        forked_from = None
        if previous and (previous["principal_id"] != context.principal_id or
                         change_kind != "reinforce" and not same_scope(previous, support)):
            if scope_fork_id is None:
                raise AppError("memory_revision_scope_mismatch", status=409)
            # An authorized private interpretation is a separate object; the shared head is untouched.
            forked_from = (claim_id, previous)
            claim_id, expected_revision, change_kind = scope_fork_id, 0, "create"
            previous = await self._claim_head_on(conn, context, claim_id, expected_revision)
        fingerprint = semantic_fingerprint(values)
        if not previous:
            await conn.execute(
                "INSERT INTO memory.claims(claim_id,current_revision,actor_id,principal_id) VALUES(%s,0,%s,%s)",
                (claim_id, context.actor_id, context.principal_id),
            )
        revision = expected_revision + 1
        content = (f"{values['subject_id']} {values['predicate']}: {json.dumps(wire(values['value']), ensure_ascii=False)}; "
                   f"assertion={values['assertion']}; polarity={values['polarity']}; "
                   f"conditions={json.dumps(values['conditions'], ensure_ascii=False)}")
        units = set(support.get("evidence_units", ()))
        units.update(item["evidence_unit"] for item in support["descriptions"])
        units = sorted(units)
        await conn.execute(
            "INSERT INTO memory.claim_revisions(claim_id,revision,speaker_id,subject_id,predicate,value,assertion,polarity,conditions,"
            "valid_from,valid_until,observed_at,state,source_refs,support_groups,scope_groups,scope_ids,evidence_units,content,lexical,"
            "recorded_at,change_kind,memory_kind,current_status,semantic_fingerprint,derivation_refs,change_reason,counterexample_refs) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (claim_id, revision, values["speaker_id"], values["subject_id"], values["predicate"], Jsonb(wire(values["value"])),
             values["assertion"], values["polarity"], Jsonb(values["conditions"]), values["valid_from"], values["valid_until"],
             values["observed_at"], Jsonb(wire(support["source_refs"])), Jsonb(wire(support["groups"])),
             Jsonb(support["scope_groups"]), support["scope_ids"], units, content, lexical(content), datetime.now(UTC),
             change_kind, values["memory_kind"], current_status, fingerprint, Jsonb(wire(support["derivation_refs"])), reason,
             Jsonb(wire(body.get("counterexample_refs", ())))),
        )
        await conn.execute("UPDATE memory.claims SET current_revision=%s WHERE claim_id=%s AND current_revision=%s",
                           (revision, claim_id, expected_revision))
        if previous and previous["semantic_fingerprint"] == fingerprint:
            await conn.execute(
                "UPDATE memory.claim_revisions new SET embedding=old.embedding,embedding_profile=old.embedding_profile "
                "FROM memory.claim_revisions old WHERE new.claim_id=%s AND new.revision=%s "
                "AND old.claim_id=new.claim_id AND old.revision=%s",
                (claim_id, revision, expected_revision))
        if previous:
            invalidated = await self.links.head_changed_on(conn, actor_id=context.actor_id, kind="claim", object_id=claim_id,
                                             old_revision=expected_revision,
                                             semantic_changed=previous["semantic_fingerprint"] != fingerprint,
                                             scope_groups=previous["scope_groups"])
            if effects is not None:
                effects.extend(invalidated)
        if current_status == "current" and (not previous or previous["semantic_fingerprint"] != fingerprint or previous["embedding"] is None):
            await self.jobs.enqueue(conn, JobSpec("memory.index_object", f"memory-index:claim:{claim_id}:{revision}",
                {"kind": "claim", "id": claim_id, "revision": revision}, ServiceClass.ELASTIC, subject_id=values["subject_id"]))
        row = await (await conn.execute(
            "SELECT r.*,c.actor_id FROM memory.claim_revisions r JOIN memory.claims c USING(claim_id) WHERE r.claim_id=%s AND r.revision=%s",
            (claim_id, revision),
        )).fetchone()
        result = codec.claim(row)
        if forked_from:
            old_id, old = forked_from
            await self.links.add_on(
                conn, context, link_id=stable_id(context.actor_id, claim_id, "scope_correction", "link"),
                from_ref={"kind": "hypothesis" if result.memory_kind == "hypothesis" else "claim", "id": result.claim_id, "revision": result.revision},
                to_ref={"kind": "hypothesis" if old["memory_kind"] == "hypothesis" else "claim", "id": old_id, "revision": old["revision"]},
                relation_kind="supersedes", support=support,
            )
        return result

    async def reinforce_on(self, conn, context, previous, support, *, reason=""):
        # Each alternative keeps its own necessary context. Turning their union into an AND
        # would incorrectly destroy an independent new support when an old source is forgotten.
        if previous["principal_id"] != context.principal_id:
            raise AppError("memory_support_owner_mismatch", status=403)
        old_derivation = codec.refs(previous.get("derivation_refs"))
        pairs = {}
        for group, scopes in zip(previous["support_groups"], previous["scope_groups"]):
            refs = unique_refs((*codec.refs(group), *old_derivation))
            pairs[body_hash(refs)] = (refs, scopes)
        for group, scopes in zip(support["groups"], support["scope_groups"]):
            refs = unique_refs((*group, *support["derivation_refs"]))
            pairs[body_hash(refs)] = (refs, scopes)
        groups = [pair[0] for pair in pairs.values()]
        if {body_hash(group) for group in groups} == {body_hash(group) for group in previous["support_groups"]}:
            return codec.claim(previous, support["source_refs"])
        # Carry immutable old alternatives without newly reading/locking them after head locks.
        scope_groups = [pair[1] for pair in pairs.values()]
        merged = {**support, "groups": groups, "derivation_refs": (),
                  "source_refs": unique_refs(ref for group in groups for ref in group),
                  "scope_groups": scope_groups,
                  "scope_ids": sorted({scope for group in scope_groups for scope in group}),
                  "evidence_units": previous["evidence_units"]}
        body = {key: previous[key] for key in CLAIM_FIELDS}
        body["counterexample_refs"] = codec.refs(previous.get("counterexample_refs"))
        # Adding support does not change the proposition or its semantic fingerprint.
        return await self.write_claim_on(conn, context, body, support=merged,
            claim_id=previous["claim_id"], expected_revision=previous["revision"], change_kind="reinforce",
            current_status=previous["current_status"], reason=reason)

    async def completion_basis_on(self, conn, context, basis, support, *, materials=None):
        if basis is None:
            human = [item for item in support["descriptions"] if item["origin_kind"] == "human"]
            if not human:
                raise AppError("matter_completion_basis_required")
            return {"kind": "human_report", "source_refs": wire(support["source_refs"])}
        if not isinstance(basis, dict) or basis.get("kind") not in {"human_report", "action_receipt"}:
            raise AppError("invalid_matter_completion_basis")
        if materials is None:
            if basis["kind"] == "action_receipt":
                raise AppError("matter_actual_receipt_unproven", status=403)
            return await self.completion_basis_on(conn, context, None, support)
        aliases = basis.get("material_refs", ())
        if not aliases or any(alias not in materials for alias in aliases):
            raise AppError("matter_completion_material_unavailable")
        chosen = [materials[alias] for alias in aliases]
        refs = unique_refs(ref for item in chosen for ref in codec.refs(item["source_refs"]))
        if not {body_hash(ref) for ref in refs} <= {body_hash(ref) for ref in support["source_refs"]}:
            raise AppError("matter_completion_support_missing")
        if basis["kind"] == "human_report":
            described = await self.sources.describe_for_write_on(conn, context, refs)
            if not described or any(item["origin_kind"] != "human" for item in described):
                raise AppError("matter_completion_requires_human_report", status=403)
            return {"kind": "human_report", "source_refs": wire(refs)}
        receipts = []
        for item in chosen:
            actual = item.get("actual_result") or {}
            if not isinstance(actual, dict):
                raise AppError("matter_actual_receipt_unproven", status=403)
            receipt = actual.get("receipt", actual)
            if not isinstance(receipt, dict):
                raise AppError("matter_actual_receipt_unproven", status=403)
            action_id = item.get("action_id") or actual.get("action_id") or receipt.get("action_id")
            receipt_id = item.get("receipt_id") or actual.get("receipt_id") or receipt.get("receipt_id")
            if (not action_id or not receipt_id or receipt.get("execution") != "succeeded"
                    or item.get("origin_kind") == "human" or actual.get("imagined") is True
                    or actual.get("kind") == "imagined" or actual.get("experience_kind") == "imagined"):
                raise AppError("matter_actual_receipt_unproven", status=403)
            receipts.append({"action_id": action_id, "receipt_id": receipt_id, "execution": "succeeded"})
        return {"kind": "action_receipt", "receipts": receipts, "source_refs": wire(refs)}

    async def write_matter_on(self, conn, context, body, *, support, matter_id, expected_revision,
                              change_kind="create", reason="", materials=None, scope_fork_id=None, effects=None):
        subject_id = body.get("subject_id", context.person_id)
        if subject_id not in await self.claims.visible_subjects(context, conn, (subject_id,)):
            raise AppError("matter_subject_forbidden", status=403)
        title, state = body.get("title"), body.get("state", "planned")
        if not isinstance(title, str) or not 1 <= len(title) <= 1000:
            raise AppError("invalid_matter_title")
        if state not in {"planned", "active", "rescheduled", "cancelled", "uncertain", "completed"}:
            raise AppError("invalid_matter_state")
        conditions, summary = body.get("conditions", {}), body.get("summary", "")
        if not isinstance(conditions, dict) or not isinstance(summary, str) or len(summary) > 8000:
            raise AppError("invalid_matter_content")
        due_at = optional_time(body.get("due_at"))
        if state == "completed" and materials is not None and body.get("completion_basis") is None:
            raise AppError("matter_completion_basis_required")
        basis = await self.completion_basis_on(conn, context, body.get("completion_basis"), support, materials=materials) if state == "completed" else None
        observed_at = parse_time(body.get("observed_at", max(item["occurred_at"] for item in support["descriptions"])))
        previous = await (await conn.execute("SELECT * FROM memory.matters WHERE matter_id=%s FOR UPDATE", (matter_id,))).fetchone()
        if type(expected_revision) is not int or expected_revision != (previous["revision"] if previous else 0):
            raise AppError("revision_conflict", status=409)
        if previous and previous["actor_id"] != context.actor_id:
            raise AppError("matter_forbidden", status=403)
        if previous and previous["subject_id"] != subject_id:
            raise AppError("matter_subject_immutable", status=409)
        if previous and (previous["principal_id"] != context.principal_id or not same_scope(previous, support)):
            if not scope_fork_id:
                raise AppError("memory_revision_scope_mismatch", status=409)
            result = await self.write_matter_on(conn, context, body, support=support, matter_id=scope_fork_id,
                expected_revision=0, reason=reason, materials=materials, effects=effects)
            await self.links.add_on(conn, context,
                link_id=stable_id(context.actor_id, scope_fork_id, "scope_correction", "link"),
                from_ref={"kind": "matter", "id": result.matter_id, "revision": result.revision},
                to_ref={"kind": "matter", "id": matter_id, "revision": previous["revision"]},
                relation_kind="supersedes", support=support)
            return result
        fingerprint = body_hash({"subject_id": subject_id, "title": title, "state": state,
                                 "due_at": due_at, "summary": summary, "conditions": conditions,
                                 "completion_basis": basis})
        content = title + " " + summary + " " + json.dumps(conditions, ensure_ascii=False)
        revision = expected_revision + 1
        row = await (await conn.execute(
            "INSERT INTO memory.matters(matter_id,actor_id,principal_id,subject_id,title,state,due_at,summary,conditions,"
            "source_refs,scope_ids,observed_at,revision,interpretation,recorded_at,change_kind,change_reason,completion_basis,"
            "support_groups,scope_groups,derivation_refs,semantic_fingerprint,lexical) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(matter_id) DO UPDATE SET title=excluded.title,state=excluded.state,due_at=excluded.due_at,"
            "summary=excluded.summary,conditions=excluded.conditions,source_refs=excluded.source_refs,scope_ids=excluded.scope_ids,"
            "observed_at=excluded.observed_at,revision=excluded.revision,interpretation=excluded.interpretation,recorded_at=excluded.recorded_at,"
            "change_kind=excluded.change_kind,change_reason=excluded.change_reason,completion_basis=excluded.completion_basis,"
            "support_groups=excluded.support_groups,scope_groups=excluded.scope_groups,derivation_refs=excluded.derivation_refs,"
            "semantic_fingerprint=excluded.semantic_fingerprint,lexical=excluded.lexical,active=true,current_status='current',"
            "embedding=CASE WHEN memory.matters.semantic_fingerprint=excluded.semantic_fingerprint THEN memory.matters.embedding ELSE NULL END,"
            "embedding_profile=CASE WHEN memory.matters.semantic_fingerprint=excluded.semantic_fingerprint THEN memory.matters.embedding_profile ELSE NULL END "
            "WHERE memory.matters.revision=%s RETURNING *",
            (matter_id, context.actor_id, context.principal_id, subject_id, title, state, due_at, summary, Jsonb(conditions),
             Jsonb(wire(support["source_refs"])), support["scope_ids"], observed_at, revision, bool(body.get("interpretation", False)),
             datetime.now(UTC), change_kind, reason, Jsonb(basis) if basis else None, Jsonb(wire(support["groups"])),
             Jsonb(support["scope_groups"]), Jsonb(wire(support["derivation_refs"])), fingerprint, lexical(content), expected_revision),
        )).fetchone()
        if row is None:
            raise AppError("revision_conflict", status=409)
        await conn.execute(
            "INSERT INTO memory.matter_revisions(matter_id,revision,actor_id,principal_id,subject_id,title,state,due_at,summary,conditions,"
            "completion_basis,observed_at,recorded_at,change_kind,change_reason,source_refs,support_groups,scope_groups,derivation_refs,"
            "scope_ids,current_status,interpretation,active,semantic_fingerprint) "
            "SELECT matter_id,revision,actor_id,principal_id,subject_id,title,state,due_at,summary,conditions,completion_basis,observed_at,recorded_at,"
            "change_kind,change_reason,source_refs,support_groups,scope_groups,derivation_refs,scope_ids,current_status,interpretation,active,"
            "semantic_fingerprint FROM memory.matters WHERE matter_id=%s", (matter_id,),
        )
        if previous:
            invalidated = await self.links.head_changed_on(conn, actor_id=context.actor_id, kind="matter", object_id=matter_id,
                old_revision=expected_revision, semantic_changed=previous["semantic_fingerprint"] != fingerprint,
                scope_groups=previous["scope_groups"])
            if effects is not None:
                effects.extend(invalidated)
        if not previous or previous["semantic_fingerprint"] != fingerprint:
            await self.jobs.enqueue(conn, JobSpec("memory.index_object", f"memory-index:matter:{matter_id}:{revision}",
                {"kind": "matter", "id": matter_id, "revision": revision}, ServiceClass.ELASTIC, subject_id=subject_id))
        return codec.matter(row)

    async def validate_read_set_on(self, conn, context, frozen):
        records = frozen.get("records", {})
        entries = tuple(frozen.get("read_set", ()))
        expected = {(value["kind"], value["id"], value["revision"]) for value in entries}
        heads = {}
        for value in entries:
            kind, object_id = value["kind"], value["id"]
            revision = value.get("head_revision") or value["revision"]
            key = ("claim" if kind == "hypothesis" else kind, object_id)
            if key in heads and heads[key] != revision:
                raise AppError("memory_read_set_ambiguous", status=409)
            heads[key] = revision
        if any((value["kind"], value["id"], value["revision"]) not in expected
               for value in records.values()):
            raise AppError("memory_read_set_incomplete", status=409)
        for (kind, object_id), revision in sorted(heads.items()):
            if kind == "claim":
                await self._claim_head_on(conn, context, object_id, revision)
            else:
                table, key = {"matter": ("matters", "matter_id"), "episode": ("episodes", "episode_id"),
                              "reflection": ("reflections", "proposal_id")}.get(kind, (None, None))
                if table is None:
                    raise AppError("invalid_memory_record_kind")
                row = await (await conn.execute(f"SELECT * FROM memory.{table} WHERE {key}=%s FOR UPDATE", (object_id,))).fetchone()
                if not row or row["actor_id"] != context.actor_id or row["revision"] != revision:
                    raise AppError("revision_conflict", status=409)
        # Multiple historical revisions of the same object may occur as legal relation
        # endpoints. Check their fixed rows separately after checking the frozen head once.
        for kind, object_id, revision in sorted(expected):
            if kind in {"claim", "hypothesis"}:
                row = await (await conn.execute(
                    "SELECT r.* FROM memory.claim_revisions r JOIN memory.claims c USING(claim_id) "
                    "WHERE r.claim_id=%s AND r.revision=%s AND c.actor_id=%s",
                    (object_id, revision, context.actor_id))).fetchone()
            elif kind == "matter":
                row = await (await conn.execute("SELECT * FROM memory.matter_revisions WHERE matter_id=%s AND revision=%s AND actor_id=%s",
                    (object_id, revision, context.actor_id))).fetchone()
            else:
                table, key = {"episode": ("episodes", "episode_id"), "reflection": ("reflections", "proposal_id")}[kind]
                row = await (await conn.execute(f"SELECT * FROM memory.{table} WHERE {key}=%s AND revision=%s AND actor_id=%s",
                    (object_id, revision, context.actor_id))).fetchone()
            if row is None or row.get("state") == "unsupported" or row.get("active") is False:
                raise AppError("memory_record_unavailable", status=409)
            frozen_states = {value["current_status"] for value in records.values()
                if value["id"] == object_id and value["revision"] == revision and "current_status" in value}
            if frozen_states and row.get("current_status", "current") not in frozen_states:
                raise AppError("revision_conflict", status=409)

    async def apply_operations_on(self, conn, context, *, batch_id, frozen, operations):
        """The caller owns source-first/batch locks and commits all coverage with this result."""
        if not context.actor_id or frozen.get("batch_id", batch_id) != batch_id or len(operations) > 64:
            raise AppError("memory_frozen_batch_invalid")
        await self.memory.identity.check_context(context, connection=conn)
        materials, records = frozen["materials"], frozen["records"]
        all_refs = unique_refs(ref for entry in (*materials.values(), *records.values(), *frozen.get("read_set", ()))
                               for ref in codec.refs(entry.get("source_refs", ())))
        if not await self.sources.validate_persistence(conn, context, all_refs):
            raise AppError("source_unavailable", status=403)
        await self.validate_read_set_on(conn, context, frozen)
        local_refs, changed, affected, link_ids, effects = {}, [], set(), [], []
        declarations = {}
        declared_operations = {}
        for operation in operations:
            target = operation["target"]
            if "create" in target:
                kind = {"add_claim": "claim", "revise_hypothesis": "hypothesis", "upsert_matter": "matter", "link": "link"}.get(operation["kind"])
                if kind is None or target["create"] in declarations:
                    raise AppError("memory_operation_create_invalid")
                declarations[target["create"]] = {"kind": kind, "id": stable_id(context.actor_id, batch_id, target["create"], kind), "revision": 1}
                declared_operations[target["create"]] = operation
            payload = operation["payload"]
            if operation["kind"] == "revise_claim" and payload.get("revision_mode") == "transition":
                alias = payload["successor_ref"]
                if alias in declarations:
                    raise AppError("memory_operation_create_invalid")
                declarations[alias] = {"kind": "claim", "id": stable_id(context.actor_id, batch_id, alias, "claim"), "revision": 1}
                declared_operations[alias] = operation
        counterexample_cache = {}
        def counterexample_sources(alias, visited=()):
            if alias in materials:
                return codec.refs(materials[alias]["source_refs"])
            if alias in records:
                return codec.refs(records[alias]["source_refs"])
            if alias not in declarations or declarations[alias]["kind"] == "link":
                raise AppError("memory_counterexample_invalid")
            if alias in visited:
                raise AppError("memory_operation_dependency_cycle")
            if alias in counterexample_cache:
                return counterexample_cache[alias]
            dependency = declared_operations[alias]
            refs = [ref for group in dependency["support_groups"] for item in group for ref in codec.refs(materials[item]["source_refs"])]
            refs.extend(ref for item in dependency["context_refs"] for ref in codec.refs(materials[item]["source_refs"]))
            refs.extend(ref for item in dependency["reads"] for ref in codec.refs(records[item]["source_refs"]))
            for item in dependency["payload"].get("counterexample_refs", ()):
                refs.extend(counterexample_sources(item, (*visited, alias)))
            counterexample_cache[alias] = unique_refs(refs)
            return counterexample_cache[alias]
        async def operation_support(operation):
            try:
                groups = [unique_refs(ref for alias in group for ref in codec.refs(materials[alias]["source_refs"]))
                          for group in operation["support_groups"]]
                derivation = [ref for alias in operation["context_refs"] for ref in codec.refs(materials[alias]["source_refs"])]
                for alias in operation["reads"]:
                    if operation["kind"] == "add_support" and alias == operation["target"].get("ref"):
                        continue
                    derivation.extend(codec.refs(records[alias]["source_refs"]))
                if operation["kind"] == "revise_hypothesis":
                    for alias in operation["payload"].get("counterexample_refs", ()):
                        if alias in materials:
                            derivation.extend(codec.refs(materials[alias]["source_refs"]))
                        elif alias in records:
                            if alias not in operation["reads"]:
                                raise AppError("memory_operation_read_missing")
                            derivation.extend(codec.refs(records[alias]["source_refs"]))
                        elif alias not in declarations:
                            raise AppError("memory_counterexample_invalid")
                        else:
                            derivation.extend(counterexample_sources(alias))
                return await self.support_on(conn, context, groups, derivation)
            except KeyError as exc:
                raise AppError("memory_operation_reference_invalid") from exc

        for operation in operations:
            if operation["subject_id"] not in frozen["subjects"]:
                raise AppError("memory_operation_subject_invalid")
            affected.add(operation["subject_id"])
            kind, target, payload = operation["kind"], operation["target"], operation["payload"]
            if kind == "link":
                continue
            support = await operation_support(operation)
            old = records.get(target.get("ref"))
            if old and (old.get("head_revision") or old["revision"]) != old["revision"]:
                raise AppError("memory_operation_historical_target")
            if old and (target["expected_revision"] != old["revision"] or target["ref"] not in operation["reads"]):
                raise AppError("revision_conflict", status=409)
            if old and old.get("subject_id") not in {None, operation["subject_id"]}:
                raise AppError("memory_operation_subject_mismatch", status=403)
            body = {**payload, "subject_id": operation["subject_id"]}
            if kind == "revise_hypothesis":
                body["counterexample_refs"] = unique_refs(ref for alias in payload["counterexample_refs"]
                                                         for ref in counterexample_sources(alias))
            observed = [optional_time(materials[alias].get("occurred_at")) for group in operation["support_groups"] for alias in group]
            body["observed_at"] = max((value for value in observed if value is not None), default=None)
            if body["observed_at"] is None:
                raise AppError("memory_observed_time_required")
            reason = operation["reason"]
            fork = stable_id(context.actor_id, batch_id, operation["op_id"] + ":scope", "matter" if kind == "upsert_matter" else "claim")
            if kind == "add_claim" or kind == "revise_hypothesis" and old is None:
                reference = declarations[target["create"]]
                body["memory_kind"] = "hypothesis" if kind == "revise_hypothesis" else "fact"
                result = await self.write_claim_on(conn, context, body, support=support, claim_id=reference["id"], expected_revision=0, reason=reason, effects=effects)
            elif kind in {"revise_claim", "revise_hypothesis", "add_support"}:
                if not old or old["kind"] not in {"claim", "hypothesis"}:
                    raise AppError("memory_operation_target_invalid")
                previous = await self._claim_head_on(conn, context, old["id"], old["revision"])
                mode = payload.get("revision_mode", "correction")
                if mode not in {"correction", "transition", "retract"}:
                    raise AppError("memory_revision_mode_invalid")
                if kind == "add_support":
                    if payload:
                        raise AppError("memory_support_payload_forbidden")
                    result = await self.reinforce_on(conn, context, previous, support, reason=reason)
                elif mode == "transition":
                    successor = declarations[payload["successor_ref"]]
                    body["memory_kind"] = previous["memory_kind"]
                    result = await self.write_claim_on(conn, context, body, support=support,
                        claim_id=successor["id"], expected_revision=0, change_kind="transition", reason=reason, effects=effects)
                    local_refs[payload["successor_ref"]] = {"kind": "hypothesis" if result.memory_kind == "hypothesis" else "claim", "id": result.claim_id, "revision": result.revision}
                    effective = optional_time(payload.get("effective_at"))
                    old_body = {key: previous[key] for key in CLAIM_FIELDS}
                    old_body["counterexample_refs"] = codec.refs(previous.get("counterexample_refs"))
                    if effective:
                        old_body["valid_until"] = effective
                    if same_scope(previous, support) and previous["principal_id"] == context.principal_id:
                        closed = await self.write_claim_on(conn, context, old_body, support=support,
                            claim_id=old["id"], expected_revision=old["revision"], change_kind="transition",
                            current_status="history_only", reason=reason, effects=effects)
                        changed.append({"kind": old["kind"], "id": old["id"], "revision": closed.revision})
                    link_id = await self.links.add_on(conn, context,
                        link_id=stable_id(context.actor_id, batch_id, operation["op_id"] + ":transition", "link"),
                        from_ref=local_refs[payload["successor_ref"]], to_ref=old,
                        relation_kind="supersedes", support=support, valid_from=effective)
                    link_ids.append(link_id)
                else:
                    if mode == "retract":
                        body = {key: previous[key] for key in CLAIM_FIELDS}
                        body["counterexample_refs"] = codec.refs(previous.get("counterexample_refs"))
                    body["memory_kind"] = "hypothesis" if kind == "revise_hypothesis" else previous["memory_kind"]
                    result = await self.write_claim_on(conn, context, body, support=support,
                        claim_id=old["id"], expected_revision=old["revision"],
                        change_kind="retract" if mode == "retract" else "correct",
                        current_status="retracted" if mode == "retract" else "current", reason=reason, scope_fork_id=fork, effects=effects)
            elif kind == "upsert_matter":
                if old and old["kind"] != "matter":
                    raise AppError("memory_operation_target_invalid")
                reference = old or declarations[target["create"]]
                body["interpretation"] = True
                result = await self.write_matter_on(conn, context, body, support=support, matter_id=reference["id"],
                    expected_revision=old["revision"] if old else 0, change_kind="transition" if old else "create",
                    reason=reason, materials=materials, scope_fork_id=fork, effects=effects)
            else:
                raise AppError("memory_operation_kind_invalid")
            reference = {"kind": "matter" if kind == "upsert_matter" else "hypothesis" if result.memory_kind == "hypothesis" else "claim",
                         "id": result.matter_id if kind == "upsert_matter" else result.claim_id, "revision": result.revision}
            if "create" in target:
                local_refs[target["create"]] = reference
            elif kind != "revise_claim" or payload.get("revision_mode") != "transition":
                # Commit identity only; link r-aliases still resolve from frozen records.
                local_refs[target["ref"]] = reference
            changed.append(reference)
        # Counterexamples can be material or existing/new evidence. They revise the sourced
        # hypothesis and never alter stable persona, relationship freezes or permissions.
        for operation in operations:
            if operation["kind"] != "revise_hypothesis":
                continue
            support = await operation_support(operation)
            target = operation["target"]
            hypothesis = local_refs[target.get("create") or target["ref"]]
            for alias in operation["payload"]["counterexample_refs"]:
                endpoint = records.get(alias) or local_refs.get(alias)
                if alias in materials:
                    material = materials[alias]
                    endpoint = ({"kind": "episode", "id": material["episode_id"],
                                 "revision": material["episode_revision"]} if material.get("episode_id") else None)
                if endpoint is None:
                    if alias in materials:
                        continue  # Precise unmaterialized originals remain on the hypothesis.
                    raise AppError("memory_counterexample_invalid")
                link_id = await self.links.add_on(conn, context,
                    link_id=stable_id(context.actor_id, batch_id, operation["op_id"] + ":counter:" + alias, "link"),
                    from_ref=endpoint, to_ref=hypothesis, relation_kind="counterexample", support=support)
                link_ids.append(link_id)
        for operation in operations:
            if operation["kind"] != "link":
                continue
            target, payload = operation["target"], operation["payload"]
            if "create" not in target:
                raise AppError("memory_link_create_required")
            support = await operation_support(operation)
            endpoints = []
            for key in ("from_ref", "to_ref"):
                alias = payload[key]
                endpoint = records.get(alias) or local_refs.get(alias)
                if endpoint is None or endpoint["kind"] == "link":
                    raise AppError("memory_link_endpoint_unavailable")
                if alias in records and alias not in operation["reads"]:
                    raise AppError("memory_operation_read_missing")
                endpoints.append(endpoint)
            link_id = await self.links.add_on(conn, context, link_id=declarations[target["create"]]["id"],
                from_ref=endpoints[0], to_ref=endpoints[1], relation_kind=payload["relation_kind"], support=support,
                valid_from=payload["valid_from"], valid_until=payload["valid_until"])
            local_refs[target["create"]] = {"kind": "link", "id": link_id, "revision": 1}
            link_ids.append(link_id)
        affected.update(row["subject_id"] for row in effects if row["subject_id"])
        return {"local_refs": local_refs, "changed": list({(ref["kind"], ref["id"], ref["revision"]): ref for ref in changed}.values()),
                "derived_invalidations": list({ref["id"]: ref for ref in effects}.values()),
                "affected_subject_ids": sorted(affected), "semantic_link_ids": link_ids}

    async def source_changed_on(self, conn, sources, kind):
        """All revisions remain subject to current retention; never resurrect an older head."""
        changes = Jsonb(wire(sources))
        patterns = [Jsonb([{"source_id": source.source_id, "revision": source.revision}]) for source in sources]
        affected = await (await conn.execute(
            "UPDATE memory.claim_revisions SET state='unsupported',content='',value='null',conditions='{}',change_reason='',"
            "embedding=NULL,embedding_profile=NULL,lexical='' WHERE state='active' "
            "AND source_refs @> ANY(%s::jsonb[]) AND sources.dependencies_intersect(source_refs,%s,%s) AND NOT ("
            "sources.any_support_usable(support_groups,scope_ids) AND "
            "sources.any_support_usable(jsonb_build_array(derivation_refs),scope_ids) AND "
            "sources.any_support_usable(jsonb_build_array(counterexample_refs),scope_ids)) RETURNING claim_id,revision",
            (patterns, changes, kind),
        )).fetchall()
        await conn.execute(
            "UPDATE memory.matter_revisions SET active=false,title='',summary='',conditions='{}',completion_basis=NULL,change_reason='' "
            "WHERE active AND source_refs @> ANY(%s::jsonb[]) AND sources.dependencies_intersect(source_refs,%s,%s) AND NOT ("
            "sources.any_support_usable(support_groups,scope_ids) AND sources.any_support_usable(jsonb_build_array(derivation_refs),scope_ids) AND "
            "sources.any_support_usable(jsonb_build_array(COALESCE(completion_basis->'source_refs','[]'::jsonb)),scope_ids))",
            (patterns, changes, kind),
        )
        await conn.execute(
            "UPDATE memory.matters m SET active=false,title='',summary='',conditions='{}',completion_basis=NULL,change_reason='',"
            "lexical='',embedding=NULL,embedding_profile=NULL WHERE m.active AND EXISTS(SELECT 1 FROM memory.matter_revisions r "
            "WHERE r.matter_id=m.matter_id AND r.revision=m.revision AND NOT r.active)")
        invalid_links = await (await conn.execute(
            "UPDATE memory.semantic_links SET state='unsupported' WHERE state='active' AND ("
            "(source_refs @> ANY(%s::jsonb[]) AND sources.dependencies_intersect(source_refs,%s,%s) AND NOT (sources.any_support_usable(support_groups,scope_ids) "
            "AND sources.any_support_usable(jsonb_build_array(derivation_refs),scope_ids))) OR "
            "EXISTS(SELECT 1 FROM memory.claim_revisions r WHERE r.state<>'active' AND "
            "((from_kind IN ('claim','hypothesis') AND from_id=r.claim_id AND from_revision=r.revision) OR "
            "(to_kind IN ('claim','hypothesis') AND to_id=r.claim_id AND to_revision=r.revision))) OR "
            "EXISTS(SELECT 1 FROM memory.matter_revisions r WHERE NOT r.active AND "
            "((from_kind='matter' AND from_id=r.matter_id AND from_revision=r.revision) OR "
            "(to_kind='matter' AND to_id=r.matter_id AND to_revision=r.revision)))) RETURNING actor_id",
            (patterns, changes, kind),
        )).fetchall()
        for actor_id in sorted({row["actor_id"] for row in invalid_links}):
            await self.links.rebuild_matter_index_on(conn, actor_id)
        return {"invalidated_claim_revisions": len(affected)}
