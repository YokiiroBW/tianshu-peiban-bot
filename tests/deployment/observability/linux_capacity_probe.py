"""Real ENOSPC confined to guard's existing 64MiB tmpfs; NOT five-volume proof."""

import errno
import json
from pathlib import Path
import shutil
import sys


def main():
    sys.path.insert(0, "/opt/observability")
    from guard import State

    root = Path("/tmp/depi-capacity")
    root.mkdir(mode=0o700, exist_ok=False)
    disk = shutil.disk_usage(root)
    if not 0 < disk.total <= 64 * 1024**2:
        raise ValueError("bounded_tmpfs_required")
    # The mount type is checked as well, so this never fills a host-backed path.
    mounts = Path("/proc/mounts").read_text().splitlines()
    if not any(line.split()[1:3] == ["/tmp", "tmpfs"] for line in mounts):
        raise ValueError("tmpfs_required")
    path = root / "fill"
    state = object.__new__(State)
    state.settings = {
        "reserve_bytes": 8 * 1024**2,
        "storage_roots": {"fixture": str(root)},
    }
    before = state.space_available()
    rejected = False
    full = False
    written = 0
    try:
        with path.open("xb", buffering=0) as stream:
            while written <= 65 * 1024**2:
                rejected |= not state.space_available()
                try:
                    written += stream.write(b"0" * (1024**2))
                except OSError as exc:
                    if exc.errno != errno.ENOSPC:
                        raise
                    full = True
                    break
        rejected |= not state.space_available()
    finally:
        # Only this invocation's synthetic filler, never any application segment.
        path.unlink(missing_ok=True)
        root.rmdir()
    after = (
        state.space_available()
        if root.exists()
        else shutil.disk_usage("/tmp").free > 8 * 1024**2
    )
    if not (before and rejected and full and after):
        raise ValueError("bounded_capacity_probe_failed")
    print(
        json.dumps(
            {
                "scope": "guard_tmpfs_only",
                "actual_five_volume_enospc": False,
                "before_available": before,
                "reserve_rejected": rejected,
                "enospc": full,
                "recovered": after,
                "written_bytes": written,
                "filesystem_total": disk.total,
            }
        )
    )


if __name__ == "__main__":
    main()
