"""Nonblocking TLS I/O with one monotonic deadline; no background socket readers."""

import errno
import io
import ipaddress
import queue
import select
import socket
import ssl
import threading
import time


class DeadlineExceeded(TimeoutError):
    pass


class Deadline:
    def __init__(self, seconds=10, parent=None, cancel=None):
        if not 0 < seconds <= 10:
            raise ValueError("deadline_budget_invalid")
        self.end = min(
            time.monotonic() + seconds, parent.end if parent else float("inf")
        )
        self.parent, self.external = parent, cancel
        self.cancelled = threading.Event()

    def remaining(self):
        if self.parent:
            self.parent.remaining()
        remaining = self.end - time.monotonic()
        if (
            self.cancelled.is_set()
            or (self.external and self.external.is_set())
            or remaining <= 0
        ):
            raise DeadlineExceeded("transport_deadline")
        return remaining

    def wait(self, sock, writing=False):
        while True:
            timeout = min(self.remaining(), 0.05)
            readable, writable, _ = select.select(
                [] if writing else [sock], [sock] if writing else [], [], timeout
            )
            self.remaining()
            if readable or writable:
                return


class _Reader(io.RawIOBase):
    def __init__(self, connection):
        super().__init__()
        self.connection = connection

    def readable(self):
        return True

    def readinto(self, buffer):
        return self.connection.recv_into(buffer)

    def close(self):
        if not self.closed:
            self.connection._release_file()
        super().close()


class Connection:
    def __init__(self, sock, deadline):
        self.socket, self.deadline = sock, deadline
        self._files, self._closed = 0, False
        sock.setblocking(False)

    def _perform(self, operation, writing=False):
        while True:
            self.deadline.remaining()
            try:
                return operation()
            except ssl.SSLWantReadError:
                self.deadline.wait(self.socket)
            except ssl.SSLWantWriteError:
                self.deadline.wait(self.socket, True)
            except BlockingIOError:
                self.deadline.wait(self.socket, writing)

    def handshake(self):
        self._perform(self.socket.do_handshake)

    def recv_into(self, buffer):
        return self._perform(lambda: self.socket.recv_into(buffer))

    def sendall(self, data):
        view = memoryview(data)
        while view:
            sent = self._perform(lambda: self.socket.send(view[:65536]), True)
            if not sent:
                raise OSError("transport_closed")
            view = view[sent:]

    def makefile(self, mode, buffering=-1):
        if mode != "rb":
            raise ValueError("read_file_only")
        self._files += 1
        raw = _Reader(self)
        return (
            raw
            if buffering == 0
            else io.BufferedReader(
                raw, io.DEFAULT_BUFFER_SIZE if buffering < 0 else buffering
            )
        )

    def shutdown(self, how):
        self.socket.shutdown(how)

    def close(self):
        self._closed = True
        if not self._files:
            self.socket.close()

    def _release_file(self):
        self._files -= 1
        if self._closed and not self._files:
            self.socket.close()

    def abort(self):
        self.socket.close()

    def fileno(self):
        return self.socket.fileno()


def accept_tls(raw, context, deadline):
    raw.setblocking(False)
    tls = context.wrap_socket(raw, server_side=True, do_handshake_on_connect=False)
    connection = Connection(tls, deadline)
    try:
        connection.handshake()
        return connection
    except BaseException:
        connection.close()
        raise


class _Resolver:
    """At most one OS resolver worker and one queued job; never one thread per call.

    An OS getaddrinfo call cannot be forcibly cancelled in portable Python. Callers
    still leave at their deadline; a stuck worker makes later uncached DNS fail
    closed. IP literals bypass it. This bounded daemon is not a socket read worker.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = queue.Queue(maxsize=1)
        self.pending, self.cache = {}, {}
        threading.Thread(target=self._run, daemon=True, name="obs-dns-resolver").start()

    def _run(self):
        while True:
            key, job = self.jobs.get()
            try:
                job["addresses"] = socket.getaddrinfo(*key, type=socket.SOCK_STREAM)
            except Exception:
                job["error"] = True
            with self.lock:
                if "addresses" in job:
                    if len(self.cache) >= 64:
                        self.cache.pop(next(iter(self.cache)))
                    self.cache[key] = (time.monotonic() + 30, job["addresses"])
                self.pending.pop(key, None)
                job["done"].set()

    def resolve(self, host, port, deadline):
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address:
            family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
            return [(family, socket.SOCK_STREAM, 0, "", (host, port))]
        key = (host, port)
        with self.lock:
            cached = self.cache.get(key)
            if cached and cached[0] > time.monotonic():
                return cached[1]
            job = self.pending.get(key)
            if job is None:
                job = {"done": threading.Event()}
                try:
                    self.jobs.put_nowait((key, job))
                except queue.Full:
                    raise OSError("dns_capacity") from None
                self.pending[key] = job
        while not job["done"].wait(min(0.05, deadline.remaining())):
            pass
        deadline.remaining()
        if job.get("error"):
            raise OSError("dns_unavailable")
        return job["addresses"]


_resolver = None
_resolver_lock = threading.Lock()


def connect_tls(host, port, context, deadline):
    global _resolver
    with _resolver_lock:
        if _resolver is None:
            _resolver = _Resolver()
    addresses = _resolver.resolve(host, port, deadline)
    for family, kind, protocol, _, address in addresses:
        raw, connection = None, None
        try:
            deadline.remaining()
            raw = socket.socket(family, kind, protocol)
            raw.setblocking(False)
            code = raw.connect_ex(address)
            if code not in {
                0,
                errno.EINPROGRESS,
                errno.EWOULDBLOCK,
                errno.EALREADY,
                10035,
                10036,
                10037,
            }:
                raise OSError("connect_failed")
            if code:
                deadline.wait(raw, True)
                if raw.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR):
                    raise OSError("connect_failed")
            tls = context.wrap_socket(
                raw, server_hostname=host, do_handshake_on_connect=False
            )
            connection = Connection(tls, deadline)
            connection.handshake()
            return connection
        except DeadlineExceeded:
            if connection:
                connection.close()
            elif raw:
                raw.close()
            raise
        except (OSError, ValueError):
            if connection:
                connection.close()
            elif raw:
                raw.close()
    raise OSError("connect_failed")
