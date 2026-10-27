"""Explicit composition input; product configuration remains with its owner."""

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
    }
    if (
        not isinstance(raw, dict)
        or set(raw) - fields
        or type(raw.get("version")) is not int
        or raw["version"] != 1
    ):
        raise ValueError("Invalid business host configuration")
    modules = raw.get("modules")
    if not isinstance(modules, dict) or set(modules) != set(MODULES):
        raise ValueError("The business host needs all five explicit modules")
    result = dict(raw)
    result["state_file"] = absolute_path(raw.get("state_file"))
    for key, default in (("shutdown_seconds", 30), ("probe_interval_seconds", 5)):
        value = raw.get(key, default)
        if type(value) not in (int, float) or not 0 < value <= 600:
            raise ValueError("Invalid business host timing")
        result[key] = value
    result["modules"] = {}
    for name in MODULES:
        spec = modules[name]
        allowed = {
            "settings",
            "origin",
            "env",
            "env_file",
            "contracts",
            "log_directory",
            "client",
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
        if name == "knowledge" and (
            not isinstance(spec.get("client"), str) or not spec["client"].strip()
        ):
            raise ValueError("Knowledge needs its existing registered client")
        result["modules"][name] = item
    return result
