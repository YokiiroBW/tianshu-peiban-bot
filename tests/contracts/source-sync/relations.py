"""Offline relations over authenticated-owner *synthetic observations*.

No token authentication, TLS, network client, product database or NLP entailment here.
Producers must construct these observations from their own trusted application services.
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from release_support import RULES

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "docs/development/candidates/source-sync"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


Violation = RULES.Violation


def require(condition, code):
    if not condition:
        raise Violation(code)


def metadata(fact):
    return {k: v for k, v in fact.items() if k != "content"}


def source_key(source):
    key = source["message_key"]
    return canonical({"channel": key["channel"], "message_id": key["message_id"]})


def by_key(facts):
    result = {canonical(f["key"]): f for f in facts}
    require(len(result) == len(facts), "coverage")
    return result


def schema_registry():
    registry = Registry()
    paths = sorted((ROOT / "contracts/text-dialogue/v1/schemas").glob("*.json")) + [PACKAGE / "schema.json"]
    schemas = {}
    for path in paths:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        schemas[path.stem] = schema
    # Resolve every definition reference locally, including those not exercised by a document.
    def walk(value, resolver):
        if isinstance(value, dict):
            if "$ref" in value:
                resolver.lookup(value["$ref"])
            for child in value.values():
                walk(child, resolver)
        elif isinstance(value, list):
            for child in value:
                walk(child, resolver)
    for schema in schemas.values():
        walk(schema, registry.resolver(schema["$id"]))
    return schemas, registry


SCHEMAS, REGISTRY = schema_registry()


def validator(selector):
    module, name = selector.split("#") if "#" in selector else ("schema", selector)
    return Draft202012Validator(
        {"$ref": SCHEMAS[module]["$id"] + "#/$defs/" + name},
        registry=REGISTRY, format_checker=FormatChecker(),
    )


def correlate(request, response):
    require(response["request_id"] == request["request_id"] and response["request_digest"] == digest(request), "correlation")


def check_core(request, response):
    correlate(request, response)
    facts = by_key(response["facts"])
    keys = [canonical(k) for k in request["keys"]]
    require(len(set(keys)) == len(keys) and set(facts) == set(keys), "coverage")
    turns = {t["turn_id"]: t for t in response["turns"]}
    require(len(turns) == len(response["turns"]) and len(set(request["turn_ids"])) == len(request["turn_ids"]) and set(turns) == set(request["turn_ids"]), "coverage")
    channel_actors = {}
    for fact in facts.values():
        require(fact["state"] != "missing", "dependency_unavailable")
        channel = canonical(fact["key"]["channel"])
        actor = fact["scope"]["actor_id"]
        require(channel_actors.setdefault(channel, actor) == actor, "unsupported_multi_actor")
        source = fact["source"]
        require(source_key(source) == canonical(fact["key"]), "source_binding")
        require(source["archive_state"] == "pending" and source["locator"] is None, "archive_unverified")
        require(fact["author"]["namespace"] == fact["key"]["channel"]["namespace"], "source_binding")
        if request["include_content"]:
            content = fact["content"]
            require(content is not None and digest(content) == fact["content_digest"], "content_digest")
            require(content["message_key"] == source["message_key"] and content["author"] == fact["author"] and content["kind"] == fact["kind"], "source_binding")
            require(all(actor == fact["scope"]["actor_id"] for actor in content["target_actor_ids"]), "source_binding")
        else:
            require(fact["content"] is None, "metadata_body")
    require(all(t.get("state") != "missing" for t in turns.values()), "dependency_unavailable")


def check_access(request, response, core, now=None):
    correlate(request, response)
    admissions, grants, facts = by_key(request["admissions"]), by_key(response["facts"]), by_key(core["facts"])
    require(set(admissions) == set(grants) == set(facts), "coverage")
    for key, admission in admissions.items():
        require(admission["content"] is None, "metadata_body")
        require(metadata(admission) == metadata(facts[key]), "admission_binding")
        grant = grants[key]
        require(grant["admission_digest"] == digest(metadata(admission)), "admission_binding")
        if grant["state"] == "allowed":
            require(grant["account"] == admission["author"] and grant["scope"] == admission["scope"] and grant["binding_version"] == admission["binding_version"] and grant["channel"] == admission["key"]["channel"], "access_binding")
    viewer, context = request["viewer"], response["viewer_context"]
    if viewer is None:
        require(context is None, "viewer_binding")
    else:
        # Issuer is fixed by deployment; common.origin intentionally carries only assertion_ref.
        require(context is not None and context["issuer"] == "platform" and context["assertion_ref"] == viewer["origin"]["assertion_ref"] and context["allowed_scope"] == viewer["scope"] and context["authenticated_service"] == "companion" and context["audience_service"] == "memory" and not context["revoked"], "viewer_binding")
        require(now is not None and datetime.fromisoformat(context["expires_at"]) > datetime.fromisoformat(now), "viewer_binding")


def check_barrier(values):
    check_core(values["core_request"], values["core"])
    check_access(values["access_request"], values["access"], values["core"])
    check_core(values["head_request"], values["head"])
    require(values["core"]["head"] == values["head"]["head"], "moving_owner")


def check_turn(request, turn, facts):
    require(turn["turn_id"] == request["turn_id"] and turn["scope"] == request["scope"] and turn["input_revision"] == request["input_revision"] and turn["input_sources"] == request["sources"], "owner_input")
    # Reply lifecycle never revokes accepted input; current source facts do that.
    current = by_key(facts)
    require(len({source_key(s) for s in request["sources"]}) == len(request["sources"]), "stale_source")
    for source in request["sources"]:
        fact = current.get(source_key(source))
        require(fact is not None and fact["state"] == "active" and fact["source"] == source and fact["scope"] == request["scope"], "stale_source")


def check_event(event, turn, facts):
    RULES.event_header(event, turn)
    check_turn({"turn_id": event["aggregate_id"], "scope": event["scope"], "input_revision": event["input_revision"], "sources": event["sources"]}, turn, facts)
    current = by_key(facts)
    RULES.event_reality(event, [current[source_key(s)]["classification"]["value"] for s in event["sources"]])


def check_drafts(commit, event, facts):
    sources = {source_key(s): s for s in event["sources"]}
    current = by_key(facts)
    for draft in commit["drafts"]:
        require(draft["scope"] == event["scope"] and draft["scope"]["audience"] == "self_private", "sharing_approval_required")
        require(draft["relationship_delta"] is None or draft["category"] == "relationship", "draft_category")
        for unit in draft["units"]:
            for source in unit["sources"]:
                key = source_key(source)
                require(sources.get(key) == source, "draft_source")
                require(current[key]["classification"]["value"] == unit["reality"], "reality")


def check_confirmation(proof, request, account, scope, binding_version, now):
    semantic = {k: v for k, v in request.items() if k not in {"command", "query"}}
    require(not proof["consumed"] and proof["confirmation_ref"] == request["confirmation_ref"] and proof["record_id"] == request["record_id"] and proof["semantic_digest"] == digest(semantic) and proof["expected_version"] == request["expected_version"] and proof["account"] == account and proof["scope"] == scope and proof["binding_version"] == binding_version and datetime.fromisoformat(proof["expires_at"]) > datetime.fromisoformat(now), "confirmation_binding")


def check_inherited(checks, versions):
    """TS-021 dependency identity includes both domain and exact original requester scope."""
    for check in checks:
        key = (check["version_domain"], canonical(check["scope"]))
        require(versions.get(key) == check["scope_version"], "inherited_scope_changed")
