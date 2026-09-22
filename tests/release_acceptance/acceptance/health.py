"""Per-product readiness vocabulary, read from the fixed TS100-103 source baselines."""

from .transport import check

CHECKS = {
    "platform": {
        "config",
        "contract",
        "store",
        "sidecars",
        "web_static",
        "tls",
        "credentials",
        "logging",
        "runtime",
    },
    "companion": {"configuration", "logs", "runtime", "dependencies"},
    "memory": {
        "configuration",
        "contract",
        "database",
        "guard",
        "mode",
        "log",
        "assembled",
        "owner",
        "remote",
    },
    "gateway": {
        "configuration",
        "contracts",
        "runtime",
        "ledger",
        "logging",
        "platform",
        "model",
        "native",
    },
}
OPTIONAL = {
    "platform": set(),
    "companion": {"dependencies"},
    "memory": {"remote"},
    "gateway": {"platform", "model", "native"},
}
LOG_KEY = {
    "platform": "logging",
    "companion": "logs",
    "memory": "log",
    "gateway": "logging",
}


def validate_ready(role, status, body):
    check(
        status == 200
        and isinstance(body, dict)
        and set(body) == {"status", "service", "checks"}
        and body["status"] == "ready"
        and body["service"] == role,
        "service_not_ready",
    )
    values = body["checks"]
    check(
        isinstance(values, dict) and set(values) == CHECKS[role],
        "readiness_check_vocabulary",
    )
    check(
        all(
            value in {"ok", "failed", "not_configured", "not_verified", "non_durable"}
            for value in values.values()
        ),
        "readiness_check_value",
    )
    check(
        all(values[name] == "ok" for name in CHECKS[role] - OPTIONAL[role]),
        "readiness_false_success",
    )
    return values
