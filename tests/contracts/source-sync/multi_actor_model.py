"""Candidate.2 executable relations/reference state, not production auth or Core code."""
import copy
import json
from datetime import datetime

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Resource

from ledger_model import Ledger, text_domain
from relations import PACKAGE, REGISTRY, canonical, digest, require, source_key, validator

VERSION = "source-sync/candidate.2"
SCHEMA = json.loads((PACKAGE / "multi-actor.schema.json").read_text(encoding="utf-8"))
Draft202012Validator.check_schema(SCHEMA)
REGISTRY = REGISTRY.with_resource(SCHEMA["$id"], Resource.from_contents(SCHEMA))


def validate(name, value):
    Draft202012Validator(
        {"$ref": SCHEMA["$id"] + "#/$defs/" + name},
        registry=REGISTRY, format_checker=FormatChecker(),
    ).validate(value)


def selector(key, actor):
    return dict(key=key, actor_id=actor)


def actor_key(value):
    return canonical(value)


def physical_key(value):
    key = value["message_key"]
    return dict(channel=key["channel"], message_id=key["message_id"])


def input_access_request(request):
    return dict(candidate_version=VERSION, request_id=request["command"]["request_id"], operation="input", ingest=request)


def verify_input(request, authority, now):
    validate("fanout_request", request)
    validate("input_authority", authority)
    data = request["input"]
    require(authority["request_id"] == request["command"]["request_id"] and authority["request_digest"] == digest(input_access_request(request)), "input_authority")
    require(authority["ingest_digest"] == digest(request) and authority["input_digest"] == digest(data), "input_authority")
    require(authority["verified_account"] == data["author"] and authority["verified_channel"] == data["message_key"]["channel"] and authority["origin_ref"] == request["command"]["origin"]["assertion_ref"], "input_authority")
    require(datetime.fromisoformat(authority["expires_at"]) > datetime.fromisoformat(now) and datetime.fromisoformat(request["command"]["deadline_at"]) > datetime.fromisoformat(now), "expired_input")
    grants = {c["allowed_scope"]["actor_id"]: c for c in authority["actor_contexts"]}
    require(len(grants) == len(authority["actor_contexts"]), "ambiguous_grant")
    return grants


def actor_authorized(context, actor, request, person, conversation, now, audience):
    if context is None:
        return False
    scope = context["allowed_scope"]
    return (
        context["authenticated_service"] == "platform"  # This fixture's registered ingress service.
        and context["audience_service"] == "companion" and not context["revoked"]
        and context["verified_account"] == request["input"]["author"]
        and context["verified_channel"] == request["input"]["message_key"]["channel"]
        and scope["actor_id"] == actor and scope["person_id"] in (None, person)
        and scope["audience"] == audience
        and scope["conversation_id"] in (None, conversation)
        and datetime.fromisoformat(context["expires_at"]) > datetime.fromisoformat(now)
    )


class AdmissionCore:
    """Synthetic owner of P, per-actor receipts and collector keys; no network/scheduler."""
    def __init__(self, classification):
        self.physicals, self.admissions, self.collectors, self.commands = {}, {}, {}, {}
        self.sequence, self.head, self.identity_calls = 0, 0, []
        self.conversations = {}
        self.classification = copy.deepcopy(classification)

    def new_id(self, kind):
        self.sequence += 1
        return "synthetic-" + kind + ":" + str(self.sequence)

    def ingest(self, request, authority, identity_lookup, now):
        grants = verify_input(request, authority, now)
        data = copy.deepcopy(request["input"])
        existing = self.physicals.get(canonical(physical_key(data)))
        require(existing is None or existing["audience"] == authority["audience"], "physical_binding")
        semantic = digest(dict(input=data, target_actor_ids=request["target_actor_ids"]))
        command_key = request["command"]["idempotency_key"]
        prior = self.commands.get(command_key)
        if prior:
            require(prior["semantic"] == semantic, "idempotency_conflict")
            saved = copy.deepcopy(prior["response"])
            for outcome in saved["outcomes"]:
                if outcome["receipt"] is not None:
                    require(actor_authorized(grants.get(outcome["actor_id"]), outcome["actor_id"], request, saved["person_id"], saved["conversation_id"], now, authority["audience"]), "forbidden")
                    outcome["state"] = "duplicate"
                    outcome["receipt"].update(request_id=request["command"]["request_id"], deduplicated=True)
            saved.update(request_id=request["command"]["request_id"], request_digest=digest(request), physical_deduplicated=True)
            return saved
        key = physical_key(data)
        pk = canonical(key)
        old = self.physicals.get(pk)
        revision = data["message_key"]["revision"]
        if old:
            require(old["author"] == data["author"], "forbidden")
            require(revision >= old["revision"], "stale_source")
            require(not (old["state"] == "withdrawn" and data["kind"] != "retract"), "physical_tombstone")
            if revision == old["revision"]:
                require(old["content_digest"] == digest(data), "idempotency_conflict")
        else:
            require(data["kind"] == "message", "missing_physical")
        channel = canonical(key["channel"])
        conversation = self.conversations.get(channel, "synthetic-conversation:" + digest(key["channel"])[:12])
        actors = [] if data["kind"] == "retract" else sorted(request["target_actor_ids"] or authority["default_actor_ids"])
        # Identity owner is an injected Memory authority, never generated from source/actor IDs.
        identity = None
        usable = [grants[a] for a in actors if a in grants and actor_authorized(grants[a], a, request, grants[a]["allowed_scope"]["person_id"], conversation, now, authority["audience"])]
        if usable:
            origin = dict(assertion_ref=usable[0]["assertion_ref"])
            self.identity_calls.append(dict(origin=copy.deepcopy(origin), account=copy.deepcopy(data["author"])))
            identity = identity_lookup(origin, copy.deepcopy(data["author"]))
        person = identity["person_id"] if identity else None
        self.conversations[channel] = conversation
        physical_duplicate = old is not None and revision == old["revision"]
        if not physical_duplicate:
            self.physicals[pk] = dict(
                key=key, revision=revision, physical_receipt_id=self.new_id("physical"),
                conversation_id=conversation, author=data["author"], content_digest=digest(data),
                audience=authority["audience"],
                kind=data["kind"], state="withdrawn" if data["kind"] == "retract" else "active",
                classification=copy.deepcopy(self.classification),
                content=copy.deepcopy(data),
            )
        physical = self.physicals[pk]
        outcomes = []
        for actor in actors:
            context = grants.get(actor)
            if not actor_authorized(context, actor, request, person, conversation, now, authority["audience"]):
                outcomes.append(dict(actor_id=actor, state="forbidden", receipt=None))
                continue
            chosen = selector(key, actor)
            ak = actor_key(chosen)
            previous = self.admissions.get(ak)
            if previous and previous["source"]["message_key"]["revision"] == revision:
                receipt = copy.deepcopy(previous["receipt"])
                receipt.update(request_id=request["command"]["request_id"], deduplicated=True)
                outcomes.append(dict(actor_id=actor, state="duplicate", receipt=receipt))
                continue
            scope = dict(context["allowed_scope"], person_id=person, conversation_id=conversation)
            collector_key = canonical([channel, data["author"], actor])
            collector = self.collectors.get(collector_key)
            if collector is None:
                collector = dict(id=self.new_id("collector"), scope=scope, messages={}, revision=0, first_sequence=self.sequence)
                self.collectors[collector_key] = collector
            collector["revision"] += 1
            receipt = dict(
                schema_version=1, request_id=request["command"]["request_id"], receipt_id=self.new_id("admission"), deduplicated=False,
                conversation_id=conversation, person_id=person,
                collection_key=dict(channel=key["channel"], author=data["author"]), collection_id=collector["id"], collection_revision=collector["revision"],
                accepted_at=now, ingest_sequence=self.sequence, archive_state="pending",
            )
            source = dict(message_key=data["message_key"], receipt_id=receipt["receipt_id"], archive_state="pending", locator=None)
            collector["messages"][pk] = source
            self.admissions[ak] = dict(selector=chosen, scope=scope, source=source, physical_receipt_id=physical["physical_receipt_id"], binding_version=identity["binding_version"], accepted_origin=dict(assertion_ref=context["assertion_ref"]), accepted_at=now, receipt=receipt)
            outcomes.append(dict(actor_id=actor, state="accepted", receipt=receipt))
        self.head += 1
        response = dict(candidate_version=VERSION, request_id=request["command"]["request_id"], request_digest=digest(request), physical_receipt_id=physical["physical_receipt_id"], physical_deduplicated=physical_duplicate, conversation_id=conversation, person_id=person, effective_actor_ids=actors, routing_version=authority["routing_version"], routing_state="routed" if actors else "unrouted", outcomes=outcomes)
        self.commands[command_key] = dict(semantic=semantic, response=copy.deepcopy(response))
        validate("fanout_response", response)
        return copy.deepcopy(response)

    def facts(self, request):
        validate("facts_request", request)
        keys = {canonical(s["key"]) for s in request["selectors"]}
        physicals = []
        for pk in sorted(keys):
            fact = copy.deepcopy(self.physicals.get(pk, dict(key=json.loads(pk), state="missing")))
            if "content" in fact and not request["include_content"]:
                fact["content"] = None
            physicals.append(fact)
        admissions = []
        for selected in request["selectors"]:
            fact = self.admissions.get(actor_key(selected))
            admissions.append({k: copy.deepcopy(v) for k, v in fact.items() if k != "receipt"} if fact else dict(selector=selected, state="missing"))
        response = dict(candidate_version=VERSION, request_id=request["request_id"], request_digest=digest(request), head=dict(generation="synthetic-core", sequence=self.head), physicals=physicals, admissions=admissions, turns=[])
        validate("facts_response", response)
        return response


def verify_snapshot(request, response):
    validate("facts_request", request)
    validate("facts_response", response)
    require(response["request_id"] == request["request_id"] and response["request_digest"] == digest(request), "correlation")
    admissions = {actor_key(a["selector"]): a for a in response["admissions"]}
    physicals = {canonical(p["key"]): p for p in response["physicals"]}
    require(len(admissions) == len(response["admissions"]) and set(admissions) == {actor_key(s) for s in request["selectors"]}, "actor_coverage")
    require(len(physicals) == len(response["physicals"]) and set(physicals) == {canonical(s["key"]) for s in request["selectors"]}, "physical_coverage")
    require({t["turn_id"] for t in response["turns"]} == set(request["turn_ids"]), "turn_coverage")
    for physical in physicals.values():
        require(physical["state"] != "missing", "dependency_unavailable")
        if request["include_content"]:
            data = physical["content"]
            require(data is not None and digest(data) == physical["content_digest"], "content_digest")
            require(physical_key(data) == physical["key"] and data["message_key"]["revision"] == physical["revision"] and data["author"] == physical["author"] and data["kind"] == physical["kind"], "physical_binding")
        else:
            require(physical["content"] is None, "metadata_body")
    receipts = set()
    for admission in admissions.values():
        require(admission.get("state") != "missing", "missing_actor_admission")
        require(admission["selector"]["actor_id"] == admission["scope"]["actor_id"] and source_key(admission["source"]) == canonical(admission["selector"]["key"]), "actor_binding")
        physical = physicals[canonical(admission["selector"]["key"])]
        require(admission["scope"]["conversation_id"] == physical["conversation_id"] and admission["scope"]["audience"] == physical["audience"], "physical_binding")
        require(admission["source"]["archive_state"] == "pending" and admission["source"]["locator"] is None, "archive_unverified")
        require(admission["source"]["receipt_id"] not in receipts, "receipt_alias")
        receipts.add(admission["source"]["receipt_id"])
    return physicals, admissions


def verify_access(request, response, snapshot, now=None):
    validate("current_access_request", request)
    validate("current_access_response", response)
    require(response["request_id"] == request["request_id"] and response["request_digest"] == digest(request), "correlation")
    facts = {actor_key(a["selector"]): a for a in snapshot["admissions"]}
    requested = {actor_key(a["selector"]): a for a in request["admissions"]}
    grants = {actor_key(g["selector"]): g for g in response["grants"]}
    require(facts == requested and len(grants) == len(response["grants"]) and set(grants) == set(facts), "actor_coverage")
    physicals = {canonical(p["key"]): p for p in snapshot["physicals"]}
    for key, grant in grants.items():
        admission = facts[key]
        require(grant["admission_digest"] == digest(admission), "admission_binding")
        require(grant["scope"] == admission["scope"] and grant["binding_version"] == admission["binding_version"] and grant["account"] == physicals[canonical(admission["selector"]["key"])]["author"], "actor_binding")
    if request["viewer"] is not None:
        context = response["viewer_context"]
        require(context is not None and context["allowed_scope"] == request["viewer"]["scope"] and context["assertion_ref"] == request["viewer"]["origin"]["assertion_ref"] and context["audience_service"] == "memory" and context["authenticated_service"] == "companion" and not context["revoked"], "viewer_actor")
        require(now is not None and datetime.fromisoformat(context["expires_at"]) > datetime.fromisoformat(now), "viewer_actor")
    else:
        require(response["viewer_context"] is None, "viewer_actor")
    return grants


def verify_first_mapping(request, response, identity):
    validate("fanout_response", response)
    require(response["request_id"] == request["command"]["request_id"] and response["request_digest"] == digest(request), "mapping_response")
    accepted = [r for r in response["outcomes"] if r["receipt"] is not None]
    require(bool(accepted) and response["person_id"] == identity["person_id"], "mapping_response")
    require(len({r["receipt"]["receipt_id"] for r in accepted}) == len(accepted), "receipt_alias")
    for result in accepted:
        receipt = result["receipt"]
        require(receipt["person_id"] == identity["person_id"] and receipt["conversation_id"] == response["conversation_id"] and receipt["collection_key"] == dict(channel=request["input"]["message_key"]["channel"], author=request["input"]["author"]), "mapping_response")


def verify_actor_event(event, owner_turn, snapshot):
    validator("conversation#committed_event").validate(event)
    owner = owner_turn["committed_event"]
    require(owner is not None and {k: v for k, v in event.items() if k != "event_id"} == {k: v for k, v in owner.items() if k != "event_id"}, "owner_event")
    require(event["scope"] == owner_turn["scope"] and event["aggregate_id"] == owner_turn["turn_id"] and event["input_revision"] == owner_turn["input_revision"] and event["sources"] == owner_turn["input_sources"], "owner_event")
    admissions = {actor_key(a["selector"]): a for a in snapshot["admissions"]}
    for source in event["sources"]:
        selected = selector(physical_key(dict(message_key=source["message_key"])), event["scope"]["actor_id"])
        admission = admissions.get(actor_key(selected))
        require(admission is not None and admission.get("state") != "missing" and admission["scope"] == event["scope"] and admission["source"] == source, "wrong_actor_receipt")


class ActorLedger(Ledger):
    """Reuses only the old reference model's SQLite transaction and version helpers."""
    def __init__(self, path):
        super().__init__(path)
        self.mutate(lambda state: state.setdefault("physicals", {}))

    def sync_actors(self, ticket, request, snapshot, access_request, access, final_head, *, crash=False, now=None):
        physicals, admissions = verify_snapshot(request, snapshot)
        grants = verify_access(access_request, access, snapshot, now)
        require(snapshot["head"] == final_head, "moving_owner")
        def apply(state):
            require(state["revision"] == ticket["revision"] and set(ticket["keys"]) <= set(admissions), "coverage")
            for owner, head in (("core", snapshot["head"]), ("platform", access["head"])):
                old = state[owner + "_head"]
                require(old is None or (head["generation"] == old["generation"] and head["sequence"] >= old["sequence"]), "recovery_required")
            domains, invalidated, changed = set(), set(), False
            for pk, physical in physicals.items():
                new = {k: v for k, v in physical.items() if k != "content"}
                old = state["physicals"].get(pk)
                if old:
                    require(new["revision"] >= old["revision"], "stale_source")
                    require(not (old["state"] == "withdrawn" and new["state"] != "withdrawn"), "physical_tombstone")
                    if new["revision"] == old["revision"]:
                        require({k: v for k, v in new.items() if k != "classification"} == {k: v for k, v in old.items() if k != "classification"}, "physical_conflict")
                changed = changed or old != new
                if old is not None and new != old:
                    # Negative physical changes invalidate every locally known actor, not just requester.
                    for ak, row in state["sources"].items():
                        if canonical(row["fact"]["selector"]["key"]) == pk:
                            row["epoch"] += 1
                            invalidated.add(ak)
                            domains.add(text_domain(row["fact"]["scope"]))
                            self.invalidate(state, ak, domains)
                state["physicals"][pk] = copy.deepcopy(new)
            for ak, fact in admissions.items():
                grant = grants[ak]
                old = state["sources"].get(ak)
                if old is not None and old["fact"] == fact and old["access"] == grant:
                    continue
                changed = True
                if old:
                    require(fact["source"]["message_key"]["revision"] >= old["fact"]["source"]["message_key"]["revision"], "stale_source")
                    if fact["source"]["message_key"]["revision"] == old["fact"]["source"]["message_key"]["revision"]:
                        require(fact == old["fact"], "admission_conflict")
                    if ak not in invalidated:
                        old["epoch"] += 1
                        domains.add(text_domain(old["fact"]["scope"]))
                        self.invalidate(state, ak, domains)
                state["sources"][ak] = dict(fact=copy.deepcopy(fact), access=copy.deepcopy(grant), epoch=old["epoch"] if old else 1, suppressed=old["suppressed"] if old else False)
            self.bump(state, domains)
            state["core_head"], state["platform_head"] = copy.deepcopy(snapshot["head"]), copy.deepcopy(access["head"])
            if changed:
                state["revision"] += 1
            return state["revision"]
        return self.mutate(apply, crash=crash)

    @staticmethod
    def current(state, ak):
        row = state["sources"][ak]
        fact = row["fact"]
        physical = state["physicals"][canonical(fact["selector"]["key"])]
        return not row["suppressed"] and row["access"]["state"] == "allowed" and physical["state"] == "active" and physical["revision"] == fact["source"]["message_key"]["revision"] and physical["physical_receipt_id"] == fact["physical_receipt_id"] and physical["classification"]["value"] in {"real", "fictional"}

    def write_group(self, group_id, scope, sources, text, shared_approval=None):
        # Synthetic precondition standing for existing Memory's exact shared-approval workflow;
        # this object is not a new wire proof and cannot be supplied by a production client.
        if scope["audience"] == "group":
            require(shared_approval == dict(scope=scope, sources_digest=digest(sources), text_digest=digest(text)), "sharing_approval_required")
        def apply(state):
            keys = [actor_key(selector(physical_key(dict(message_key=s["message_key"])), scope["actor_id"])) for s in sources]
            for ak, source in zip(keys, sources):
                require(ak in state["sources"] and state["sources"][ak]["fact"]["scope"] == scope and state["sources"][ak]["fact"]["source"] == source, "wrong_actor_receipt")
                require(self.current(state, ak), "stale_source")
            effects = [canonical([ak, source["message_key"]["revision"], scope]) for ak, source in zip(keys, sources)]
            if any(key in state["writes"] for key in effects):
                return "duplicate_source"
            state["groups"][group_id] = dict(keys=keys, domain=text_domain(scope), active=True, text=text)
            for key in effects:
                state["writes"][key] = group_id
            state["revision"] += 1
            return "committed"
        return self.mutate(apply)

    def read(self, scope):
        state = self.state()
        return [g["text"] for g in state["groups"].values() if g["active"] and g["domain"] == text_domain(scope) and all(self.current(state, k) for k in g["keys"])]

    def queue_candidate(self, job_id, event, owner_turn, snapshot):
        verify_actor_event(event, owner_turn, snapshot)
        def apply(state):
            prior = state["events"].get(event["event_id"])
            if prior:
                require(prior["digest"] == digest(event), "idempotency_conflict")
                return prior["job"]
            turn_key = canonical([event["aggregate_id"], event["input_revision"]])
            signature = digest({k: v for k, v in event.items() if k != "event_id"})
            prior_turn = state["turns"].get(turn_key)
            if prior_turn:
                require(prior_turn["signature"] == signature, "idempotency_conflict")
                state["events"][event["event_id"]] = dict(digest=digest(event), job=prior_turn["job"])
                return prior_turn["job"]
            keys = [actor_key(selector(physical_key(dict(message_key=s["message_key"])), event["scope"]["actor_id"])) for s in event["sources"]]
            require(all(key in state["sources"] and self.current(state, key) for key in keys), "stale_source")
            require(state["versions"].get(text_domain(event["scope"]), 1) == event["scope_version"], "scope_changed")
            state["jobs"][job_id] = dict(state="pending", snapshot={key: state["sources"][key]["epoch"] for key in keys})
            state["events"][event["event_id"]] = dict(digest=digest(event), job=job_id)
            state["turns"][turn_key] = dict(signature=signature, job=job_id)
            state["revision"] += 1
            return job_id
        return self.mutate(apply)


def verify_turn_order(turns, events):
    """One conversation's two-active-turn and globally ordered-send contract trace."""
    require(len({t["conversation_id"] for t in turns}) == 1, "split_conversation")
    order = [t["turn_id"] for t in sorted(turns, key=lambda t: t["turn_sequence"])]
    require(sorted(t["turn_sequence"] for t in turns) == list(range(1, len(turns) + 1)), "turn_sequence")
    active, closed, segments = set(), set(), {}
    for event in events:
        turn = event["turn_id"]
        require(turn in order, "unknown_turn")
        if event["kind"] == "start":
            require(turn not in active | closed and len(active) < 2, "global_two_turn_limit")
            active.add(turn)
        elif event["kind"] == "send":
            require(turn in active and all(previous in closed for previous in order[:order.index(turn)]), "global_send_order")
            require(event["segment_sequence"] == segments.get(turn, 0) + 1, "segment_order")
            segments[turn] = event["segment_sequence"]
        elif event["kind"] == "close":
            require(turn in active, "turn_not_active")
            active.remove(turn)
            closed.add(turn)
        else:
            require(False, "unknown_trace_action")
