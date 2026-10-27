"""One public application, with explicit persistent roots and native observers."""

import copy

from .profile import MODULES


def producer_log_mounts(mounts):
    """The five shared log roots have native writers as well as observer readers."""
    result = copy.deepcopy(list(mounts))
    roots = {f"/data/logs/{name}" for name in MODULES}
    for mount in result:
        if (
            isinstance(mount, dict)
            and mount.get("type") == "bind"
            and mount.get("target") in roots
        ):
            mount["read_only"] = False
    return result


def port(value):
    if type(value) is not int or not 1024 <= value <= 65535:
        raise ValueError("distribution_port_invalid")
    return value


def compose(
    image,
    *,
    public_port=18446,
    frontend_port=8443,
    api_port=None,
    api_frontend_port=8443,
    onebot=False,
    onebot_port=18765,
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
        published.append(port(onebot_port))
    if api_port is not None:
        published.append(port(api_port))
        if port(api_frontend_port) == port(frontend_port):
            raise ValueError("distribution_listener_ports_overlap")
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
        service["ports"].append(f"{onebot_port}:18765")
    if api_port is not None:
        service["ports"].append(f"{api_port}:{api_frontend_port}")
    if comics:
        service["environment"] = {"TIANSHU_COMICS_PLUGIN": "1"}
    return {"name": "tianshu", "services": {"tianshu": service}}
