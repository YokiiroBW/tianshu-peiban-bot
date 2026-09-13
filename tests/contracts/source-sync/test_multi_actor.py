"""Joint synthetic producer/consumer cases, exercised equally for group and private."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import ValidationError

from ledger_model import public_domain
from multi_actor_model import (
    ActorLedger, AdmissionCore, VERSION, actor_key, input_access_request, physical_key,
    selector, validate, verify_access, verify_actor_event, verify_first_mapping,
    verify_snapshot, verify_turn_order,
)
from relations import PACKAGE, Violation, canonical, digest, validator

FIXTURE = json.loads((PACKAGE / "multi-actor.examples.json").read_text(encoding="utf-8"))
NOW = FIXTURE["now"]
ACTORS = ["actor:a", "actor:b"]


def sample(audience):
    return {k: copy.deepcopy(v["document"]) for k, v in FIXTURE["scenarios"][audience]["documents"].items()}


def proof(request, original, granted=None, defaults=None):
    result = copy.deepcopy(original)
    result.update(request_id=request["command"]["request_id"], request_digest=digest(input_access_request(request)), ingest_digest=digest(request), input_digest=digest(request["input"]), origin_ref=request["command"]["origin"]["assertion_ref"])
    if granted is not None:
        result["actor_contexts"] = [c for c in result["actor_contexts"] if c["allowed_scope"]["actor_id"] in granted]
    if defaults is not None:
        result["default_actor_ids"] = defaults
    return result


def next_request(request, suffix, *, targets=None, revision=None, kind=None, message=None):
    value = copy.deepcopy(request)
    value["command"]["request_id"] += ":" + suffix
    value["command"]["idempotency_key"] += ":" + suffix
    if targets is not None:
        value["target_actor_ids"] = targets
    if revision is not None:
        value["input"]["message_key"]["revision"] = revision
    if kind is not None:
        value["input"]["kind"] = kind
        value["input"]["parts"] = [] if kind == "retract" else [dict(kind="text", text="白天也不喝咖啡。")]
    if message is not None:
        value["input"]["message_key"]["message_id"] = message
    return value


def current_access(snapshot, denied=(), sequence=1):
    request = dict(candidate_version=VERSION, request_id="access:current", operation="current", admissions=copy.deepcopy(snapshot["admissions"]), viewer=None)
    physicals = {canonical(p["key"]): p for p in snapshot["physicals"]}
    grants = [dict(selector=a["selector"], admission_digest=digest(a), entry_id="entry:" + a["scope"]["actor_id"], entry_digest=digest(["entry", a["scope"]["actor_id"]]), account=physicals[canonical(a["selector"]["key"])]["author"], scope=a["scope"], binding_version=a["binding_version"], state="denied" if a["scope"]["actor_id"] in denied else "allowed") for a in snapshot["admissions"]]
    response = dict(candidate_version=VERSION, request_id=request["request_id"], operation="current", request_digest=digest(request), head=dict(generation="synthetic-platform", sequence=sequence), grants=grants, viewer_context=None)
    return request, response


def frame(core, original, actors=ACTORS, denied=(), sequence=1):
    request = dict(candidate_version=VERSION, request_id="facts:current", mode="snapshot", selectors=[selector(physical_key(original["input"]), actor) for actor in actors], turn_ids=[], include_content=False)
    snapshot = core.facts(request)
    access_request, access = current_access(snapshot, denied, sequence)
    return request, snapshot, access_request, access


def identity(origin, account):
    return copy.deepcopy(FIXTURE["identity"])


def approved_group(ledger, admission, text, group_id):
    scope, sources = admission["scope"], [admission["source"]]
    approval = dict(scope=scope, sources_digest=digest(sources), text_digest=digest(text)) if scope["audience"] == "group" else None
    return ledger.write_group(group_id, scope, sources, text, approval)


class MultiActorJointContracts(unittest.TestCase):
    def test_all_joint_documents_and_owner_events(self):
        for audience, scenario in FIXTURE["scenarios"].items():
            with self.subTest(audience=audience):
                for entry in scenario["documents"].values():
                    validate(entry["schema"], entry["document"])
                data = sample(audience)
                verify_snapshot(data["facts_request"], data["facts"])
                verify_access(data["access_request"], data["access"], data["facts"])
                verify_first_mapping(data["request"], data["response"], FIXTURE["identity"], data["authority"], data["facts"]["admissions"], NOW)
                for turn in scenario["owner_turns"]:
                    validator("turn_fact").validate(turn)
                    verify_actor_event(turn["committed_event"], turn, data["facts"])

    def test_legacy_source_wire_maps_to_one_actor_without_adding_fields(self):
        legacy = json.loads((PACKAGE / "examples.json").read_text(encoding="utf-8"))["documents"]
        old = legacy["core"]["document"]["facts"][0]
        content = copy.deepcopy(legacy["content"]["document"])
        content.pop("target_actor_ids")
        key = physical_key(content)
        scope = old["scope"]
        core = AdmissionCore(FIXTURE["classification"])
        core.physicals[canonical(key)] = dict(key=key, revision=1, physical_receipt_id="synthetic:migrated-physical", conversation_id=scope["conversation_id"], audience=scope["audience"], author=old["author"], content_digest=digest(content), kind=content["kind"], state="active", classification=old["classification"], content=content)
        chosen = selector(key, scope["actor_id"])
        admission = dict(selector=chosen, scope=scope, source=copy.deepcopy(old["source"]), physical_receipt_id="synthetic:migrated-physical", binding_version=old["binding_version"], accepted_origin=old["accepted_origin"], accepted_at=old["accepted_at"])
        core.admissions[actor_key(chosen)] = admission
        request = dict(candidate_version=VERSION, request_id="facts:legacy", mode="snapshot", selectors=[chosen], turn_ids=[], include_content=False)
        snapshot = core.facts(request)
        verify_snapshot(request, snapshot)
        self.assertEqual(canonical(snapshot["admissions"][0]["source"]), canonical(old["source"]))
        validator("common#source").validate(snapshot["admissions"][0]["source"])
        illegal_wire = dict(old["source"], actor_id=scope["actor_id"])
        with self.assertRaises(ValidationError):
            validator("common#source").validate(illegal_wire)
        request["selectors"] = [selector(key, "actor:not-the-legacy-owner")]
        with self.assertRaisesRegex(Violation, "missing_actor_admission"):
            verify_snapshot(request, core.facts(request))

    def test_one_physical_input_has_distinct_actor_receipts_and_one_conversation(self):
        for audience in FIXTURE["scenarios"]:
            with self.subTest(audience=audience):
                data = sample(audience)
                core = AdmissionCore(FIXTURE["classification"])
                result = core.ingest(data["request"], data["authority"], identity, NOW)
                self.assertEqual(result, data["response"])
                self.assertEqual(len(core.physicals), 1)
                self.assertEqual(len(core.admissions), 2)
                self.assertEqual(len({a["source"]["receipt_id"] for a in core.admissions.values()}), 2)
                self.assertEqual(len({a["physical_receipt_id"] for a in core.admissions.values()}), 1)
                self.assertEqual(len({a["scope"]["conversation_id"] for a in core.admissions.values()}), 1)
                self.assertEqual(len({c["id"] for c in core.collectors.values()}), 2)

    def test_a_and_b_different_messages_never_share_collector(self):
        for audience in FIXTURE["scenarios"]:
            with self.subTest(audience=audience):
                data = sample(audience)
                core = AdmissionCore(FIXTURE["classification"])
                a = next_request(data["request"], "a", targets=[ACTORS[0]], message="message:a")
                b = next_request(data["request"], "b", targets=[ACTORS[1]], message="message:b")
                ra = core.ingest(a, proof(a, data["authority"]), identity, NOW)
                rb = core.ingest(b, proof(b, data["authority"]), identity, NOW)
                self.assertEqual(ra["conversation_id"], rb["conversation_id"])
                self.assertNotEqual(ra["outcomes"][0]["receipt"]["collection_id"], rb["outcomes"][0]["receipt"]["collection_id"])
                for collector in core.collectors.values():
                    self.assertEqual(len(collector["messages"]), 1)

    def test_separate_fanout_and_replay_do_not_alias_or_swallow_b(self):
        for audience in FIXTURE["scenarios"]:
            with self.subTest(audience=audience):
                data = sample(audience)
                core = AdmissionCore(FIXTURE["classification"])
                a = next_request(data["request"], "a", targets=[ACTORS[0]])
                b = next_request(data["request"], "b", targets=[ACTORS[1]])
                ra = core.ingest(a, proof(a, data["authority"]), identity, NOW)
                rb = core.ingest(b, proof(b, data["authority"]), identity, NOW)
                again = core.ingest(a, proof(a, data["authority"]), identity, NOW)
                self.assertEqual(ra["physical_receipt_id"], rb["physical_receipt_id"])
                self.assertNotEqual(ra["outcomes"][0]["receipt"]["receipt_id"], rb["outcomes"][0]["receipt"]["receipt_id"])
                self.assertEqual(again["outcomes"][0]["state"], "duplicate")
                self.assertEqual(len(core.admissions), 2)
                conflict = copy.deepcopy(a)
                conflict["target_actor_ids"] = [ACTORS[1]]
                with self.assertRaisesRegex(Violation, "idempotency_conflict"):
                    core.ingest(conflict, proof(conflict, data["authority"]), identity, NOW)

    def test_empty_target_uses_registered_defaults_and_retry_freezes_first_routing(self):
        for audience in FIXTURE["scenarios"]:
            with self.subTest(audience=audience):
                data = sample(audience)
                request = next_request(data["request"], "empty", targets=[])
                core = AdmissionCore(FIXTURE["classification"])
                result = core.ingest(request, proof(request, data["authority"], defaults=[ACTORS[0]]), identity, NOW)
                self.assertEqual(result["effective_actor_ids"], [ACTORS[0]])
                later = proof(request, data["authority"], defaults=ACTORS)
                later["routing_version"] = 2
                repeated = core.ingest(request, later, identity, NOW)
                self.assertEqual(repeated["effective_actor_ids"], [ACTORS[0]])
                self.assertEqual(len(core.admissions), 1)
                core = AdmissionCore(FIXTURE["classification"])
                result = core.ingest(request, proof(request, data["authority"], defaults=ACTORS), identity, NOW)
                self.assertEqual(result["effective_actor_ids"], ACTORS)
                core = AdmissionCore(FIXTURE["classification"])
                result = core.ingest(request, proof(request, data["authority"], defaults=[]), identity, NOW)
                self.assertEqual((result["routing_state"], result["outcomes"], core.identity_calls), ("unrouted", [], []))
                request["default_actor_ids"] = ACTORS
                with self.assertRaises(ValidationError):
                    validate("fanout_request", request)

    def test_only_a_authorized_cannot_claim_b_even_when_default_mentions_b(self):
        for audience in FIXTURE["scenarios"]:
            with self.subTest(audience=audience):
                data = sample(audience)
                core = AdmissionCore(FIXTURE["classification"])
                result = core.ingest(data["request"], proof(data["request"], data["authority"], granted=[ACTORS[0]], defaults=ACTORS), identity, NOW)
                self.assertEqual([o["state"] for o in result["outcomes"]], ["accepted", "forbidden"])
                self.assertIsNone(result["outcomes"][1]["receipt"])
                self.assertEqual(len(core.admissions), 1)
                request = copy.deepcopy(data["facts_request"])
                request["selectors"] = [selector(physical_key(data["request"]["input"]), ACTORS[1])]
                with self.assertRaisesRegex(Violation, "missing_actor_admission"):
                    verify_snapshot(request, core.facts(request))

    def test_real_physical_author_required_and_same_revision_body_is_immutable(self):
        for audience in FIXTURE["scenarios"]:
            with self.subTest(audience=audience):
                data = sample(audience)
                core = AdmissionCore(FIXTURE["classification"])
                core.ingest(data["request"], data["authority"], identity, NOW)
                changed = next_request(data["request"], "same-revision")
                changed["input"]["parts"] = [dict(kind="text", text="删掉否定后的假正文")]
                with self.assertRaisesRegex(Violation, "idempotency_conflict"):
                    core.ingest(changed, proof(changed, data["authority"]), identity, NOW)
                other = next_request(data["request"], "other-author", revision=2, kind="edit")
                other["input"]["author"]["immutable_account_id"] = "different-author"
                authority = proof(other, data["authority"])
                with self.assertRaisesRegex(Violation, "input_authority"):
                    core.ingest(other, authority, identity, NOW)
                authority["verified_account"] = other["input"]["author"]
                with self.assertRaisesRegex(Violation, "forbidden"):
                    core.ingest(other, authority, identity, NOW)

    def test_first_person_and_channel_mapping_has_no_source_cycle(self):
        core = AdmissionCore(FIXTURE["classification"])
        def forbidden_source_lookup(*args, **kwargs):
            raise AssertionError("Identity/bootstrap must not read SourceAuthority")
        core.facts = forbidden_source_lookup
        responses = []
        for audience in FIXTURE["scenarios"]:
            data = sample(audience)
            self.assertTrue(all(c["allowed_scope"]["person_id"] is None and c["allowed_scope"]["conversation_id"] is None for c in data["authority"]["actor_contexts"]))
            response = core.ingest(data["request"], data["authority"], identity, NOW)
            self.assertEqual(core.identity_calls[-1]["origin"]["assertion_ref"], data["authority"]["actor_contexts"][0]["assertion_ref"])
            self.assertNotEqual(core.identity_calls[-1]["origin"], data["request"]["command"]["origin"])
            admissions = [{k: copy.deepcopy(v) for k, v in a.items() if k != "receipt"} for a in core.admissions.values()]
            verify_first_mapping(data["request"], response, FIXTURE["identity"], data["authority"], admissions, NOW)
            responses.append(response)
            bad = copy.deepcopy(response)
            bad["outcomes"][1]["receipt"]["conversation_id"] = "conversation:wrong"
            with self.assertRaisesRegex(Violation, "mapping_response"):
                verify_first_mapping(data["request"], bad, FIXTURE["identity"], data["authority"], admissions, NOW)
            bad = copy.deepcopy(response)
            bad["outcomes"][1]["receipt"]["receipt_id"] = bad["outcomes"][0]["receipt"]["receipt_id"]
            with self.assertRaisesRegex(Violation, "receipt_alias"):
                verify_first_mapping(data["request"], bad, FIXTURE["identity"], data["authority"], admissions, NOW)
        self.assertEqual(len(core.identity_calls), 2)  # Once per physical request, not once per actor.
        self.assertEqual({r["person_id"] for r in responses}, {FIXTURE["identity"]["person_id"]})
        self.assertNotEqual(responses[0]["conversation_id"], responses[1]["conversation_id"])

    def test_two_active_turns_and_send_order_are_global_across_actors(self):
        for audience, scenario in FIXTURE["scenarios"].items():
            with self.subTest(audience=audience):
                turns = [dict(turn_id=t["turn_id"], actor_id=t["scope"]["actor_id"], conversation_id=t["scope"]["conversation_id"], turn_sequence=t["committed_event"]["turn_sequence"]) for t in scenario["owner_turns"]]
                turns.append(dict(turns[0], turn_id="turn:third", turn_sequence=3))
                a, b, third = [t["turn_id"] for t in turns]
                trace = [dict(kind="start", turn_id=a), dict(kind="start", turn_id=b), dict(kind="send", turn_id=a, segment_sequence=1), dict(kind="send", turn_id=a, segment_sequence=2), dict(kind="close", turn_id=a), dict(kind="start", turn_id=third), dict(kind="send", turn_id=b, segment_sequence=1), dict(kind="close", turn_id=b)]
                verify_turn_order(turns, trace)
                with self.assertRaisesRegex(Violation, "global_two_turn_limit"):
                    verify_turn_order(turns, trace[:2] + [dict(kind="start", turn_id=third)])
                with self.assertRaisesRegex(Violation, "global_send_order"):
                    verify_turn_order(turns, trace[:2] + [dict(kind="send", turn_id=b, segment_sequence=1)])
                with self.assertRaisesRegex(Violation, "segment_order"):
                    verify_turn_order(turns, trace[:2] + [dict(kind="send", turn_id=a, segment_sequence=2)])


class MultiActorMemory(unittest.TestCase):
    def setUp(self):
        runtime = (Path(__file__).resolve().parent / ".runtime").resolve()
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="actors-", dir=runtime)
        self.directory = Path(self.temp.name).resolve()
        assert self.directory.is_relative_to(runtime) and self.directory != runtime
        self.ledgers = []

    def tearDown(self):
        for ledger in self.ledgers:
            ledger.close()
        self.temp.cleanup()

    def prepare(self, audience):
        data = sample(audience)
        core = AdmissionCore(FIXTURE["classification"])
        core.ingest(data["request"], data["authority"], identity, NOW)
        ledger = ActorLedger(self.directory / (audience + ".sqlite"))
        self.ledgers.append(ledger)
        f = frame(core, data["request"])
        ledger.sync_actors(ledger.ticket(f[1]["admissions"][0]["scope"]), *f, f[1]["head"])
        for i, admission in enumerate(f[1]["admissions"]):
            self.assertEqual(approved_group(ledger, admission, "actor-specific:" + ACTORS[i], "group:" + ACTORS[i]), "committed")
        return data, core, ledger, f[1]["admissions"]

    def restart(self, ledger, audience):
        self.ledgers.remove(ledger)
        ledger.close()
        new = ActorLedger(self.directory / (audience + ".sqlite"))
        self.ledgers.append(new)
        return new

    def test_actor_specific_writes_and_forget_preserve_other_actor(self):
        for audience in FIXTURE["scenarios"]:
            with self.subTest(audience=audience):
                data, core, ledger, admissions = self.prepare(audience)
                a, b = admissions
                self.assertEqual(ledger.read(a["scope"]), ["actor-specific:actor:a"])
                self.assertEqual(ledger.read(b["scope"]), ["actor-specific:actor:b"])
                self.assertEqual(approved_group(ledger, a, "duplicate", "duplicate:a"), "duplicate_source")
                known_b = ledger.probe(ledger.state()["revision"], b["scope"])
                ledger.suppress(actor_key(a["selector"]))
                self.assertEqual(ledger.read(a["scope"]), [])
                self.assertEqual(ledger.read(b["scope"]), ["actor-specific:actor:b"])
                self.assertEqual(ledger.probe(ledger.state()["revision"], b["scope"], known_b), known_b)
                self.assertEqual(ledger.state()["versions"].get(public_domain(ACTORS[1]), 1), 1)
                ledger = self.restart(ledger, audience)
                edited = next_request(data["request"], "edit", revision=2, kind="edit")
                core.ingest(edited, proof(edited, data["authority"]), identity, NOW)
                f = frame(core, edited)
                ledger.sync_actors(ledger.ticket(a["scope"]), *f, f[1]["head"])
                self.assertFalse(ledger.current(ledger.state(), actor_key(a["selector"])))
                self.assertTrue(ledger.current(ledger.state(), actor_key(b["selector"])))
                self.assertEqual(approved_group(ledger, f[1]["admissions"][1], "new-b", "group:new-b"), "committed")
                self.assertEqual(ledger.read(a["scope"]), [])
                self.assertEqual(ledger.read(b["scope"]), ["new-b"])

    def test_physical_edit_invalidates_b_even_when_only_a_was_requested(self):
        for audience in FIXTURE["scenarios"]:
            with self.subTest(audience=audience):
                data, core, ledger, admissions = self.prepare(audience)
                a, b = admissions
                known_b = ledger.probe(ledger.state()["revision"], b["scope"])
                edited = next_request(data["request"], "a-edit", targets=[ACTORS[0]], revision=2, kind="edit")
                core.ingest(edited, proof(edited, data["authority"], granted=[ACTORS[0]]), identity, NOW)
                f = frame(core, edited, actors=[ACTORS[0]])
                revision = ledger.sync_actors(ledger.ticket(a["scope"]), *f, f[1]["head"])
                self.assertEqual(ledger.read(b["scope"]), [])
                self.assertFalse(ledger.current(ledger.state(), actor_key(b["selector"])))
                with self.assertRaisesRegex(Violation, "scope_changed"):
                    ledger.probe(revision, b["scope"], known_b)
                with self.assertRaisesRegex(Violation, "stale_source"):
                    approved_group(ledger, b, "old-b", "group:stale-b")
                to_b = next_request(edited, "b-admission", targets=[ACTORS[1]])
                result = core.ingest(to_b, proof(to_b, data["authority"], granted=[ACTORS[1]]), identity, NOW)
                self.assertTrue(result["physical_deduplicated"])
                f = frame(core, edited, actors=[ACTORS[1]])
                ledger.sync_actors(ledger.ticket(b["scope"]), *f, f[1]["head"])
                self.assertNotEqual(f[1]["admissions"][0]["source"]["receipt_id"], b["source"]["receipt_id"])
                self.assertEqual(approved_group(ledger, f[1]["admissions"][0], "edited-b", "group:edited-b"), "committed")

    def test_physical_retract_broadcasts_to_jobs_groups_and_restart_without_b_grant(self):
        for audience in FIXTURE["scenarios"]:
            with self.subTest(audience=audience):
                data, core, ledger, admissions = self.prepare(audience)
                for turn in FIXTURE["scenarios"][audience]["owner_turns"]:
                    ledger.queue_candidate("job:" + turn["scope"]["actor_id"], turn["committed_event"], turn, data["facts"])
                before = ledger.state()
                retract = next_request(data["request"], "retract", targets=[], revision=2, kind="retract")
                result = core.ingest(retract, proof(retract, data["authority"], granted=[]), identity, NOW)
                self.assertEqual(result["outcomes"], [])
                f = frame(core, retract, actors=[ACTORS[0]])
                with self.assertRaises(RuntimeError):
                    ledger.sync_actors(ledger.ticket(admissions[0]["scope"]), *f, f[1]["head"], crash=True)
                self.assertEqual(ledger.state(), before)
                revision = ledger.sync_actors(ledger.ticket(admissions[0]["scope"]), *f, f[1]["head"])
                ledger = self.restart(ledger, audience)
                for admission in admissions:
                    self.assertEqual(ledger.read(admission["scope"]), [])
                    self.assertEqual(ledger.state()["jobs"]["job:" + admission["scope"]["actor_id"]]["state"], "stale_source")
                    with self.assertRaisesRegex(Violation, "scope_changed"):
                        ledger.probe(revision, admission["scope"], 1)
                revive = next_request(retract, "revive", targets=ACTORS, revision=3, kind="edit")
                with self.assertRaisesRegex(Violation, "physical_tombstone"):
                    core.ingest(revive, proof(revive, data["authority"]), identity, NOW)

    def test_receipt_scope_and_viewer_cannot_be_swapped_to_b(self):
        for audience in FIXTURE["scenarios"]:
            with self.subTest(audience=audience):
                data, core, ledger, admissions = self.prepare(audience)
                a, b = admissions
                approval = dict(scope=b["scope"], sources_digest=digest([a["source"]]), text_digest=digest("forged")) if audience == "group" else None
                with self.assertRaisesRegex(Violation, "wrong_actor_receipt"):
                    ledger.write_group("forged", b["scope"], [a["source"]], "forged", approval)
                owner_b = copy.deepcopy(FIXTURE["scenarios"][audience]["owner_turns"][1])
                bad = copy.deepcopy(owner_b["committed_event"])
                bad["sources"] = [a["source"]]
                with self.assertRaisesRegex(Violation, "owner_event"):
                    verify_actor_event(bad, owner_b, data["facts"])
                owner_b["committed_event"] = bad
                owner_b["input_sources"] = bad["sources"]
                with self.assertRaisesRegex(Violation, "wrong_actor_receipt"):
                    verify_actor_event(bad, owner_b, data["facts"])
                aq, ar = current_access(data["facts"])
                aq["viewer"] = dict(origin=dict(assertion_ref="viewer:b"), scope=a["scope"])
                ar["request_digest"] = digest(aq)
                context = copy.deepcopy(data["authority"]["actor_contexts"][1])
                context.update(authenticated_service="companion", audience_service="memory", assertion_ref="viewer:b", allowed_scope=b["scope"])
                ar["viewer_context"] = context
                with self.assertRaisesRegex(Violation, "viewer_actor"):
                    verify_access(aq, ar, data["facts"], NOW)

    def test_revoke_a_and_owner_rollback_do_not_grant_or_invalidate_b(self):
        for audience in FIXTURE["scenarios"]:
            with self.subTest(audience=audience):
                data, core, ledger, admissions = self.prepare(audience)
                a, b = admissions
                f = frame(core, data["request"], actors=[ACTORS[0]], denied=[ACTORS[0]], sequence=2)
                ledger.sync_actors(ledger.ticket(a["scope"]), *f, f[1]["head"])
                self.assertEqual(ledger.read(a["scope"]), [])
                self.assertEqual(ledger.read(b["scope"]), ["actor-specific:actor:b"])
                state = ledger.state()
                old = frame(core, data["request"], actors=[ACTORS[0]], sequence=1)
                with self.assertRaisesRegex(Violation, "recovery_required"):
                    ledger.sync_actors(ledger.ticket(a["scope"]), *old, old[1]["head"])
                self.assertEqual(ledger.state(), state)
                old[3]["head"]["generation"] = "restored-platform"
                with self.assertRaisesRegex(Violation, "recovery_required"):
                    ledger.sync_actors(ledger.ticket(a["scope"]), *old, old[1]["head"])

    def test_actor_admission_is_not_group_sharing_approval(self):
        data, core, ledger, admissions = self.prepare("group")
        with self.assertRaisesRegex(Violation, "sharing_approval_required"):
            ledger.write_group("unapproved", admissions[0]["scope"], [admissions[0]["source"]], "private inference")


if __name__ == "__main__":
    unittest.main()
