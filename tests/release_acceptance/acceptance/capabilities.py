"""Read the TS-108 public capability facts; HTTP 200 alone proves no memory write."""

from .transport import Missing, check


def disabled_facts(client, token):
    status, body = client.request(
        "GET",
        "/internal/v1/runtime/capabilities",
        headers={"Authorization": "Bearer " + token},
    )
    if status in {404, 503}:
        raise Missing("companion_disable_capability_unavailable")
    check(
        status == 200
        and isinstance(body, dict)
        and type(body.get("schema_version")) is int
        and body.get("schema_version") == 1
        and body.get("service") == "companion",
        "capability_response_invalid",
    )
    memory = body.get("automatic_memory_candidates", {})
    expected = {
        "enabled": False,
        "generation": "disabled",
        "submission": "paused",
        "backlog_policy": "preserve",
        "memory_write_verification": "not_verified",
    }
    check(
        all(
            memory.get(k) == v and type(memory.get(k)) is type(v)
            for k, v in expected.items()
        ),
        "automatic_memory_not_safely_disabled",
    )
    retained = memory.get("retained_outbox")
    check(
        isinstance(retained, dict)
        and set(retained) == {"pending", "blocked_scope", "submitting", "unknown"}
        and all(type(v) is bool for v in retained.values()),
        "retained_outbox_state_invalid",
    )
    check(
        body.get("chat_audit") == {"enabled": False, "state": "not_integrated"}
        and body["chat_audit"]["enabled"] is False,
        "chat_audit_disable_state_invalid",
    )
    return {
        "stage": "disabled_verified",
        "generation": "disabled",
        "submission": "paused",
        "retained_outbox": retained,
        "memory_write_proven": False,
        "chat_archive_proven": False,
        "basis": "product_public_capabilities",
    }
