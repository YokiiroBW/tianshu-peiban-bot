"""One public application, with explicit persistent roots and native observers."""


def port(value):
    if type(value) is not int or not 1024 <= value <= 65535:
        raise ValueError("distribution_port_invalid")
    return value


def compose(
    image,
    *,
    public_port=18446,
    frontend_port=8443,
    onebot=False,
    observability_ports=None,
    comics=False,
    stop_grace_seconds=420,
):
    if not isinstance(image, str) or not image or any(c in image for c in "\n\r $\t"):
        raise ValueError("application_image_required")
    exposed = (
        observability_ports
        if observability_ports is not None
        else {"grafana": 18490, "query": 18491}
    )
    if not isinstance(exposed, dict) or set(exposed) != {"grafana", "query"}:
        raise ValueError("observability_ports_required")
    published = [port(public_port), *(port(value) for value in exposed.values())]
    if onebot:
        published.append(18765)
    if len(published) != len(set(published)):
        raise ValueError("distribution_ports_overlap")
    service = {
        "image": image,
        "user": "0:10001",
        "restart": "unless-stopped",
        "read_only": True,
        "stop_grace_period": f"{stop_grace_seconds}s",
        "cap_drop": ["ALL"],
        "cap_add": ["SETUID", "SETGID", "KILL"],
        "security_opt": ["no-new-privileges:true"],
        "volumes": [
            "./config:/config:ro",
            "./data:/data",
            "./data/legacy-state:/var/lib/tianshu",
            "./data/legacy-logs:/var/log/tianshu",
        ],
        "tmpfs": [
            "/run/tianshu:rw,nosuid,nodev,size=16m,mode=0755",
            "/tmp:rw,nosuid,nodev,size=64m,mode=1777",
        ],
        "ports": [
            f"{public_port}:{port(frontend_port)}",
            f"127.0.0.1:{exposed['grafana']}:3000",
            f"127.0.0.1:{exposed['query']}:8445",
        ],
        "logging": {"driver": "local", "options": {"max-size": "10m", "max-file": "3"}},
    }
    if onebot:
        service["ports"].append("18765:18765")
    if comics:
        service["environment"] = {"TIANSHU_COMICS_PLUGIN": "1"}
    return {"name": "tianshu", "services": {"tianshu": service}}
