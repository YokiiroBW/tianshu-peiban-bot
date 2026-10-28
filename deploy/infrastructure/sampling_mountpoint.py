"""Prepare only the empty host mountpoint beneath the Platform data bind."""

import os
from pathlib import Path


def prepare_sampling_mountpoint(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.parent.is_dir():
        raise ValueError("sampling_mountpoint_invalid")
    if path.exists():
        if not path.is_dir() or any(path.iterdir()):
            raise ValueError("sampling_mountpoint_not_empty")
    else:
        path.mkdir(mode=0o700)
    os.chown(path, 10001, 10001)
    path.chmod(0o700)
