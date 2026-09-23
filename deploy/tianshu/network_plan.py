"""Explicit small private auxiliary networks; defaults remain Docker-managed."""

import ipaddress
from manifest import require


def validate(subnets, core):
    if subnets is None:
        return None
    require(
        isinstance(subnets, dict) and set(subnets) == {"egress", "frontend"},
        "auxiliary_network_shape",
    )
    nets = [ipaddress.ip_network(core, strict=True)]
    for value in subnets.values():
        net = ipaddress.ip_network(value, strict=True)
        require(
            net.version == 4 and net.is_private and 24 <= net.prefixlen <= 28,
            "auxiliary_private_small_subnet_required",
        )
        require(
            not any(net.overlaps(other) for other in nets), "auxiliary_network_overlap"
        )
        nets.append(net)
    return subnets
