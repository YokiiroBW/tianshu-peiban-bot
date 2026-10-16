"""Operation dispatch: configuration and bounded collectors meet at this edge."""

from __future__ import annotations

from .compose import collect_compose
from .docker import DockerCollector
from .linux import collect_node
from .protocol import Failure, envelope
from .sampling import SampleStore, slots
from .transport import Deadline, SSHTransport


def execute(request, config, *, transport_factory=SSHTransport):
    if request.operation == "node.catalog":
        nodes = [{"id": node.id, "label": node.label, "kind": node.kind, "capabilities": node.capabilities}
                 for node in sorted(config.nodes.values(), key=lambda node: node.id)]
        return envelope(request, data={"nodes": nodes}, capabilities=["node.catalog"], returned=len(nodes), total=len(nodes))
    node = config.nodes.get(request.node_id)
    if node is None:
        return envelope(request, status="unavailable", reasons=["node_not_registered"])
    capabilities = node.capabilities
    if request.operation not in capabilities:
        return envelope(request, status="unsupported", reasons=["operation_not_configured"], capabilities=capabilities)
    deadline = Deadline(request.timeout_ms / 1000)
    try:
        sampling = request.operation in ("node.snapshot", "docker.stats")
        with slots(config, node, deadline, sampling=sampling):
            transport = transport_factory(config, node, deadline)
            store = SampleStore(config, node) if sampling else None
            cursor = None
            if request.operation == "node.snapshot":
                data, reasons = collect_node(transport, node, store, config.stale_after_seconds)
                returned = total = 1
            elif request.operation == "compose.inventory":
                data, reasons, total, cursor = collect_compose(transport, node, request.params)
                returned = len(data["stacks"])
            else:
                docker = DockerCollector(transport, node)
                if request.operation == "docker.inventory":
                    data, reasons, total, cursor = docker.inventory(request.params)
                    returned = len(data["containers"])
                elif request.operation == "docker.stats":
                    data, reasons = docker.stats(request.params["container_ids"], store, config.stale_after_seconds)
                    returned = total = len(data["containers"])
                else:
                    data, reasons = docker.logs(request.params)
                    returned = total = 1
            if store:
                try:
                    store.save()
                except Failure as failure:
                    reasons.append(failure.code)
            return envelope(request, status="partial" if reasons else "ok", reasons=reasons, data=data,
                            capabilities=capabilities, returned=returned, total=total, next_cursor=cursor)
    except Failure as failure:
        return envelope(request, status=failure.status, reasons=[failure.code], capabilities=capabilities)
