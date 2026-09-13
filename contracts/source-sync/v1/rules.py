"""Pure cross-document contract checks. Not authentication, storage or product code."""
import hashlib
import json
from datetime import datetime


class Violation(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Violation(reason)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def physical_key(source):
    key = source["message_key"]
    return dict(channel=key["channel"], message_id=key["message_id"])


def selector(key, actor):
    return dict(key=key, actor_id=actor)


def event_header(event, turn):
    """Shared by legacy and actor-qualified paths; input cancellation is source-owned."""
    saved = turn["committed_event"]
    require(saved is not None, "dependency_unavailable")
    require({k: v for k, v in event.items() if k != "event_id"} == {k: v for k, v in saved.items() if k != "event_id"}, "owner_event")
    require(event["scope"] == turn["scope"] and event["aggregate_id"] == turn["turn_id"] and event["input_revision"] == turn["input_revision"] and event["sources"] == turn["input_sources"], "owner_event")
    require(event["conversation_id"] == event["scope"]["conversation_id"] and event["conversation_id"] is not None, "owner_event")
    require(turn["aggregate_version"] >= event["aggregate_version"], "owner_event")
    keys = [canonical(physical_key(source)) for source in event["sources"]]
    require(bool(keys) and len(set(keys)) == len(keys), "duplicate_sources")
    require(len({s["receipt_id"] for s in event["sources"]}) == len(keys), "receipt_alias")


def event_reality(event, values):
    realities = set(values)
    require(bool(realities) and realities <= {"real", "fictional"}, "reality")
    expected = next(iter(realities)) if len(realities) == 1 else "mixed"
    require(event["reality"] == expected, "reality")


def current_actor_sources(scope, sources, snapshot):
    """Join full P/A facts, never a remote current=True assertion."""
    admissions = {canonical(a["selector"]): a for a in snapshot["admissions"]}
    physicals = {canonical(p["key"]): p for p in snapshot["physicals"]}
    require(len(admissions) == len(snapshot["admissions"]), "duplicate_admissions")
    require(len(physicals) == len(snapshot["physicals"]), "duplicate_physicals")
    keys = [canonical(physical_key(source)) for source in sources]
    require(bool(keys) and len(set(keys)) == len(keys), "duplicate_sources")
    current = []
    for source, pk in zip(sources, keys):
        chosen = selector(physical_key(source), scope["actor_id"])
        admission = admissions.get(canonical(chosen))
        require(admission is not None and admission.get("state") != "missing" and admission["scope"] == scope and admission["source"] == source, "wrong_actor_receipt")
        require(source["archive_state"] == "pending" and source["locator"] is None, "archive_unverified")
        physical = physicals.get(pk)
        require(physical is not None and physical.get("state") != "missing", "dependency_unavailable")
        require(physical["state"] == "active" and physical["revision"] == source["message_key"]["revision"] and physical["physical_receipt_id"] == admission["physical_receipt_id"], "stale_source")
        require(physical["conversation_id"] == scope["conversation_id"] and physical["audience"] == scope["audience"], "physical_binding")
        current.append(physical)
    return current


def actor_event(event, turn, snapshot):
    event_header(event, turn)
    current = current_actor_sources(event["scope"], event["sources"], snapshot)
    event_reality(event, [p["classification"]["value"] for p in current])


def input_authority(request, authority, now):
    ingest = request["ingest"]
    data = ingest["input"]
    require(authority["request_id"] == request["request_id"] and authority["request_digest"] == digest(request) and authority["ingest_digest"] == digest(ingest), "input_authority")
    require(authority["verified_account"] == data["author"] and authority["verified_channel"] == data["message_key"]["channel"] and authority["input_digest"] == digest(data) and authority["origin_ref"] == ingest["command"]["origin"]["assertion_ref"], "input_authority")
    require(datetime.fromisoformat(authority["expires_at"]) > datetime.fromisoformat(now) and datetime.fromisoformat(ingest["command"]["deadline_at"]) > datetime.fromisoformat(now), "expired_input")
    contexts = {c["allowed_scope"]["actor_id"]: c for c in authority["actor_contexts"]}
    require(len(contexts) == len(authority["actor_contexts"]), "ambiguous_grant")
    return contexts


def context_allows(context, actor, ingest, person, conversation, audience, now, ingress_service):
    if context is None:
        return False
    scope = context["allowed_scope"]
    return context["issuer"] == "platform" and context["authenticated_service"] == ingress_service and context["audience_service"] == "companion" and not context["revoked"] and context["verified_account"] == ingest["input"]["author"] and context["verified_channel"] == ingest["input"]["message_key"]["channel"] and scope["actor_id"] == actor and scope["person_id"] in (None, person) and scope["conversation_id"] in (None, conversation) and scope["audience"] == audience and datetime.fromisoformat(context["expires_at"]) > datetime.fromisoformat(now)


def fanout_mapping(ingest, response, identity, access_request, authority, now, frozen_route=None, ingress_service="platform"):
    """Map only Core's inline admission facts; no Memory/source lookup cycle."""
    require(access_request["ingest"] == ingest, "input_authority")
    contexts = input_authority(access_request, authority, now)
    require(response["request_id"] == ingest["command"]["request_id"] and response["request_digest"] == digest(ingest), "mapping_response")
    if ingest["input"]["kind"] == "retract":
        expected, route_version = [], authority["routing_version"]
    elif frozen_route is not None:
        require(frozen_route["semantic_digest"] == digest(dict(input=ingest["input"], target_actor_ids=ingest["target_actor_ids"])), "frozen_route")
        expected, route_version = sorted(frozen_route["effective_actor_ids"]), frozen_route["routing_version"]
        require(not ingest["target_actor_ids"] or expected == sorted(ingest["target_actor_ids"]), "frozen_route")
    else:
        expected = sorted(ingest["target_actor_ids"] or authority["default_actor_ids"])
        route_version = authority["routing_version"]
    require(len(set(expected)) == len(expected) and response["effective_actor_ids"] == expected and response["routing_version"] == route_version and response["routing_state"] == ("routed" if expected else "unrouted"), "actor_coverage")
    actors = [r["actor_id"] for r in response["outcomes"]]
    require(len(set(actors)) == len(actors) and set(actors) == set(expected), "actor_coverage")
    accepted = [r for r in response["outcomes"] if r["state"] != "forbidden"]
    require(not accepted or (identity is not None and response["person_id"] == identity["person_id"]), "mapping_response")
    require(len({r["receipt"]["receipt_id"] for r in accepted}) == len(accepted), "receipt_alias")
    for result in response["outcomes"]:
        if result["state"] == "forbidden":
            require(result["receipt"] is None and result["admission"] is None, "actor_admission")
            continue
        actor, receipt, admission = result["actor_id"], result["receipt"], result["admission"]
        require(admission is not None, "actor_admission")
        require(context_allows(contexts.get(actor), actor, ingest, identity["person_id"], response["conversation_id"], authority["audience"], now, ingress_service), "actor_authorization")
        scope = dict(actor_id=actor, person_id=identity["person_id"], conversation_id=response["conversation_id"], audience=authority["audience"])
        require(receipt["person_id"] == identity["person_id"] and receipt["conversation_id"] == response["conversation_id"] and receipt["request_id"] == ingest["command"]["request_id"] and receipt["collection_key"] == dict(channel=ingest["input"]["message_key"]["channel"], author=ingest["input"]["author"]), "mapping_response")
        require(admission["selector"] == selector(physical_key(ingest["input"]), actor) and admission["scope"] == scope and admission["binding_version"] == identity["binding_version"] and admission["source"]["message_key"] == ingest["input"]["message_key"] and admission["source"]["receipt_id"] == receipt["receipt_id"] and admission["physical_receipt_id"] == response["physical_receipt_id"] and admission["accepted_at"] == receipt["accepted_at"], "actor_admission")
        require(receipt["deduplicated"] == (result["state"] == "duplicate"), "actor_admission")
        require(admission["source"]["archive_state"] == "pending" and admission["source"]["locator"] is None, "archive_unverified")
        if result["state"] == "accepted":
            require(admission["accepted_origin"]["assertion_ref"] == contexts[actor]["assertion_ref"], "actor_admission")


def current_access(request, response, snapshot, now=None):
    require(response["request_id"] == request["request_id"] and response["request_digest"] == digest(request), "correlation")
    facts = {canonical(a["selector"]): a for a in snapshot["admissions"]}
    requested = {canonical(a["selector"]): a for a in request["admissions"]}
    grants = {canonical(g["selector"]): g for g in response["grants"]}
    require(len(facts) == len(snapshot["admissions"]) and len(requested) == len(request["admissions"]) and len(grants) == len(response["grants"]) and facts == requested and set(grants) == set(facts), "actor_coverage")
    physicals = {canonical(p["key"]): p for p in snapshot["physicals"]}
    require(len(physicals) == len(snapshot["physicals"]), "duplicate_physicals")
    for key, grant in grants.items():
        admission = facts[key]
        require(grant["admission_digest"] == digest(admission), "admission_binding")
        require(grant["scope"] == admission["scope"] and grant["binding_version"] == admission["binding_version"] and grant["account"] == physicals[canonical(admission["selector"]["key"])]["author"], "actor_binding")
    context = response["viewer_context"]
    if request["viewer"] is None:
        require(context is None, "viewer_actor")
    else:
        require(context is not None and context["issuer"] == "platform" and context["allowed_scope"] == request["viewer"]["scope"] and context["assertion_ref"] == request["viewer"]["origin"]["assertion_ref"] and context["audience_service"] == "memory" and context["authenticated_service"] == "companion" and not context["revoked"], "viewer_actor")
        require(now is not None and datetime.fromisoformat(context["expires_at"]) > datetime.fromisoformat(now), "viewer_actor")
    return grants


def source_snapshot(request, response):
    require(response["request_id"] == request["request_id"] and response["request_digest"] == digest(request), "correlation")
    admissions = {canonical(a["selector"]): a for a in response["admissions"]}
    physicals = {canonical(p["key"]): p for p in response["physicals"]}
    require(len(admissions) == len(response["admissions"]) and set(admissions) == {canonical(s) for s in request["selectors"]}, "actor_coverage")
    require(len(physicals) == len(response["physicals"]) and set(physicals) == {canonical(s["key"]) for s in request["selectors"]}, "physical_coverage")
    require(len({t["turn_id"] for t in response["turns"]}) == len(response["turns"]) and {t["turn_id"] for t in response["turns"]} == set(request["turn_ids"]), "turn_coverage")
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
        require(admission["selector"]["actor_id"] == admission["scope"]["actor_id"] and physical_key(admission["source"]) == admission["selector"]["key"], "actor_binding")
        physical = physicals[canonical(admission["selector"]["key"])]
        require(admission["scope"]["conversation_id"] == physical["conversation_id"] and admission["scope"]["audience"] == physical["audience"], "physical_binding")
        require(admission["source"]["archive_state"] == "pending" and admission["source"]["locator"] is None, "archive_unverified")
        require(admission["source"]["receipt_id"] not in receipts, "receipt_alias")
        receipts.add(admission["source"]["receipt_id"])
    require(all(t.get("state") != "missing" for t in response["turns"]), "dependency_unavailable")
    return physicals, admissions


def background_check(request, response, snapshot):
    require(response["request_id"] == request["request_id"] and response["request_digest"] == digest(request) and response["scope"] == request["scope"] and response["version_domain"] == "text-dialogue/v1", "check_response")
    turns = {t["turn_id"]: t for t in snapshot["turns"]}
    require(len(turns) == len(snapshot["turns"]), "turn_coverage")
    turn = turns.get(request["turn_id"])
    require(turn is not None and turn.get("state") != "missing" and turn["scope"] == request["scope"] and turn["input_revision"] == request["input_revision"] and turn["input_sources"] == request["sources"], "owner_event")
    current_actor_sources(request["scope"], request["sources"], snapshot)


def confirmation(proof, request, context, binding_version, now):
    semantic = {k: v for k, v in request.items() if k not in {"command", "query"}}
    require(context["issuer"] == "platform" and context["audience_service"] == "memory" and not context["revoked"] and datetime.fromisoformat(context["expires_at"]) > datetime.fromisoformat(now), "confirmation_binding")
    require(not proof["consumed"] and proof["confirmation_ref"] == request["confirmation_ref"] and proof["record_id"] == request["record_id"] and proof["semantic_digest"] == digest(semantic) and proof["expected_version"] == request["expected_version"] and proof["account"] == context["verified_account"] and proof["scope"] == context["allowed_scope"] and proof["binding_version"] == binding_version and datetime.fromisoformat(proof["expires_at"]) > datetime.fromisoformat(now), "confirmation_binding")


def sync_barrier(observation, previous_heads=None):
    source_snapshot(observation["request"], observation["snapshot"])
    current_access(observation["access_request"], observation["access"], observation["snapshot"], observation.get("now"))
    require(observation["snapshot"]["head"] == observation["final_head"], "moving_owner")
    if previous_heads is not None:
        for owner, head in (("core", observation["snapshot"]["head"]), ("platform", observation["access"]["head"])):
            old = previous_heads[owner]
            require(head["generation"] == old["generation"] and head["sequence"] >= old["sequence"], "recovery_required")
