"""Explicit composition input; product configuration remains with its owner."""

import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from .exchange import authority
from .process import ENVIRONMENT_KEY, environment_for

MODULES = ("platform", "gateway", "memory", "knowledge", "companion")
SYSTEM_ENVIRONMENT = frozenset(
    {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "TMPDIR",
        "COMSPEC",
        "PATHEXT",
        "LANG",
        "LC_ALL",
        "TZ",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONUNBUFFERED",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "HOMEDRIVE",
        "HOMEPATH",
        "USER",
        "LOGNAME",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
    }
)


def absolute_path(value):
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError("Host configuration needs an absolute path")
    return value


def module_environment(spec, *, environ=None):
    """Do not inherit another module's credentials or storage overrides."""
    source = os.environ if environ is None else environ
    base = {
        key: value for key, value in source.items() if key.upper() in SYSTEM_ENVIRONMENT
    }
    return environment_for(spec, base_environment=base)


def load_settings(path):
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    fields = {
        "version",
        "modules",
        "state_file",
        "shutdown_seconds",
        "probe_interval_seconds",
        "frontend",
    }
    if (
        not isinstance(raw, dict)
        or set(raw) - fields
        or type(raw.get("version")) is not int
        or raw["version"] != 1
    ):
        raise ValueError("Invalid business host configuration")
    modules = raw.get("modules")
    if (
        not isinstance(modules, dict)
        or set(MODULES) - set(modules)
        or set(modules) - {*MODULES, "onebot"}
    ):
        raise ValueError("The business host needs all five explicit modules")
    result = dict(raw)
    result["state_file"] = absolute_path(raw.get("state_file"))
    for key, default in (("shutdown_seconds", 30), ("probe_interval_seconds", 5)):
        value = raw.get(key, default)
        if type(value) not in (int, float) or not 0 < value <= 600:
            raise ValueError("Invalid business host timing")
        result[key] = value
    result["modules"] = {}
    for name in (*MODULES, *(("onebot",) if "onebot" in modules else ())):
        spec = modules[name]
        allowed = {
            "settings",
            "origin",
            "env",
            "env_file",
            "contracts",
            "log_directory",
            "client",
            "data_directory",
            "token_env",
            "token_file",
            "listen_host",
            "flow_query",
        }
        if not isinstance(spec, dict) or set(spec) - allowed:
            raise ValueError("Invalid business module configuration")
        item = dict(spec)
        item["settings"] = absolute_path(spec.get("settings"))
        origin = spec.get("origin")
        authority(origin)
        parsed = urlsplit(origin)
        if parsed.path not in {"", "/"} or parsed.query:
            raise ValueError("Module origin must not contain a path or query")
        for key in ("contracts", "log_directory"):
            if key in spec:
                item[key] = absolute_path(spec[key])
        if "flow_query" in spec:
            query = spec["flow_query"]
            if (
                name != "platform"
                or not isinstance(query, dict)
                or set(query) != {"url", "settings"}
                or not isinstance(query["url"], str)
            ):
                raise ValueError("Invalid platform flow query")
            target = urlsplit(query["url"])
            if target.scheme != "https" or target.path not in {"", "/"} or target.query:
                raise ValueError("Flow query needs its explicit HTTPS origin")
            authority(query["url"])
            item["flow_query"] = {**query, "settings": absolute_path(query["settings"])}
        files = spec.get("env_file", [])
        files = [files] if isinstance(files, str) else files
        if not isinstance(files, list):
            raise TypeError("Module environment files must be a list")
        item["env_file"] = [absolute_path(value) for value in files]
        values = spec.get("env", {})
        if not isinstance(values, dict) or not all(
            isinstance(key, str)
            and ENVIRONMENT_KEY.fullmatch(key)
            and isinstance(value, str)
            and "\0" not in value
            for key, value in values.items()
        ):
            raise ValueError("Invalid module environment values")
        item["env"] = dict(values)
        if name == "onebot":
            ipaddress.ip_address(spec.get("listen_host", "0.0.0.0"))
            item["data_directory"] = absolute_path(spec.get("data_directory"))
            if ("token_env" in spec) == ("token_file" in spec):
                raise ValueError("OneBot needs exactly one token source")
            if "token_file" in spec:
                item["token_file"] = absolute_path(spec["token_file"])
            elif not isinstance(
                spec["token_env"], str
            ) or not ENVIRONMENT_KEY.fullmatch(spec["token_env"]):
                raise ValueError("Invalid OneBot token reference")
        if name == "knowledge" and (
            not isinstance(spec.get("client"), str) or not spec["client"].strip()
        ):
            raise ValueError("Knowledge needs its existing registered client")
        result["modules"][name] = item
    if "frontend" in raw:
        # With native WebAccess this is an additional TLS SDK/API listener;
        # otherwise it remains the original bootstrap/public TLS listener.
        front = raw["frontend"]
        if not isinstance(front, dict) or set(front) != {
            "host",
            "port",
            "tls_certfile",
            "tls_keyfile",
        }:
            raise ValueError("Invalid public frontend")
        ipaddress.ip_address(front["host"])
        if type(front["port"]) is not int or not 1 <= front["port"] <= 65535:
            raise ValueError("Invalid frontend port")
        result["frontend"] = {
            **front,
            "tls_certfile": absolute_path(front["tls_certfile"]),
            "tls_keyfile": absolute_path(front["tls_keyfile"]),
        }
    return result
