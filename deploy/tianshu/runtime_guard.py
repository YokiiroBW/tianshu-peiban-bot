"""Linux deployment owner lock, retained across exec; no product internals imported."""

import os
import sys
from pathlib import Path


def arguments(argv):
    """Retain the native one-directory CLI, or lock all unified module directories."""
    if argv and argv[0] != "--state-dir":
        if len(argv) < 3:
            raise ValueError("missing runtime command")
        return [argv[0]], argv[1:]
    paths = []
    while len(argv) >= 2 and argv[0] == "--state-dir":
        paths.append(argv[1])
        argv = argv[2:]
    if not paths or len(argv) < 3 or argv[0] != "--":
        raise ValueError("missing runtime directories or command")
    return list(dict.fromkeys(paths)), argv[1:]


def acquire(paths):
    """Hold the same native owner locks; release all if any directory is owned."""
    import fcntl

    descriptors = []
    identities = set()
    try:
        for value in sorted(set(paths)):
            state = Path(value)
            if not state.is_absolute() or state.is_symlink() or not state.is_dir():
                raise ValueError("invalid deployment state")
            fd = os.open(
                state / ".deployment-owner.lock",
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                0o600,
            )
            entry = os.fstat(fd)
            identity = (entry.st_dev, entry.st_ino)
            if identity in identities:
                os.close(fd)
                continue
            descriptors.append(fd)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.set_inheritable(fd, True)
            identities.add(identity)
        return descriptors
    except BaseException:
        for fd in descriptors:
            os.close(fd)
        raise


def main():
    if sys.platform != "linux" or os.geteuid() != 10001:
        print("deployment_runtime_refused", file=sys.stderr)
        return 2
    descriptors = []
    try:
        paths, command = arguments(sys.argv[1:])
        descriptors = acquire(paths)
        os.umask(0o027)
        os.execvp(command[0], command)
    except (OSError, ValueError):
        print("deployment_owner_or_exec_refused", file=sys.stderr)
        return 2
    finally:
        for fd in descriptors:
            os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())
