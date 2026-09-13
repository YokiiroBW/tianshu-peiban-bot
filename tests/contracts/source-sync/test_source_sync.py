import copy
import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import ValidationError

from ledger_model import Ledger, group_domain, public_domain, text_domain
from relations import (
    PACKAGE, Violation, canonical, check_access, check_barrier, check_confirmation,
    check_core, check_drafts, check_event, check_inherited, check_turn, digest,
    metadata, validator,
)

FIXTURE = json.loads((PACKAGE / "examples.json").read_text(encoding="utf-8"))
DOCUMENTS = FIXTURE["documents"]
CASES = json.loads((PACKAGE / "relations.json").read_text(encoding="utf-8"))


def sample():
    return {key: copy.deepcopy(value["document"]) for key, value in DOCUMENTS.items()}


class SchemaAndRelations(unittest.TestCase):
    def test_positive_documents(self):
        for name, entry in DOCUMENTS.items():
            with self.subTest(document=name):
                validator(entry["schema"]).validate(entry["document"])

    def test_synthetic_relations(self):
        for case in CASES:
            with self.subTest(case=case["id"]):
                values = sample()
                for mutation in case["mutations"]:
                    target = values
                    for part in mutation["path"][:-1]:
                        target = target[part]
                    target[mutation["path"][-1]] = copy.deepcopy(mutation["value"])
                check = case["check"]
                actual = None
                try:
                    if check.startswith("schema_"):
                        name = {"schema_commit": "commit", "schema_check": "check_response", "schema_core": "core"}[check]
                        validator(DOCUMENTS[name]["schema"]).validate(values[name])
                    else:
                        # Relational negatives must remain shape-valid, or they test the wrong layer.
                        for name, value in values.items():
                            validator(DOCUMENTS[name]["schema"]).validate(value)
                        if check == "barrier":
                            check_barrier(values)
                        elif check in {"core", "full"}:
                            check_core(values[check + "_request"], values[check])
                        elif check in {"access", "live"}:
                            prefix = "live_access" if check == "live" else "access"
                            check_access(values[prefix + "_request"], values[prefix], values["core"], FIXTURE["now"])
                        elif check == "event":
                            check_event(values["event"], values["core"]["turns"][0], values["core"]["facts"])
                        elif check == "check":
                            check_turn(values["check_request"], values["core"]["turns"][0], values["core"]["facts"])
                        elif check == "confirmation":
                            fact = values["core"]["facts"][0]
                            check_confirmation(values["confirmation"], values["revise"], fact["author"], fact["scope"], 1, FIXTURE["now"])
                        elif check == "draft":
                            check_drafts(values["commit"], values["event"], values["core"]["facts"])
                        else:
                            self.fail("Unknown relation: " + check)
                except Violation as error:
                    actual = str(error)
                except ValidationError:
                    if not check.startswith("schema_"):
                        raise
                    actual = "schema"
                self.assertEqual(actual, case["expected_error"])

    def test_archive_observation_does_not_prove_source(self):
        values = sample()
        source = values["core"]["facts"][0]["source"]
        source.update(archive_state="archived", locator="audit-evidence:synthetic-observation")
        validator("core_response").validate(values["core"])
        with self.assertRaisesRegex(Violation, "archive_unverified"):
            check_core(values["core_request"], values["core"])

    def test_inherited_author_and_profile_domains_are_independent(self):
        scope_a = sample()["event"]["scope"]
        scope_a["audience"] = "group"
        scope_b = dict(scope_a, person_id="person:synthetic-b")
        checks = [
            dict(version_domain=domain, scope=scope, scope_version=4)
            for scope in (scope_a, scope_b)
            for domain in ("text-dialogue/v1", "profile-memory/v1")
        ]
        versions = {(c["version_domain"], canonical(c["scope"])): 4 for c in checks}
        check_inherited(checks, versions)
        versions[("profile-memory/v1", canonical(scope_b))] = 5
        with self.assertRaisesRegex(Violation, "inherited_scope_changed"):
            check_inherited(checks, versions)
        versions[("profile-memory/v1", canonical(scope_b))] = 4
        del versions[("text-dialogue/v1", canonical(scope_b))]
        with self.assertRaisesRegex(Violation, "inherited_scope_changed"):
            check_inherited(checks, versions)

    def test_confirmation_cannot_move_account_binding_or_semantics(self):
        values = sample()
        fact, proof, request = values["core"]["facts"][0], values["confirmation"], values["revise"]
        for account, scope, version in [
            (dict(fact["author"], immutable_account_id="synthetic-b"), fact["scope"], 1),
            (fact["author"], dict(fact["scope"], actor_id="actor:other"), 1),
            (fact["author"], fact["scope"], 2),
        ]:
            with self.subTest(account=account, scope=scope, version=version):
                with self.assertRaisesRegex(Violation, "confirmation_binding"):
                    check_confirmation(proof, request, account, scope, version, FIXTURE["now"])
        request.update(revision_kind="correct", replacement_statement="每天喝咖啡")
        with self.assertRaisesRegex(Violation, "confirmation_binding"):
            check_confirmation(proof, request, fact["author"], fact["scope"], 1, FIXTURE["now"])

    def test_owner_snapshot_handles_aggregate_gap_and_unknown_delivery(self):
        values = sample()
        # Current owner version may advance without another turn_committed event.
        values["core"]["turns"][0]["aggregate_version"] = 27
        values["core"]["turns"][0]["delivery_state"] = "sent"
        check_event(values["event"], values["core"]["turns"][0], values["core"]["facts"])
        self.assertEqual(values["event"]["delivery_state"], "unknown")

    def test_live_origin_expires_while_historical_admission_does_not_authorize(self):
        values = sample()
        check_access(values["live_access_request"], values["live_access"], values["core"], FIXTURE["now"])
        with self.assertRaisesRegex(Violation, "viewer_binding"):
            check_access(values["live_access_request"], values["live_access"], values["core"], "2026-09-14T01:04:00Z")
        # No online context is accepted in a background source-access response.
        values["access"]["viewer_context"] = values["live_access"]["viewer_context"]
        with self.assertRaisesRegex(Violation, "viewer_binding"):
            check_access(values["access_request"], values["access"], values["core"])

    def test_mixed_event_requires_separate_classified_unit_sources(self):
        values = sample()
        first = values["core"]["facts"][0]
        second = copy.deepcopy(first)
        second["key"]["message_id"] = "message:synthetic-fiction"
        second["source"]["message_key"]["message_id"] = second["key"]["message_id"]
        second["source"]["receipt_id"] = "receipt:synthetic-fiction"
        second["classification"]["value"] = "fictional"
        event = values["event"]
        event.update(sources=[first["source"], second["source"]], reality="mixed")
        turn = values["core"]["turns"][0]
        turn.update(input_sources=event["sources"], committed_event=copy.deepcopy(event))
        check_event(event, turn, [first, second])
        unit = copy.deepcopy(values["commit"]["drafts"][0]["units"][0])
        unit.update(reality="fictional", statement="虚构人物的白天咖啡习惯", sources=[second["source"]])
        values["commit"]["drafts"][0]["units"].append(unit)
        validator("candidate_commit").validate(values["commit"])
        check_drafts(values["commit"], event, [first, second])
        unit["reality"] = "real"
        with self.assertRaisesRegex(Violation, "reality"):
            check_drafts(values["commit"], event, [first, second])
        second["classification"]["value"] = "mixed"
        with self.assertRaisesRegex(Violation, "reality"):
            check_event(event, turn, [first, second])

    def test_candidate_group_scope_is_not_sharing_approval(self):
        values = sample()
        values["event"]["scope"]["audience"] = "group"
        values["commit"]["drafts"][0]["scope"]["audience"] = "group"
        with self.assertRaisesRegex(Violation, "sharing_approval_required"):
            check_drafts(values["commit"], values["event"], values["core"]["facts"])


class TransactionTraces(unittest.TestCase):
    def setUp(self):
        runtime = Path(__file__).resolve().parent / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="ts002-", dir=runtime)
        self.path = Path(self.temp.name) / "contract.sqlite"
        self.ledger = Ledger(self.path)
        self.values = sample()
        self.fact = self.values["core"]["facts"][0]
        self.access = self.values["access"]["facts"][0]
        self.scope = self.fact["scope"]
        self.key = canonical(self.fact["key"])
        self.head = copy.deepcopy(self.values["core"]["head"])
        self.sync()

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def sync(self, **kwargs):
        ticket = kwargs.pop("ticket", self.ledger.ticket(self.scope))
        return self.ledger.sync(ticket, [self.fact], [self.access], self.head, **kwargs)

    def revision(self, number, withdrawn=False):
        self.fact["source"]["message_key"]["revision"] = number
        self.fact["source"]["receipt_id"] = "receipt:synthetic-" + str(number)
        self.fact["ingest_sequence"] = number
        self.fact["content_digest"] = digest(["synthetic changed input", number, withdrawn])
        self.fact["kind"] = "retract" if withdrawn else "edit"
        self.fact["state"] = "withdrawn" if withdrawn else "active"
        self.access["admission_digest"] = digest(metadata(self.fact))
        self.head["sequence"] += 1

    def test_identical_snapshot_does_not_bump_versions(self):
        before = self.ledger.state()
        self.sync()
        self.assertEqual(before, self.ledger.state())

    def test_same_revision_cannot_change_receipt_or_full_input(self):
        before = self.ledger.state()
        self.fact["source"]["receipt_id"] = "receipt:invented"
        with self.assertRaisesRegex(Violation, "idempotency_conflict"):
            self.sync()
        self.assertEqual(before, self.ledger.state())

    def test_zero_budget_conflict_keeps_sync_invalidation_committed(self):
        self.ledger.seed_group("group:complete", [self.key], text_domain(self.scope))
        known = self.ledger.probe(self.ledger.state()["revision"], self.scope)
        self.revision(2)
        revision = self.sync()
        with self.assertRaisesRegex(Violation, "scope_changed"):
            self.ledger.probe(revision, self.scope, known)
        state = self.ledger.state()
        self.assertFalse(state["groups"]["group:complete"]["active"])
        self.assertGreater(state["sources"][self.key]["epoch"], 1)
        self.ledger.close()
        self.ledger = Ledger(self.path)
        self.assertEqual(state, self.ledger.state())

    def test_failed_sync_does_not_advance_watermark_or_leave_half_invalidation(self):
        self.ledger.seed_group("group:complete", [self.key], text_domain(self.scope))
        before = self.ledger.state()
        self.revision(2)
        with self.assertRaisesRegex(RuntimeError, "synthetic crash"):
            self.sync(crash=True)
        self.ledger.close()
        self.ledger = Ledger(self.path)
        self.assertEqual(before, self.ledger.state())
        self.sync()
        self.assertFalse(self.ledger.state()["groups"]["group:complete"]["active"])

    def test_metadata_coverage_and_local_revision_cannot_be_skipped(self):
        ticket = self.ledger.ticket(self.scope)
        with self.assertRaisesRegex(Violation, "coverage"):
            self.ledger.sync(ticket, [], [], self.head)
        self.ledger.seed_group("group:concurrent", [self.key], text_domain(self.scope))
        with self.assertRaisesRegex(Violation, "changed_local"):
            self.sync(ticket=ticket)
        revision = self.sync()
        self.ledger.suppress(self.key)
        with self.assertRaisesRegex(Violation, "changed_local"):
            self.ledger.probe(revision, self.scope)

    def test_foreign_owner_restart_or_sequence_rollback_fails_closed(self):
        before = self.ledger.state()
        self.head["sequence"] -= 1
        with self.assertRaisesRegex(Violation, "recovery_required"):
            self.sync()
        self.head["generation"] = "core:restored-with-old-backup"
        with self.assertRaisesRegex(Violation, "recovery_required"):
            self.sync()
        self.assertEqual(before, self.ledger.state())

    def test_old_source_and_retract_cannot_resurrect(self):
        original_fact = copy.deepcopy(self.fact)
        self.revision(2, withdrawn=True)
        self.sync()
        self.fact = original_fact
        self.head["sequence"] += 1
        with self.assertRaisesRegex(Violation, "stale_source"):
            self.sync()
        self.revision(3)
        with self.assertRaisesRegex(Violation, "resurrection"):
            self.sync()
        self.assertFalse(self.ledger.active(self.ledger.state()["sources"][self.key]))

    def test_forget_survives_new_revision_and_restart(self):
        job = self.ledger.consume(self.values["event"], self.values["core"]["turns"][0])
        self.ledger.suppress(self.key)
        self.ledger.close()
        self.ledger = Ledger(self.path)
        self.revision(2)
        self.sync()
        self.assertTrue(self.ledger.state()["sources"][self.key]["suppressed"])
        with self.assertRaisesRegex(Violation, "stale_source"):
            self.ledger.commit(job, self.values["commit"]["drafts"], self.values["core"]["turns"][0])
        # A replay returns its historical job, which remains invalidated.
        self.assertEqual(job, self.ledger.consume(self.values["event"], self.values["core"]["turns"][0]))
        self.assertEqual(self.ledger.state()["jobs"][job]["state"], "stale_source")

    def test_entry_revoke_invalidates_source_and_pending_job(self):
        job = self.ledger.consume(self.values["event"], self.values["core"]["turns"][0])
        self.access.update(state="denied", reason="entry_revoked")
        revision = self.sync()
        self.assertFalse(self.ledger.active(self.ledger.state()["sources"][self.key]))
        self.assertEqual(self.ledger.state()["jobs"][job]["state"], "stale_source")
        with self.assertRaisesRegex(Violation, "scope_changed"):
            self.ledger.probe(revision, self.scope, 1)

    def test_private_and_withdrawn_profile_changes_do_not_leak_epoch(self):
        actor = self.scope["actor_id"]
        self.revision(2)
        revision = self.sync()
        self.assertEqual(self.ledger.probe(revision, self.scope, profile=True), 1)
        self.ledger.seed_group("profile:approved", [self.key], public_domain(actor))
        published = self.ledger.probe(self.ledger.state()["revision"], self.scope, profile=True)
        self.revision(3)
        revision = self.sync()
        withdrawn = self.ledger.probe(revision, self.scope, profile=True)
        self.assertEqual(withdrawn, published + 1)
        self.revision(4)
        revision = self.sync()
        self.assertEqual(self.ledger.probe(revision, self.scope, profile=True), withdrawn)

    def test_group_epoch_and_target_independent_profile_coverage(self):
        actor = self.scope["actor_id"]
        group_a = dict(self.scope, audience="group", conversation_id="group:synthetic-a")
        group_b = dict(group_a, conversation_id="group:synthetic-b")
        self.ledger.seed_group("profile:group-a", [self.key], group_domain(actor, group_a["conversation_id"]))
        # Coverage comes from all active projections in the epoch, no target lookup.
        self.assertEqual(self.ledger.ticket(group_a, profile=True)["keys"], [self.key])
        self.assertEqual(self.ledger.ticket(group_b, profile=True)["keys"], [])
        self.revision(2)
        revision = self.sync()
        self.assertEqual(self.ledger.probe(revision, group_a, profile=True), 3)
        self.assertEqual(self.ledger.probe(revision, group_b, profile=True), 1)

    def test_candidate_atomicity_duplicate_event_and_source_effects(self):
        event, turn = self.values["event"], self.values["core"]["turns"][0]
        job = self.ledger.consume(event, turn)
        renamed = dict(event, event_id="event:renamed")
        self.assertEqual(job, self.ledger.consume(renamed, turn))
        drafts = copy.deepcopy(self.values["commit"]["drafts"])
        drafts[0].update(category="relationship", relationship_delta=2)
        before = self.ledger.state()
        with self.assertRaisesRegex(RuntimeError, "synthetic crash"):
            self.ledger.commit(job, drafts, turn, crash=True)
        self.assertEqual(before, self.ledger.state())
        self.assertEqual(self.ledger.commit(job, drafts, turn), "committed")
        self.assertEqual(self.ledger.commit(job, drafts, turn), "committed")
        self.assertEqual(self.ledger.state()["effects"], 2)
        changed = copy.deepcopy(drafts)
        changed[0]["relationship_delta"] = 3
        with self.assertRaisesRegex(Violation, "idempotency_conflict"):
            self.ledger.commit(job, changed, turn)
        other_event = dict(event, event_id="event:other", aggregate_id="turn:other")
        other_turn = dict(turn, turn_id="turn:other", committed_event=other_event)
        second = self.ledger.consume(other_event, other_turn)
        self.assertEqual(self.ledger.commit(second, drafts, other_turn), "duplicate_source")
        self.assertEqual(self.ledger.state()["effects"], 2)

    def test_whole_group_qualifiers_retained_and_late_event_cannot_restore(self):
        event, turn = self.values["event"], self.values["core"]["turns"][0]
        job = self.ledger.consume(event, turn)
        drafts = self.values["commit"]["drafts"]
        self.ledger.commit(job, drafts, turn)
        stored = self.ledger.state()["groups"][job + ":0"]["units"]
        self.assertEqual(stored, drafts[0]["units"])
        self.assertEqual(stored[0]["negations"], ["晚上不喝咖啡"])
        self.revision(2)
        self.sync()
        self.assertEqual(job, self.ledger.consume(event, turn))
        self.assertFalse(self.ledger.state()["groups"][job + ":0"]["active"])

    def test_bootstrap_has_no_turn_or_source_requirement(self):
        values = sample()
        values["core_request"].update(keys=[], turn_ids=[])
        values["core"].update(facts=[], turns=[], request_digest=digest(values["core_request"]))
        check_core(values["core_request"], values["core"])
        fresh = Ledger(":memory:")
        try:
            revision = fresh.sync(fresh.ticket(self.scope), [], [], values["core"]["head"])
            self.assertEqual(fresh.probe(revision, self.scope), 1)
        finally:
            fresh.close()
        # Identity authentication/register itself is existing product behavior, not simulated here.


if __name__ == "__main__":
    unittest.main()
