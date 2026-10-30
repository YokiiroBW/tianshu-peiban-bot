"""Reclaim closed source segments using Vector's acknowledged offsets."""

import time
from fnmatch import fnmatchcase
from pathlib import Path

from source_segments import MODULES, _sync_directory, active_instances, closed, native_segment, stamp
from vector_checkpoints import checkpoint_positions, fingerprint


def prune_sources(roots, vector_data, *, host_state_path=None, now=None):
    """Remove only unchanged, closed files with unique complete offsets.

    Only safe with the pinned Vector file-source fingerprint configuration and
    sink acknowledgements enabled.  Missing or ambiguous evidence retains files.
    """
    realtime = now is None
    now = time.time() if realtime else now
    instances = active_instances(host_state_path, now)
    deleted = deleted_bytes = failed = 0
    for source, root in roots.items():
        if source not in MODULES:
            continue
        try:
            directory = Path(root)
            if directory.is_symlink() or (hasattr(directory, "is_junction") and directory.is_junction()):
                raise ValueError("unsafe_log_root")
            directory = directory.resolve(strict=True)
            files = []
            for path in sorted(directory.iterdir()):
                if fnmatchcase(path.name, "*.jsonl") or fnmatchcase(path.name, "*.jsonl.[0-9]*"):
                    if path.is_symlink() or path.resolve().parent != directory or not path.is_file():
                        raise ValueError("unsafe_vector_source")
                    files.append(path)
            if not files:
                continue
            positions = checkpoint_positions(Path(vector_data) / source / "checkpoints.json")
            catalog = []
            highest, counts = {}, {}
            # Count *all* watched files, not just files with native names: Vector
            # treats files sharing a checksum as the same input.
            for path in files:
                segment = native_segment(source, path.name)
                info = path.lstat()
                if not path.is_file() or path.is_symlink():
                    continue
                code = fingerprint(path)
                if code is not None:
                    counts[code] = counts.get(code, 0) + 1
                if segment:
                    highest[segment[0]] = max(highest.get(segment[0], -1), segment[1])
                    catalog.append((path, segment, info, code))
            for path, segment, info, code in catalog:
                if (
                    info.st_nlink != 1
                    or code is None
                    or counts[code] != 1
                    or code not in positions
                    or positions[code][0] != info.st_size
                    or positions[code][1] < info.st_ctime_ns
                    or not closed(source, segment, highest, instances)
                ):
                    continue
                if not closed(
                    source, segment, highest,
                    active_instances(host_state_path, time.time() if realtime else now),
                ):
                    continue
                if stamp(path.lstat()) != stamp(info) or fingerprint(path) != code:
                    continue
                path.unlink()
                _sync_directory(path.parent)
                deleted += 1
                deleted_bytes += info.st_size
        except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
            failed += 1
    return {
        "source_retention_deleted_segments": deleted,
        "source_retention_deleted_bytes": deleted_bytes,
        "source_retention_failures": failed,
    }
