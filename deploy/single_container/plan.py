"""Assemble native modules into one host and retain the five native observers."""

import copy
from pathlib import Path
import ssl
import tempfile
from urllib.parse import urlsplit

from .exchange import authority
from .profile import APPLICATION, MODULES, OBSERVABILITY, PORTS, PYTHON, adapt, document


def grafana_hostname(files, environment):
    name = (
        environment.get("GF_SERVER_DOMAIN")
        or urlsplit(environment.get("GF_SERVER_ROOT_URL", "")).hostname
    )
    if name:
        return name
    certificate = files.get("observability/tls/grafana.pem")
    if not isinstance(certificate, (str, bytes)):
        raise ValueError("grafana_hostname_or_public_certificate_required")
    # Decode only the public certificate. No key or token is parsed or returned.
    with tempfile.TemporaryDirectory(prefix="tianshu-public-certificate-") as temporary:
        path = Path(temporary) / "grafana.pem"
        path.write_bytes(
            certificate.encode("utf-8") if isinstance(certificate, str) else certificate
        )
        names = [
            value
            for kind, value in ssl._ssl._test_decode_cert(str(path)).get(
                "subjectAltName", ()
            )
            if kind == "DNS"
        ]
    if not names or "*" in names[0]:
        raise ValueError("explicit_grafana_hostname_required")
    return names[0]


def observer_processes(files, native_environment):
    root = "/config/observability"
    tls, secret = root + "/tls", root + "/secrets"
    definitions = {
        "obs-loki": (
            ["/usr/local/bin/loki", "-config.file=" + root + "/loki.json"],
            "/ready",
            [],
        ),
        "obs-guard": (
            [
                PYTHON,
                "-B",
                APPLICATION + "/deploy/observability/guard.py",
                "--settings",
                root + "/guard.json",
                "--host",
                "127.0.0.1",
                "--port",
                "8445",
            ],
            "/ready",
            ["obs-loki"],
        ),
        "obs-vector": (
            ["/usr/local/bin/vector", "--config", root + "/vector.json"],
            "/metrics",
            ["obs-guard"],
        ),
        "obs-prometheus": (
            [
                "/usr/local/bin/prometheus",
                "--config.file=" + root + "/prometheus.json",
                "--web.config.file=" + root + "/prometheus-web.json",
                "--storage.tsdb.path=/data/observability/prometheus",
                "--storage.tsdb.retention.time=30d",
                "--storage.tsdb.retention.size=2GB",
                "--no-web.enable-admin-api",
            ],
            "/-/ready",
            ["obs-guard", "obs-vector"],
        ),
        "obs-grafana": (
            ["/bin/sh", APPLICATION + "/deploy/observability/grafana-entrypoint.sh"],
            "/api/health",
            ["obs-guard", "obs-prometheus"],
        ),
    }
    grafana = {
        "GF_PATHS_HOME": "/usr/share/grafana",
        "GF_PATHS_CONFIG": "/usr/share/grafana/conf/defaults.ini",
        "GF_PATHS_DATA": "/data/observability/grafana",
        "GF_PATHS_LOGS": "/tmp/grafana",
        "GF_PATHS_PLUGINS": "/data/observability/grafana/plugins",
        "GF_PATHS_PROVISIONING": root + "/provisioning",
        "GF_SERVER_PROTOCOL": "https",
        "GF_SERVER_HTTP_PORT": "3000",
        "GF_SERVER_CERT_FILE": tls + "/grafana.pem",
        "GF_SERVER_CERT_KEY": tls + "/grafana.key",
        "GF_SECURITY_ADMIN_USER": "admin",
        "GF_SECURITY_ADMIN_PASSWORD__FILE": secret + "/grafana_admin_password",
        "GF_USERS_AUTO_ASSIGN_ORG_ROLE": "Viewer",
        "GF_USERS_ALLOW_SIGN_UP": "false",
        "GF_AUTH_ANONYMOUS_ENABLED": "false",
        "GF_UNIFIED_ALERTING_ENABLED": "true",
        "GF_SECURITY_COOKIE_SECURE": "true",
        "GF_SECURITY_COOKIE_SAMESITE": "strict",
        "GF_LOG_LEVEL": "error",
        "GF_SERVER_ROUTER_LOGGING": "false",
        "GF_ANALYTICS_REPORTING_ENABLED": "false",
        "GF_ANALYTICS_CHECK_FOR_UPDATES": "false",
        "GF_PLUGINS_PREINSTALL_DISABLED": "true",
    }
    result = []
    for name, (argv, path, dependencies) in definitions.items():
        environment = (
            dict(grafana)
            if name == "obs-grafana"
            else {"VECTOR_LOG": "error"}
            if name == "obs-vector"
            else {}
        )
        environment.update(adapt(native_environment.get(name, {}), name))
        hostname = (
            grafana_hostname(files, environment) if name == "obs-grafana" else name
        )
        if name == "obs-grafana":
            # The one existing entrypoint keeps its standalone /run/secrets default.
            environment["QUERY_TOKEN_FILE"] = secret + "/query_token"
        check = {
            "type": "http",
            "url": f"https://{hostname}:{PORTS[name]}{path}",
            "ca_file": tls + "/ca.pem",
            "interval_seconds": 10,
        }
        if name == "obs-loki":
            check.update(
                client_certfile=tls + "/client.pem", client_keyfile=tls + "/client.key"
            )
        elif name == "obs-guard":
            check["bearer_token_file"] = secret + "/metrics_token"
        result.append(
            {
                "name": name,
                "argv": argv,
                "env": environment,
                "uid": 10001,
                "gid": 10001,
                "depends_on": dependencies,
                "health": check,
                "startup_timeout_seconds": 120,
                "stop_timeout_seconds": 90 if name == "obs-vector" else 30,
            }
        )
    return result


def make_installation(
    image,
    files,
    *,
    knowledge_client,
    mounts=(),
    comics=None,
    observability_environment=None,
    native_origins=None,
    frontend=None,
):
    """Reuse reviewed native outputs; never generate credentials, models or storage."""
    if not isinstance(knowledge_client, str) or not knowledge_client.strip():
        raise ValueError("registered_knowledge_client_required")
    if not isinstance(files, dict) or "host.json" in files or "runtime.json" in files:
        raise ValueError("native_files_required")
    required = {
        f"observability/{name}.json"
        for name in (
            "guard",
            "vector",
            "loki",
            "prometheus",
            "prometheus-web",
            "vocabulary",
        )
    }
    if not required <= set(files):
        raise ValueError("native_observability_configuration_required")
    files = copy.deepcopy(files)
    mounts = copy.deepcopy(list(mounts))
    native_environment = copy.deepcopy(observability_environment or {})
    if not isinstance(native_environment, dict) or set(native_environment) - set(
        OBSERVABILITY
    ):
        raise ValueError("native_observability_environment_invalid")
    for values in native_environment.values():
        if not isinstance(values, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in values.items()
        ):
            raise ValueError("native_observability_environment_invalid")
    modules = {}
    for name in MODULES:
        if (
            f"modules/{name}/settings.json" not in files
            or f"private/{name}.env" not in files
        ):
            raise ValueError("native_module_settings_and_environment_required")
        modules[name] = {
            "settings": f"/config/modules/{name}/settings.json",
            "origin": f"https://{name}.internal:{PORTS[name]}",
            "env_file": [f"/config/private/{name}.env"],
            "env": {
                "TIANSHU_LOG_DIR": f"/data/logs/{name}",
                "SSL_CERT_FILE": f"/config/modules/{name}/tls/ca.pem",
            },
        }
        if name in {"memory", "knowledge"}:
            modules[name]["contracts"] = f"/config/contracts/{name}/diagnostics/v1"
            modules[name]["log_directory"] = f"/data/logs/{name}"
        if name == "knowledge":
            modules[name]["client"] = knowledge_client
    platform = document(files["modules/platform/settings.json"])
    if comics is not None:
        if (
            not isinstance(comics, dict)
            or comics.get("socket") != "/run/tianshu/comics/control.sock"
        ):
            raise ValueError("fixed_comic_control_socket_required")
        plugin = platform.get("plugins", {}).get("comic_files")
        if not isinstance(plugin, dict):
            raise ValueError("native_comic_files_configuration_required")
        # Only this fixed runtime integration reference is added to existing config.
        if plugin.get("runtime_socket") != "/run/tianshu/comics/control.sock":
            platform = copy.deepcopy(platform)
            platform["plugins"]["comic_files"]["runtime_socket"] = (
                "/run/tianshu/comics/control.sock"
            )
            files["modules/platform/settings.json"] = platform
    onebot_files = {
        "modules/onebot/storage.json",
        "modules/onebot/secrets/onebot-token",
    }
    if set(files) & onebot_files:
        if not onebot_files <= set(files):
            raise ValueError("onebot_native_storage_and_token_required")
        modules["onebot"] = {
            "settings": "/config/modules/onebot/storage.json",
            "origin": "http://onebot:18765",
            "data_directory": "/data/modules/onebot",
            "token_file": "/config/modules/onebot/secrets/onebot-token",
            "env_file": ["/config/private/onebot.env"]
            if "private/onebot.env" in files
            else [],
            "env": {},
        }
    if native_origins is not None:
        if not isinstance(native_origins, dict) or set(native_origins) != set(modules):
            raise ValueError("complete_native_module_origins_required")
        for name, origin in native_origins.items():
            if not isinstance(origin, str):
                raise ValueError("native_module_origin_invalid")
            authority(origin)
            parsed = urlsplit(origin)
            if parsed.path not in {"", "/"} or parsed.query:
                raise ValueError("native_module_origin_invalid")
            modules[name]["origin"] = origin
    host = {
        "version": 1,
        "state_file": "/run/tianshu/host/business-host.json",
        "modules": modules,
    }
    if frontend is not None:
        host["frontend"] = copy.deepcopy(frontend)
    elif platform.get("web_access") is None:
        host["frontend"] = {
            "host": "0.0.0.0",
            "port": 8443,
            "tls_certfile": "/config/modules/platform/tls/server.pem",
            "tls_keyfile": "/config/modules/platform/tls/server.key",
        }
    files["host.json"] = host
    guard = [PYTHON, "-B", APPLICATION + "/deploy/tianshu/runtime_guard.py"]
    state_mounts = {
        mount["target"]: mount["source"]
        for mount in mounts
        if isinstance(mount, dict)
        and mount.get("type") == "bind"
        and isinstance(mount.get("source"), str)
        and isinstance(mount.get("target"), str)
    }
    locked = set()
    for directory in (f"/data/modules/{name}" for name in modules):
        identity = (
            ("bind", state_mounts[directory])
            if directory in state_mounts
            else ("internal", directory)
        )
        if identity in locked:
            continue
        locked.add(identity)
        guard += ["--state-dir", directory]
    processes = observer_processes(files, native_environment)
    processes.append(
        {
            "name": "business-host",
            "argv": [
                *guard,
                "--",
                PYTHON,
                "-m",
                "deploy.single_container.host",
                "--config",
                "/config/host.json",
            ],
            "uid": 10001,
            "gid": 10001,
            "cwd": APPLICATION,
            "env": {
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
                "PYTHONPATH": APPLICATION,
            },
            "health": {
                "type": "module_state",
                "path": host["state_file"],
                "max_age_seconds": 15,
                "interval_seconds": 5,
            },
            "startup_timeout_seconds": 180,
            "stop_timeout_seconds": 120,
        }
    )
    return {
        "version": 1,
        "image": image,
        "files": files,
        "processes": processes,
        "mounts": mounts,
        "comics": copy.deepcopy(comics),
    }
