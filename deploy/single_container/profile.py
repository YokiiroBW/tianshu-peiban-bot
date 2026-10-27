"""Relocate native filesystem references without changing business identities."""

import json
import re

MODULES = ("platform", "gateway", "memory", "knowledge", "companion")
PORTS = {
    "platform": 8443,
    "gateway": 8443,
    "memory": 8130,
    "knowledge": 18135,
    "companion": 8765,
    "onebot": 18765,
    "obs-guard": 8445,
    "obs-vector": 9598,
    "obs-loki": 3100,
    "obs-prometheus": 9090,
    "obs-grafana": 3000,
}
OBSERVABILITY = ("obs-loki", "obs-guard", "obs-vector", "obs-prometheus", "obs-grafana")
APPLICATION = "/opt/tianshu/application"
PYTHON = "/opt/tianshu-venv/bin/python"
PATH_ENVIRONMENT = frozenset(
    {
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "TIANSHU_LOG_DIR",
        "TIANSHU_MEMORY_CONFIG",
        "TIANSHU_HEALTHCHECK_CA",
        "TIANSHU_ONEBOT_TOKEN_FILE",
        "TIANSHU_COMPANION_CONFIG",
        "TIANSHU_COMPANION_DATABASE",
        "TIANSHU_CONTRACTS",
    }
)
JSON_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')


def identity_field(key, value):
    """These strings are values/identities even when they resemble a local path."""
    key = key.lower()
    return (
        key in {"headers", "authorization"}
        or isinstance(value, str)
        and (
            key
            in {
                "namespace",
                "password",
                "secret",
                "token",
                "api_key",
                "model",
                "model_id",
                "model_name",
            }
            or key.endswith(("_namespace", "_password", "_secret", "_token"))
        )
    )


def path_map(name):
    if name in OBSERVABILITY:
        return {
            "/opt/observability/vocabulary.json": "/config/observability/vocabulary.json",
            "/opt/observability": APPLICATION + "/deploy/observability",
            "/etc/tianshu": "/config/observability",
            "/etc/grafana/provisioning": "/config/observability/provisioning",
            "/etc/grafana/dashboards": "/config/observability/dashboards",
            "/run/tls": "/config/observability/tls",
            "/run/secrets": "/config/observability/secrets",
            "/contracts/diagnostics/v1": "/config/contracts/diagnostics/v1",
            **{
                f"/var/lib/{part}": f"/data/observability/{part}"
                for part in ("vector", "loki", "prometheus", "grafana", "guard")
            },
            **{f"/sources/{part}": f"/data/logs/{part}" for part in MODULES},
            **{
                f"/capacity/{part}": f"/data/observability/{part}"
                for part in ("vector", "loki")
            },
        }
    result = {
        "/etc/tianshu": f"/config/modules/{name}",
        "/contracts": f"/config/contracts/{name}",
        "/var/log/tianshu": f"/data/logs/{name}",
    }
    if name in {"platform", "gateway"}:
        result["/var/lib/tianshu"] = f"/data/modules/{name}"
    elif name in {"memory", "knowledge"}:
        result["/srv/tianshu"] = f"/data/modules/{name}"
    elif name in {"companion", "onebot"}:
        result["/data"] = f"/data/modules/{name}"
    if name == "onebot":
        result["/run/secrets"] = "/config/modules/onebot/secrets"
    return result


def adapt(value, name):
    """Only native path literals and the relocated guard listener change."""
    if isinstance(value, dict):
        return {
            key: (
                {
                    field: adapt(content, name)
                    if field == "credentials_file"
                    else content
                    for field, content in item.items()
                }
                if key.lower() == "authorization" and isinstance(item, dict)
                else item
                if identity_field(key, item)
                else adapt(item, name)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [adapt(item, name) for item in value]
    if not isinstance(value, str):
        return value
    if value == "/config" or value.startswith(
        (
            "/config/",
            "/data/modules/",
            "/data/logs/",
            "/data/observability/",
            APPLICATION + "/",
        )
    ):
        return value
    if name in OBSERVABILITY:
        if value == "obs-guard:8443":
            return "obs-guard:8445"
        prefix = "https://obs-guard:8443"
        if value == prefix or value.startswith(prefix + "/"):
            return "https://obs-guard:8445" + value[len(prefix) :]
    for source, target in sorted(
        path_map(name).items(), key=lambda item: -len(item[0])
    ):
        if value == source or value.startswith(source + "/"):
            return target + value[len(source) :]
    return value


def owner(path):
    parts = path.split("/")
    if len(parts) >= 3 and parts[0] == "modules" and parts[1] in (*MODULES, "onebot"):
        return parts[1]
    if parts[0] == "observability":
        return "obs-guard"
    if len(parts) == 2 and parts[0] == "private" and parts[1].endswith(".env"):
        name = parts[1][:-4]
        return name if name in (*MODULES, "onebot", *OBSERVABILITY) else None
    return None


def document(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def relocate_json(value, name):
    """Keep original JSON whitespace and all unchanged string token bytes."""
    text = value.decode("utf-8") if isinstance(value, bytes) else value

    class ObjectPairs(list):
        pass

    # Pair order retains even duplicate JSON keys and distinguishes keys from values.
    parsed = json.loads(text, object_pairs_hook=ObjectPairs)

    def strings(item, protected=False, authorization=False):
        if isinstance(item, ObjectPairs):
            for key, field in item:
                yield key
                # Prometheus credentials_file is a path, while credentials and
                # every other authorization value retain their literal bytes.
                if authorization:
                    yield from strings(field, protected or key != "credentials_file")
                elif (
                    not protected
                    and key.lower() == "authorization"
                    and isinstance(field, ObjectPairs)
                ):
                    yield from strings(field, authorization=True)
                else:
                    yield from strings(field, protected or identity_field(key, field))
        elif isinstance(item, list):
            for field in item:
                yield from strings(field, protected)
        elif isinstance(item, str):
            yield item if protected else adapt(item, name)

    relocated_strings = iter(strings(parsed))

    def replace(match):
        original = json.loads(match[0])
        relocated = next(relocated_strings)
        return (
            match[0]
            if original == relocated
            else json.dumps(relocated, ensure_ascii=False)
        )

    return JSON_STRING.sub(replace, text).encode("utf-8")


def relocate_environment(value, name):
    text = value.decode("utf-8") if isinstance(value, bytes) else value
    lines = []
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        ending = line[len(content) :]
        key, separator, field = content.partition("=")
        if separator and key in PATH_ENVIRONMENT:
            quote = (
                field[0]
                if len(field) >= 2 and field[0] in "'\"" and field[-1] == field[0]
                else ""
            )
            field = quote + adapt(field[1:-1] if quote else field, name) + quote
        lines.append(key + separator + field + ending if separator else line)
    return "".join(lines).encode("utf-8")
