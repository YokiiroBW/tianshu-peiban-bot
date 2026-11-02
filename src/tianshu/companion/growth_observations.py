"""Bounded reflection views of action receipts, with explicit unassessed originals."""

from dataclasses import replace
import json

from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind


MATERIAL_PROTOCOL = "growth.observation.v1"
OBSERVATION_BYTES = 4096
# These are complete semantic units, not a second copy of provider payloads,
# cursors, executable continuation arguments or duplicate source locators.
SEMANTIC_FIELDS = (
    "summary", "text", "observation", "analysis", "consumption", "consistency",
    "experience_kind", "software_action", "activity_mode", "content_completed",
    "generated_at", "title", "content_key",
)
RECEIPT_FIELDS = ("capability_id", "receipt_id", "execution", "effect", "delivery", "error_code", "read_basis", "experience_eligible")
TRANSPORT_FIELDS = frozenset({
    *RECEIPT_FIELDS, "source_refs", "source", "original_source", "locator",
    "continuation", "discovery_continuation", "next_actions", "exploration",
})


def size(value):
    return len(json.dumps(wire(value), ensure_ascii=False).encode("utf-8"))


def episode_material(episode):
    """The original Episode and its source closure remain authoritative and intact.

    Reflection can review an action observation without assessing every returned
    document. Whole semantic units either fit or remain explicitly unassessed;
    no truncated body is presented as the complete original evidence.
    """
    actual = episode.actual_result
    if episode.origin_kind != OriginKind.TOOL_RESULT or not actual:
        return wire(episode)
    row = wire(replace(episode, actual_result=None, summary="", consolidation={}))
    included, missing, omitted = [], set(), 0
    remaining = OBSERVATION_BYTES

    def unit(path, value):
        nonlocal remaining, omitted
        cost = size({path: value})
        if cost > remaining:
            omitted += 1
            missing.add("action_content_not_assessed" if path == "actual_result.content" else "action_semantic_unit_not_assessed")
            return False
        remaining -= cost
        included.append(path)
        return True

    observed = {}
    for field in RECEIPT_FIELDS:
        if field in actual and unit("actual_result." + field, actual[field]):
            observed[field] = wire(actual[field])
    if unit("summary", episode.summary):
        row["summary"] = episode.summary
    else:
        row["summary"] = "Action receipt observed; the full summary is retained in the original Episode."

    discovery = actual.get("read_basis") == "discovery_metadata" or actual.get("capability_id") == "content.discover"
    if discovery:
        # Candidate descriptions and executable next-actions do not establish
        # that a source body was read. This is the complete discovery event view.
        candidates = actual.get("candidates") or ()
        next_actions = actual.get("next_actions") or ()
        observed.update(read_basis="discovery_metadata", experience_eligible=False,
            observation={"kind": "discovery", "candidate_count": len(candidates),
                "next_action_count": len(next_actions),
                "continuation_available": bool(actual.get("continuation") or actual.get("discovery_continuation")),
                "body_read": False})
        if actual.get("sources"):
            states = [item.get("state") for item in actual["sources"] if isinstance(item, dict)]
            known = {"ok", "partial", "unavailable", "disabled", "unsupported"}
            observed["observation"]["source_states"] = {state: states.count(state) for state in sorted(known) if state in states}
            unknown = sum(state not in known for state in states)
            if unknown:
                observed["observation"]["source_states"]["unknown"] = unknown
            if any(state != "ok" for state in states):
                missing.add("discovery_source_coverage_partial")
        for field in ("intention", "query", "stop_reason"):
            value = (actual.get("exploration") or {}).get(field)
            if value is not None and unit("actual_result.exploration." + field, value):
                observed["observation"][field] = wire(value)
        missing.add("discovery_metadata_only_body_not_read")
        omitted += len(candidates)
    else:
        handled = set(TRANSPORT_FIELDS)
        for field in SEMANTIC_FIELDS:
            if field not in actual:
                continue
            handled.add(field)
            if unit("actual_result." + field, actual[field]):
                observed[field] = wire(actual[field])
        if "content" in actual:
            handled.add("content")
            if unit("actual_result.content", actual["content"]):
                observed["content"] = wire(actual["content"])
        other = len(actual.keys() - handled)
        omitted += other
        if other:
            missing.add("action_result_details_not_assessed")
        observed["content_evidence_included"] = "content" in observed or "text" in observed

    # Receipt coverage describes the external operation. Reflection coverage is
    # separate: consuming this observation does not certify omitted originals.
    missing.update(episode.coverage.missing)
    if not episode.coverage.complete:
        missing.add("action_source_coverage_partial")
    row["actual_result"] = observed
    row["reflection_coverage"] = {
        "representation": "semantic_action_observation", "complete": not missing,
        "missing": sorted(missing), "included_units": included,
        "omitted_units": omitted, "whole_units": True,
        "original": {"episode_id": episode.episode_id, "revision": episode.revision,
                     "fields": ["summary", "actual_result"], "retrieval": "episodes_by_refs_or_source_trace"},
    }
    return row


def material_coverages(episodes):
    return {item.episode_id: row["reflection_coverage"] for item in episodes
            if "reflection_coverage" in (row := episode_material(item))}
