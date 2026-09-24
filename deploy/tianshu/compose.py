"""Compose output is JSON (also valid YAML), with no interpolation of secrets or host paths."""

from configuration import CA, DATABASE, PORTS, STATE, endpoint
from manifest import PRODUCTS
from resource_profile import constrain


def bind(source, target, readonly=True):
    return {
        "type": "bind",
        "source": source,
        "target": target,
        "read_only": readonly,
        "bind": {"create_host_path": False},
    }


def compose_document(manifest, site):
    services = {}
    for product in PRODUCTS:
        info = manifest["products"][product]["image"]
        reference = info["reference"] + ("@" + info["digest"] if info["digest"] else "")
        settings = "/etc/tianshu/settings.json"
        tls = [
            "--tls-cert",
            "/etc/tianshu/tls/server.pem",
            "--tls-key",
            "/etc/tianshu/tls/server.key",
        ]
        binding = ["--host", "0.0.0.0", "--port", str(PORTS[product])]
        if product == "platform":
            entry = ["python", "-m", "services.platform"]
            command = ["--settings", settings, "serve", *binding]
            health = [
                *entry,
                "healthcheck",
                "--url",
                endpoint(product) + "/health/live",
                "--ca-file",
                CA,
            ]
        elif product == "companion":
            entry = ["python", "-m", "tianshu_companion.runtime_cli"]
            command = [
                "--config",
                settings,
                "--contracts",
                "/contracts/text-dialogue/v1",
                "--database",
                STATE[product] + "/" + DATABASE[product],
                "--log-dir",
                "/var/log/tianshu",
                *binding,
                *tls,
            ]
            health = ["python", "/app/scripts/container_healthcheck.py"]
        elif product == "memory":
            entry = ["python", "-m", "tianshu_memory.cli"]
            command = [
                "--config",
                settings,
                "serve",
                *binding,
                "--tls-certfile",
                "/etc/tianshu/tls/server.pem",
                "--tls-keyfile",
                "/etc/tianshu/tls/server.key",
                "--allowed-host",
                f"memory.internal:{PORTS[product]}",
                "--diagnostics-contract",
                "/contracts/diagnostics/v1",
            ]
            health = ["python", "/opt/tianshu/container_healthcheck.py"]
        else:
            entry = ["python", "-m", "tianshu_gateway"]
            command = ["--settings", settings, *binding, *tls]
            health = [
                "python",
                "/opt/tianshu/healthcheck.py",
                "--url",
                endpoint(product) + "/health/live",
                "--cacert",
                CA,
            ]
        environment = {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "TIANSHU_LOG_DIR": "/var/log/tianshu",
            "TIANSHU_HEALTHCHECK_CA": CA,
            "TIANSHU_HEALTHCHECK_HOST": product
            + ".internal"
            + (":" + str(PORTS[product]) if product == "memory" else ""),
            "TIANSHU_HEALTHCHECK_PORT": str(PORTS[product]),
            "TIANSHU_HEALTHCHECK_SCHEME": "https",
            "SSL_CERT_FILE": CA,
        }
        if product == "memory":
            # This baseline's app factory still reads the environment even when the CLI
            # takes --config. Bind both explicitly to the SAME reviewed file.
            environment["TIANSHU_MEMORY_CONFIG"] = settings
        services[product] = {
            "image": reference,
            "platform": "linux/amd64",
            "user": "10001:10001",
            "read_only": True,
            "init": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "pids_limit": 128,
            "mem_limit": "1g",
            "cpus": "2.0",
            "restart": "no",
            "scale": 1,
            "stop_signal": "SIGTERM",
            "stop_grace_period": "30s",
            "entrypoint": [
                "python",
                "/opt/dep-a/runtime_guard.py",
                STATE[product],
                *entry,
            ],
            "command": command,
            "environment": environment,
            "env_file": [f"./private/{product}.env"],
            "tmpfs": ["/tmp:rw,noexec,nosuid,size=64m,mode=1777"],
            "volumes": [
                bind(f"./config/{product}", "/etc/tianshu"),
                bind("./contracts", "/contracts"),
                bind("./tools/runtime_guard.py", "/opt/dep-a/runtime_guard.py"),
                bind(f"./data/{product}", STATE[product], False),
                bind(f"./logs/{product}", "/var/log/tianshu", False),
            ],
            "networks": {
                "core": {
                    "ipv4_address": site["service_ips"][product],
                    "aliases": [product + ".internal"],
                }
            },
            "healthcheck": {
                "test": ["CMD", *health],
                "interval": "30s",
                "timeout": "10s",
                "start_period": "30s",
                "retries": 3,
            },
            # Application JSONL is the full log. Engine logs are bounded secondary diagnostics.
            "logging": {
                "driver": "local",
                "options": {"max-size": "10m", "max-file": "3"},
            },
            "labels": {
                "org.tianshu.release": manifest["release_id"],
                "org.tianshu.product": product,
            },
        }
    for service in services.values():
        constrain(service, site.get("resource_profile"))
    public_port = 8080 if site.get("public_web") else 8443
    services["platform"]["ports"] = [
        f"{site['bind_address']}:{site['web_port']}:{public_port}"
    ]
    # No dependency cycle: platform answers authority queries before peers start. Liveness
    # only orders startup. Release acceptance must separately authenticate /health/ready.
    for product in ("memory", "gateway"):
        services[product]["depends_on"] = {"platform": {"condition": "service_healthy"}}
    services["companion"]["depends_on"] = {
        p: {"condition": "service_healthy"} for p in ("platform", "memory", "gateway")
    }
    services["gateway"]["networks"]["egress"] = {}
    services["platform"]["networks"]["frontend"] = {}
    from network_plan import validate as validate_networks

    auxiliary = validate_networks(site.get("auxiliary_subnets"), site["subnet"])
    return {
        "name": site["project_name"],
        "services": services,
        "networks": {
            "core": {
                "internal": True,
                "ipam": {"config": [{"subnet": site["subnet"]}]},
            },
            "egress": {
                "internal": False,
                **(
                    {"ipam": {"config": [{"subnet": auxiliary["egress"]}]}}
                    if auxiliary
                    else {}
                ),
            },
            "frontend": {
                "internal": False,
                **(
                    {"ipam": {"config": [{"subnet": auxiliary["frontend"]}]}}
                    if auxiliary
                    else {}
                ),
            },
        },
    }
