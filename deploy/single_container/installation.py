"""Export a new installation without touching existing data or running services."""

import argparse
import copy
import json
import os
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from deploy.observability.retention import apply_retention

from .compose import compose, port, producer_log_mounts
from .profile import (
    MODULES,
    OBSERVABILITY,
    adapt,
    document,
    owner,
    relocate_environment,
    relocate_json,
)


NAMES = frozenset({"business-host", *OBSERVABILITY})


def relative(value):
    if (
        not isinstance(value, str)
        or "\\" in value
        or any(c in value for c in ("\n", "\r", "$", "\0"))
    ):
        raise ValueError("installation_file_path_invalid")
    result = PurePosixPath(value)
    if (
        result.is_absolute()
        or ".." in result.parts
        or str(result) != value
        or value in ("", ".")
    ):
        raise ValueError("installation_file_path_invalid")
    return result


def file_bytes(name, value):
    module = owner(name)
    if isinstance(value, (dict, list)):
        if name.startswith("contracts/"):
            raise ValueError("contract_files_require_original_bytes")
        return (
            json.dumps(
                adapt(value, module) if module else value, ensure_ascii=False, indent=2
            )
            + "\n"
        ).encode("utf-8")
    if not isinstance(value, (str, bytes)):
        raise ValueError("installation_file_content_invalid")
    if not name.startswith("contracts/") and module:
        if Path(name).suffix in {".json", ".yaml", ".yml"}:
            return relocate_json(value, module)
        if name.endswith(".env"):
            return relocate_environment(value, module)
    return value.encode("utf-8") if isinstance(value, str) else value


def write_file(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    path.write_bytes(content)
    path.chmod(0o640)


def validate_host(files):
    host = document(files.get("host.json"))
    if (
        not isinstance(host, dict)
        or type(host.get("version")) is not int
        or host["version"] != 1
    ):
        raise ValueError("unified_business_host_required")
    modules = host.get("modules")
    if (
        not isinstance(modules, dict)
        or set(MODULES) - set(modules)
        or set(modules) - {*MODULES, "onebot"}
    ):
        raise ValueError("complete_business_host_required")
    platform = document(files["modules/platform/settings.json"])
    access = platform.get("web_access")
    if access is None:
        frontend = host.get("frontend", {})
        frontend_port = port(frontend.get("port"))
        api_frontend_port = None
    else:
        frontend_port = port(access.get("port"))
        api_frontend_port = (
            port(host["frontend"].get("port")) if "frontend" in host else None
        )
        if api_frontend_port == frontend_port:
            raise ValueError("native_frontend_ports_overlap")
    for listener_port in (frontend_port, api_frontend_port):
        if listener_port in {8445, 9598, 3100, 9090, 3000, 9096} or (
            "onebot" in modules and listener_port == 18765
        ):
            raise ValueError("native_frontend_conflicts_with_observer_listener")
    return host, frontend_port, api_frontend_port


def observer_hosts(processes, files):
    """Only native TLS engines need container DNS aliases; modules use exchange."""
    vector = document(files["observability/vector.json"])
    if "knowledge" not in vector.get(
        "sources", {}
    ) or "knowledge_safe" not in vector.get("sinks", {}).get("loki", {}).get(
        "inputs", []
    ):
        raise ValueError("knowledge_vector_source_required")
    guard = document(files["observability/guard.json"])
    knowledge_root = guard.get("log_roots", {}).get("knowledge")
    knowledge_budget = guard.get("log_budgets", {}).get("knowledge")
    if (
        not isinstance(knowledge_root, str)
        or not PurePosixPath(knowledge_root).is_absolute()
        or type(knowledge_budget) is not int
        or knowledge_budget <= 0
    ):
        raise ValueError("knowledge_log_root_and_budget_required")
    aliases = set()
    for process in processes:
        if process["name"] not in OBSERVABILITY:
            continue
        host = urlsplit(process["health"]["url"]).hostname
        if not host or any(c in host for c in ": /\n\r\t*"):
            raise ValueError("native_observer_hostname_invalid")
        aliases.add(host)
    aliases.update(OBSERVABILITY)
    prometheus = document(files["observability/prometheus.json"])
    for job in prometheus.get("scrape_configs", []):
        for group in job.get("static_configs", []):
            for target in group.get("targets", []):
                host = urlsplit("//" + target).hostname
                if host in {*MODULES, *(name + ".internal" for name in MODULES)}:
                    raise ValueError("business_tcp_scrape_requires_host_adapter")
    return [f"{name}:127.0.0.1" for name in sorted(aliases)]


def export(
    installation,
    output,
    *,
    public_port=18446,
    api_port=None,
    onebot_port=18765,
    observability_ports=None,
    retention_days=None,
    log_max_events=None,
):
    """Write one reviewed unit; all validation precedes creating its new directory."""
    if type(installation.get("version")) is not int or installation["version"] != 1:
        raise ValueError("installation_version_invalid")
    processes = copy.deepcopy(installation.get("processes", []))
    names = [item.get("name") for item in processes]
    if len(names) != len(set(names)) or set(names) != NAMES:
        raise ValueError("one_business_host_and_five_native_observers_required")
    for process in processes:
        if not process.get("argv") or not process.get("health"):
            raise ValueError("process_entrypoint_and_real_health_required")
    business = next(item for item in processes if item["name"] == "business-host")
    if business["health"].get("type") != "module_state":
        raise ValueError("business_host_module_state_probe_required")
    files = installation.get("files", {})
    if not isinstance(files, dict) or "runtime.json" in files:
        raise ValueError("runtime_configuration_is_generated")
    files = copy.deepcopy(files)
    host, frontend_port, api_frontend_port = validate_host(files)
    guard_settings = document(files["observability/guard.json"])
    files["observability/guard.json"], files["observability/loki.json"] = (
        apply_retention(
            guard_settings,
            document(files["observability/loki.json"]),
            retention_days=guard_settings.get("retention_days", 30)
            if retention_days is None
            else retention_days,
            log_max_events=guard_settings.get("log_max_events", 10000)
            if log_max_events is None
            else log_max_events,
        )
    )
    files["observability/guard.json"]["host_state_path"] = host["state_file"]
    paths = {name: relative(name) for name in files}
    contents = {name: file_bytes(name, value) for name, value in files.items()}
    if api_port is not None and api_frontend_port is None:
        raise ValueError("additional_tls_api_frontend_required")
    if business["health"].get("path") != host["state_file"]:
        raise ValueError("business_host_state_probe_mismatch")
    # The real group stops in reverse order: business closes before its log pipeline.
    if names[-1] != "business-host":
        raise ValueError("business_host_must_stop_before_observers")
    comic = copy.deepcopy(installation.get("comics"))
    grace = sum(item.get("stop_timeout_seconds", 30) + 6 for item in processes)
    if comic is not None:
        grace += sum(
            item.get("stop_timeout_seconds", 30) + 6
            for item in comic.get("processes", [])
        )
    document_compose = compose(
        installation["image"],
        public_port=public_port,
        frontend_port=frontend_port,
        api_port=api_port,
        api_frontend_port=api_frontend_port,
        onebot="onebot" in host["modules"],
        onebot_port=onebot_port,
        observability_ports=observability_ports,
        comics=comic is not None,
        stop_grace_seconds=max(180, grace),
    )
    service = document_compose["services"]["tianshu"]
    service["extra_hosts"] = observer_hosts(processes, files)
    for mount in producer_log_mounts(installation.get("mounts", [])):
        if not isinstance(mount, dict) or mount.get("type") != "bind":
            raise ValueError("installation_bind_mount_required")
        destination = mount.get("target", "")
        if (
            not isinstance(destination, str)
            or not PurePosixPath(destination).is_absolute()
            or str(PurePosixPath(destination)) != destination
            or ".." in PurePosixPath(destination).parts
            or not isinstance(mount.get("source"), str)
        ):
            raise ValueError("installation_bind_path_invalid")
        protected = (
            "/config",
            "/run/tianshu",
            "/proc",
            "/sys",
            "/dev",
        )
        if destination in {"/", "/data", "/var/lib/tianshu", "/var/log/tianshu"} or any(
            destination == root
            or destination.startswith(root + "/")
            or root.startswith(destination + "/")
            for root in protected
        ):
            raise ValueError("installation_mount_overlaps_application_root")
        value = copy.deepcopy(mount)
        value["bind"] = {"create_host_path": False}
        service["volumes"].append(value)
    runtime = {
        "version": 1,
        "state_file": "/run/tianshu/runtime.json",
        "processes": processes,
    }
    if comic is not None:
        runtime["comics"] = comic
    target = Path(output)
    if target.exists():
        raise ValueError("installation_output_already_exists")
    target.mkdir(parents=True, mode=0o700)
    config = target / "config"
    for name, content in contents.items():
        write_file(config.joinpath(*paths[name].parts), content)
    write_file(config / "runtime.json", file_bytes("runtime.json", runtime))
    write_file(target / "compose.yaml", file_bytes("compose.yaml", document_compose))
    data = target / "data"
    directories = {data, data / "legacy-state", data / "legacy-logs"}
    for group, entries in (
        ("modules", host["modules"]),
        ("logs", MODULES),
        ("observability", ("guard", "vector", "loki", "prometheus", "grafana")),
    ):
        directories.add(data / group)
        directories.update(data / group / name for name in entries)
    for directory in sorted(directories):
        directory.mkdir(parents=True, exist_ok=True, mode=0o750)
    if hasattr(os, "chown") and os.geteuid() == 0:
        # Only this newly created installation; no old/media directory is traversed.
        for directory in directories:
            os.chown(directory, 10001, 10001)
        for path in [config, *config.rglob("*")]:
            os.chown(path, 0, 10001)
            if path.is_dir():
                path.chmod(0o750)
    return {
        "status": "exported_not_started",
        "services": 1,
        "business_processes": 1,
        "native_observer_processes": len(OBSERVABILITY),
        "modules": len(host["modules"]),
        "comics_plugin": comic is not None,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--public-port", type=int, default=18446)
    parser.add_argument("--api-port", type=int)
    parser.add_argument("--onebot-port", type=int, default=18765)
    parser.add_argument("--observability-ports", type=Path)
    parser.add_argument("--retention-days", type=int)
    parser.add_argument("--max-events", type=int)
    args = parser.parse_args(argv)
    ports = (
        json.loads(args.observability_ports.read_bytes())
        if args.observability_ports
        else None
    )
    print(
        json.dumps(
            export(
                json.loads(args.installation.read_bytes()),
                args.output,
                public_port=args.public_port,
                api_port=args.api_port,
                onebot_port=args.onebot_port,
                observability_ports=ports,
                retention_days=args.retention_days,
                log_max_events=args.max_events,
            )
        )
    )


if __name__ == "__main__":
    main()
