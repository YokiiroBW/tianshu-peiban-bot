"""Deployment settings, independent of AssetLibrary and Home Assistant configuration."""

import re
from pathlib import Path

from ..contracts import require
from ..external_catalog import ExternalCatalog


def node_id(value):
    require(
        isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", value),
        "invalid_input",
        400,
    )
    return value


def executor_configuration(value):
    require(
        isinstance(value, dict) and set(value) == {"command", "directory"}, "invalid_input", 400
    )
    command = value["command"]
    require(
        isinstance(command, list)
        and 1 <= len(command) <= 32
        and all(
            isinstance(item, str)
            and 0 < len(item) <= 4096
            and not any(ch in item for ch in "\x00\r\n")
            for item in command
        )
        and Path(command[0]).is_absolute()
        and isinstance(value["directory"], str)
        and Path(value["directory"]).is_absolute(),
        "invalid_input",
        400,
    )
    return {**value, "command": list(command)}


def configuration(settings, *, runtime=None):
    value = settings.get("infrastructure")
    if value is None:
        return None
    require(
        isinstance(value, dict)
        and {"directory", "contract_directory", "executor"}
        <= set(value)
        <= {
            "directory",
            "contract_directory",
            "executor",
            "poll_interval_seconds",
            "stale_after_seconds",
            "timeout_ms",
            "control",
            "platform_node_id",
        },
        "invalid_input",
        400,
    )
    for name in ("directory", "contract_directory"):
        require(
            isinstance(value[name], str) and Path(value[name]).is_absolute(), "invalid_input", 400
        )
    external = settings.get("web_external")
    require(
        not external or Path(value["directory"]).resolve() == Path(external["directory"]).resolve(),
        "invalid_input",
        400,
    )
    result = {**value, "executor": executor_configuration(value["executor"])}
    if "platform_node_id" in value:
        result["platform_node_id"] = node_id(value["platform_node_id"])
    for name, default, minimum, maximum in (
        ("poll_interval_seconds", 30, 5, 3600),
        ("stale_after_seconds", 120, 10, 86400),
        ("timeout_ms", 10000, 500, 30000),
    ):
        selected = value.get(name, default)
        require(type(selected) is int and minimum <= selected <= maximum, "invalid_input", 400)
        result[name] = selected
    require(result["stale_after_seconds"] >= result["poll_interval_seconds"], "invalid_input", 400)
    ExternalCatalog.verify_existing(result["directory"], runtime=runtime)
    from .client import Contract

    Contract(result["contract_directory"])
    if "control" in value:
        control = value["control"]
        require(
            isinstance(control, dict)
            and set(control) == {"contract_directory", "executor"}
            and isinstance(control["contract_directory"], str)
            and Path(control["contract_directory"]).is_absolute(),
            "invalid_input",
            400,
        )
        from .control_client import ControlContract

        ControlContract(control["contract_directory"])
        result["control"] = {
            "contract_directory": control["contract_directory"],
            "executor": executor_configuration(control["executor"]),
        }
    return result
