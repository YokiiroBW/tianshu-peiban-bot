"""Linux deployment owner lock, retained across exec; no product internals imported."""

import os
import sys
from pathlib import Path


def main():
    if sys.platform != "linux" or len(sys.argv) < 4 or os.geteuid() != 10001:
        print("deployment_runtime_refused", file=sys.stderr)
        return 2
    import fcntl

    state = Path(sys.argv[1])
    if not state.is_absolute() or state.is_symlink() or not state.is_dir():
        print("deployment_state_refused", file=sys.stderr)
        return 2
    try:
        fd = os.open(
            state / ".deployment-owner.lock",
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.set_inheritable(fd, True)
        os.umask(0o027)
        os.execvp(sys.argv[2], sys.argv[2:])
    except (OSError, ValueError):
        print("deployment_owner_or_exec_refused", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
