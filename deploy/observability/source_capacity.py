"""Physical source usage, independent of log-event processing."""

import shutil

from policy import segments


def capacities(roots, budgets):
    values = {}
    for service, path in roots.items():
        size = sum(segment.stat().st_size for segment in segments(path))
        disk = shutil.disk_usage(path)
        values[service] = {
            "bytes": size, "budget": budgets[service], "ratio": size / budgets[service],
            "free": disk.free, "filesystem_ratio": 1 - disk.free / disk.total,
        }
    return values
