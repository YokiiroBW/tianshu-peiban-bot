"""Generate the exclusively-owned logging Compose; no application network or sockets."""


def mount(source, target, readonly=True):
    return {
        "type": "bind",
        "source": str(source),
        "target": target,
        "read_only": readonly,
        "bind": {"create_host_path": False},
    }


def compose(bundle, log_roots, contract, tls, tokens, images, ports):
    services = {}
    for name, memory in (
        ("vector", "768m"),
        ("loki", "1536m"),
        ("grafana", "512m"),
        ("prometheus", "512m"),
        ("guard", "256m"),
    ):
        services["obs-" + name] = {
            "image": images[name],
            "user": "10001:10001",
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "pids_limit": 128,
            "mem_limit": memory,
            "cpus": 1.5 if name == "loki" else 0.75,
            "restart": "unless-stopped",
            "stop_grace_period": "30s",
            "logging": {
                "driver": "local",
                "options": {"max-size": "10m", "max-file": "3"},
            },
            "tmpfs": ["/tmp:rw,noexec,nosuid,size=64m,uid=10001,gid=10001,mode=0700"],
            "networks": ["observe"],
            "volumes": [mount(bundle / "data" / name, "/var/lib/" + name, False)],
        }

    def add(name, source, target, readonly=True):
        services["obs-" + name]["volumes"].append(mount(source, target, readonly))

    def certs(name, names):
        for filename in names:
            add(name, tls[filename], "/run/tls/" + filename)

    def secret(name, token):
        add(name, tokens[token], "/run/secrets/" + token)

    for service, path in log_roots.items():
        for collector in ("vector", "guard"):
            add(collector, path, "/sources/" + service)
    for name in ("vector", "loki", "prometheus"):
        add(
            name, bundle / "config" / (name + ".json"), "/etc/tianshu/" + name + ".json"
        )
    certs("vector", ["ca.pem", "vector.pem", "vector.key"])
    secret("vector", "writer_token")
    services["obs-vector"]["command"] = ["--config", "/etc/tianshu/vector.json"]
    services["obs-vector"]["environment"] = {"VECTOR_LOG": "error"}
    certs("loki", ["loki.pem", "loki.key", "client-ca.pem"])
    services["obs-loki"]["command"] = ["-config.file=/etc/tianshu/loki.json"]
    services["obs-loki"]["networks"] = ["storage"]
    certs("guard", ["ca.pem", "guard.pem", "guard.key", "client.pem", "client.key"])
    for token in ("writer_token", "query_token", "metrics_token"):
        secret("guard", token)
    add("guard", contract, "/contracts/diagnostics/v1")
    add("guard", bundle / "code", "/opt/observability")
    add("guard", bundle / "config/guard.json", "/etc/tianshu/guard.json")
    for role in ("vector", "loki"):
        add("guard", bundle / "data" / role, "/capacity/" + role)
    services["obs-guard"]["command"] = [
        "python",
        "-B",
        "/opt/observability/guard.py",
        "--settings",
        "/etc/tianshu/guard.json",
    ]
    services["obs-guard"]["networks"] = ["observe", "storage"]
    services["obs-guard"]["ports"] = [f"127.0.0.1:{ports['query']}:8443"]
    certs(
        "prometheus",
        ["ca.pem", "prometheus.pem", "prometheus.key"],
    )
    secret("prometheus", "metrics_token")
    add(
        "prometheus",
        bundle / "config/prometheus-web.json",
        "/etc/tianshu/prometheus-web.json",
    )
    services["obs-prometheus"]["command"] = [
        "--config.file=/etc/tianshu/prometheus.json",
        "--web.config.file=/etc/tianshu/prometheus-web.json",
        "--storage.tsdb.path=/var/lib/prometheus",
        "--storage.tsdb.retention.time=30d",
        "--storage.tsdb.retention.size=2GB",
        "--web.enable-admin-api=false",
    ]
    certs("grafana", ["grafana.pem", "grafana.key"])
    secret("grafana", "query_token")
    secret("grafana", "grafana_admin_password")
    add("grafana", bundle / "config/provisioning", "/etc/grafana/provisioning")
    add("grafana", bundle / "config/dashboards", "/etc/grafana/dashboards")
    add(
        "grafana",
        bundle / "code/grafana-entrypoint.sh",
        "/opt/observability/grafana-entrypoint.sh",
    )
    services["obs-grafana"]["entrypoint"] = [
        "/bin/sh",
        "/opt/observability/grafana-entrypoint.sh",
    ]
    services["obs-grafana"]["environment"] = {
        "GF_PATHS_DATA": "/var/lib/grafana",
        "GF_PATHS_LOGS": "/tmp",
        "GF_SERVER_PROTOCOL": "https",
        "GF_SERVER_HTTP_PORT": "3000",
        "GF_SERVER_CERT_FILE": "/run/tls/grafana.pem",
        "GF_SERVER_CERT_KEY": "/run/tls/grafana.key",
        "GF_SECURITY_ADMIN_USER": "admin",
        "GF_SECURITY_ADMIN_PASSWORD__FILE": "/run/secrets/grafana_admin_password",
        "GF_SECURITY_COOKIE_SECURE": "true",
        "GF_SECURITY_COOKIE_SAMESITE": "strict",
        "GF_USERS_ALLOW_SIGN_UP": "false",
        "GF_USERS_AUTO_ASSIGN_ORG_ROLE": "Viewer",
        "GF_AUTH_ANONYMOUS_ENABLED": "false",
        "GF_ANALYTICS_REPORTING_ENABLED": "false",
        "GF_ANALYTICS_CHECK_FOR_UPDATES": "false",
        "GF_LOG_LEVEL": "error",
        "GF_SERVER_ROUTER_LOGGING": "false",
        "GF_UNIFIED_ALERTING_ENABLED": "true",
    }
    services["obs-grafana"]["ports"] = [f"127.0.0.1:{ports['grafana']}:3000"]
    return {
        "name": "tianshu-observability",
        "services": services,
        "networks": {"observe": {"internal": True}, "storage": {"internal": True}},
    }
