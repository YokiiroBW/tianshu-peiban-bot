"""Signal-safe request flag and bounded joining of every backend owner."""

import signal
import threading
import time


class SignalStop:
    def __init__(self):
        self.requested = False
        self.previous = {}

    def __enter__(self):
        def request(_number, _frame):
            # No locks, I/O, shutdown(), close() or thread joins in a signal handler.
            self.requested = True

        for number in (signal.SIGTERM, signal.SIGINT):
            self.previous[number] = signal.signal(number, request)
        return self

    def __exit__(self, *_):
        for number, handler in self.previous.items():
            signal.signal(number, handler)


def run(server, state, backend, stop_requested, shutdown_seconds=10.0):
    """0 only after listener, monitor and request threads have stopped and closed.

    A stuck filesystem/foreign extension may not be cancellable. On timeout, leave
    backend unclosed while its owners still exist and return 2. CLI exits nonzero;
    daemon threads are not a claim of a successful durable shutdown.
    """
    failed = threading.Event()
    server.timeout = 0.05

    def listen():
        try:
            while not state.stop.is_set():
                server.handle_request()
        except BaseException:
            failed.set()

    def monitor():
        try:
            state.run_monitor()
        except BaseException:
            failed.set()
        finally:
            if not state.stop.is_set():
                failed.set()

    listener = threading.Thread(target=listen, name="obs-listener", daemon=True)
    writer = threading.Thread(target=monitor, name="obs-monitor", daemon=True)
    started = []
    try:
        for thread in (writer, listener):
            thread.start()
            started.append(thread)
        while not stop_requested() and not failed.is_set():
            if not listener.is_alive() or not writer.is_alive():
                failed.set()
                break
            time.sleep(0.025)
    except BaseException:
        failed.set()
    finally:
        deadline = time.monotonic() + shutdown_seconds
        state.stop.set()
        server.cancel_requests()
        # Cancellation only signals requests; final resource close is after joins.
        backend.cancel()
        for thread in started:
            thread.join(max(0, deadline - time.monotonic()))
        requests_done = server.join_requests(max(0, deadline - time.monotonic()))
        owners_done = all(not thread.is_alive() for thread in started) and requests_done
        if owners_done:
            server.server_close()
            backend.close()
        if not owners_done or time.monotonic() >= deadline:
            failed.set()
    return 2 if failed.is_set() else 0
