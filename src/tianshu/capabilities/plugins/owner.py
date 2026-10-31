"""One supervising process per plugin ledger, released automatically after a crash."""

import os

from ..contracts import Fault


class Owner:
    def __init__(self, path):
        self.path, self.stream = path, None

    def acquire(self):
        stream = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                if stream.tell() == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            raise Fault("plugin_host_busy", 409) from None
        self.stream = stream

    def close(self):
        if self.stream is None:
            return
        if os.name == "nt":
            import msvcrt

            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        self.stream.close()
        self.stream = None
