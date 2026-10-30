"""Synthetic Vector/Loki hard-restart test. Run only in an isolated container."""

import json
import os
import signal
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def wait_until(fn, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = fn()
            if value:
                return value
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.2)
    raise RuntimeError("synthetic_condition_timeout")


def main():
    processes = []
    with tempfile.TemporaryDirectory(prefix="synthetic-vector-") as tmp:
        root = Path(tmp)
        (root / "vector").mkdir()
        (root / "loki").mkdir()
        (root / "source").mkdir()
        log = root / "source" / "companion.jsonl"
        log.write_text('one\ntwo\nthree\n')
        active = root / "source" / "companion.jsonl.1"
        active.write_bytes(b"")
        vector = {
            "data_dir": str(root / "vector"),
            "sources": {"companion": {"type": "file", "include": [str(root / "source" / "*.jsonl"), str(root / "source" / "*.jsonl.[0-9]*")],
                "read_from": "beginning", "ignore_checkpoints": False,
                "fingerprint": {"strategy": "checksum", "lines": 1},
                "max_line_bytes": 4095, "glob_minimum_cooldown_ms": 1000}},
            "sinks": {"loki": {"type": "loki", "inputs": ["companion"],
                "endpoint": "http://127.0.0.1:13100",
                "labels": {"stack": "synthetic"}, "encoding": {"codec": "json"},
                "acknowledgements": {"enabled": True}, "healthcheck": {"enabled": False},
                "buffer": {"type": "disk", "max_size": 268435488, "when_full": "block"},
                "batch": {"max_bytes": 262144, "timeout_secs": 1},
                "request": {"timeout_secs": 2, "retry_initial_backoff_secs": 1}}},
        }
        loki = {
            "auth_enabled": False,
            "server": {"http_listen_address": "127.0.0.1", "http_listen_port": 13100,
                       "grpc_listen_address": "127.0.0.1", "grpc_listen_port": 13101},
            "common": {"path_prefix": str(root / "loki"), "replication_factor": 1,
                       "instance_addr": "127.0.0.1",
                       "ring": {"kvstore": {"store": "inmemory"}}},
            "memberlist": {"bind_addr": ["127.0.0.1"], "advertise_addr": "127.0.0.1"},
            "schema_config": {"configs": [{"from": "2020-01-01", "store": "tsdb",
                "object_store": "filesystem", "schema": "v13",
                "index": {"prefix": "index_", "period": "24h"}}]},
            "storage_config": {"filesystem": {"directory": str(root / "loki" / "chunks")}},
            "limits_config": {"allow_structured_metadata": False,
                              "retention_period": "24h", "deletion_mode": "filter-and-delete"},
            "compactor": {"working_directory": str(root / "loki" / "compactor"),
                          "retention_enabled": True, "delete_request_store": "filesystem"},
            "analytics": {"reporting_enabled": False},
        }
        (root / "vector.json").write_text(json.dumps(vector))
        (root / "loki.json").write_text(json.dumps(loki))
        output = (root / "process.log").open("wb")
        try:
            def start(command):
                proc = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT)
                processes.append(proc)
                return proc

            first = start(["vector", "--config", str(root / "vector.json")])
            checkpoint = root / "vector" / "companion" / "checkpoints.json"
            def is_acknowledged():
                value = json.loads(checkpoint.read_bytes())
                return any(c["position"] == log.stat().st_size for c in value["checkpoints"])
            wait_until(is_acknowledged)
            if first.poll() is not None:
                raise RuntimeError("vector_exited_before_restart")
            # Reclaim the closed source using the actual candidate code. The
            # only remaining copy is then Vector's acknowledged disk buffer.
            sys.path.insert(0, "/tmp/log-simple-fixtures.zip")
            from vector_source_retention import prune_sources
            result = prune_sources({"companion": str(root / "source")}, root / "vector")
            if result["source_retention_deleted_segments"] != 1 or log.exists() or not active.exists():
                raise RuntimeError("synthetic_source_reclaim_failed")
            os.kill(first.pid, signal.SIGKILL)
            first.wait(timeout=5)
            server = start(["loki", "-config.file=" + str(root / "loki.json")])
            def ready():
                with urllib.request.urlopen("http://127.0.0.1:13100/ready", timeout=1) as res:
                    return res.status == 200
            wait_until(ready)
            second = start(["vector", "--config", str(root / "vector.json")])
            def count():
                q = urllib.parse.urlencode({"query": 'sum(count_over_time({stack="synthetic"}[10m]))'})
                with urllib.request.urlopen("http://127.0.0.1:13100/loki/api/v1/query?" + q, timeout=2) as res:
                    data = json.load(res)["data"]["result"]
                return sum(float(item["value"][1]) for item in data)
            wait_until(lambda: count() == 3)
            time.sleep(2)
            if count() != 3 or second.poll() is not None or server.poll() is not None:
                raise RuntimeError("synthetic_recovery_duplicate_or_process_exit")
            print("synthetic_vector_checkpoint_buffer_sigkill_restart_loki_count=3: PASS")

            # Exercise both clear boundaries in real Loki, including a late
            # replay whose collection time is new but event time is old.
            cutoff_ms = int(time.time() * 1000) - 20000
            def iso(ms):
                return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()
            streams = []
            for kind, event_offset, collect_offset in [
                ("old", -1000, -1000), ("buffered", 1000, -1000),
                ("replayed", -1000, 1000), ("new", 1000, 1000),
            ]:
                streams.append({"stream": {"stack": "clear-synthetic", "kind": kind},
                    "values": [[str((cutoff_ms + collect_offset) * 1000000),
                                json.dumps({"timestamp": iso(cutoff_ms + event_offset)})]]})
            payload = json.dumps({"streams": streams}).encode()
            request = urllib.request.Request("http://127.0.0.1:13100/loki/api/v1/push",
                data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=3) as response:
                if response.status != 204:
                    raise RuntimeError("synthetic_clear_push_failed")
            expression = (' | json __ts_clear_timestamp="timestamp" '
                '| label_format __ts_clear_event=`{{ unixEpochMillis (toDateInZone "2006-01-02T15:04:05.999999999Z07:00" "UTC" .__ts_clear_timestamp) }}`,'
                '__ts_clear_collect=`{{ unixEpochMillis __timestamp__ }}` '
                f'| __ts_clear_event > {cutoff_ms} | __ts_clear_collect > {cutoff_ms} '
                '| drop __ts_clear_timestamp,__ts_clear_event,__ts_clear_collect ')
            def visible(query):
                q = urllib.parse.urlencode({"query": query, "start": str((cutoff_ms - 5000)*1000000),
                                           "end": str((cutoff_ms + 5000)*1000000), "limit": 100})
                with urllib.request.urlopen("http://127.0.0.1:13100/loki/api/v1/query_range?" + q, timeout=3) as res:
                    return {item["stream"]["kind"] for item in json.load(res)["data"]["result"]}
            query = '{stack="clear-synthetic"}'
            wait_until(lambda: len(visible(query)) == 4)
            if visible(query + expression) != {"new"}:
                raise RuntimeError("synthetic_clear_filter_failed")
            print("synthetic_loki_clear_old_buffered_replayed_new: PASS")
            # The caller concatenates the actual clear_retention module with this
            # standalone fixture when running in the isolated test container.
            try:
                control_class = ClearControl
            except NameError:
                from clear_retention import ClearControl as control_class
            class Backend:
                def request(self, path, method, body=None):
                    req = urllib.request.Request("http://127.0.0.1:13100" + path,
                                                 data=body, method=method)
                    with urllib.request.urlopen(req, timeout=5) as response:
                        return response.status, b"", "application/json"
            control = control_class(root)
            control.clear(now=cutoff_ms / 1000)
            control.submit(Backend(), threading.Lock(), now=int(time.time()))
            if control_class(root).last_delete == 0:
                raise RuntimeError("synthetic_clear_delete_not_persisted")
            print("synthetic_loki_native_clear_delete_accepted: PASS")

            # Exercise the actual candidate guard against this same isolated Loki.
            sys.path.insert(0, "/tmp/log-simple-fixtures.zip")
            from guard import Server, State
            from query import LokiClient
            cert, key = root / "guard.pem", root / "guard.key"
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=localhost",
                "-addext", "subjectAltName=DNS:localhost"], check=True, stdout=output, stderr=output)
            tokens = {}
            for role, char in (("writer", "w"), ("query", "q"), ("metrics", "m")):
                token = root / (role + ".token")
                token.write_text(char * 40)
                tokens[role] = str(token)
            logroot = root / "empty-logs"
            logroot.mkdir()
            state_dir = root / "guard-data"
            state_dir.mkdir()
            # A deliberately invalid legacy file must never be read.
            (state_dir / "integrity.sqlite").write_bytes(b"synthetic broken legacy")
            class DirectBackend:
                def request(self, path, method="GET", body=None, content_type=None,
                            encoding=None, *, deadline=None):
                    req = urllib.request.Request("http://127.0.0.1:13100" + path, data=body, method=method)
                    with urllib.request.urlopen(req, timeout=5) as response:
                        return response.status, response.read(), response.headers.get("Content-Type", "application/json")
            state = State({"tokens": tokens, "ledger": str(state_dir / "integrity.sqlite"),
                "storage_roots": {"guard": str(state_dir)}, "reserve_bytes": 0,
                "log_roots": {"companion": str(logroot)}, "log_budgets": {"companion": 1024**3},
                "interval_seconds": 0.1}, DirectBackend())
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.load_cert_chain(cert, key)
            guard = Server(("127.0.0.1", 0), state, tls)
            serving = threading.Thread(target=guard.serve_forever, daemon=True)
            monitoring = threading.Thread(target=state.run_monitor, daemon=True)
            serving.start()
            monitoring.start()
            clients = []
            try:
                wait_until(state.monitor_ready)
                url = f"https://localhost:{guard.server_port}"
                read = LokiClient(url, cert, token="q" * 40)
                writer = LokiClient(url, cert, token="w" * 40)
                clients.extend([read, writer])
                status, _, _ = read.request("/loki/api/v1/query?" + urllib.parse.urlencode(
                    {"query": 'sum(count_over_time({stack="synthetic"}[10m]))'}))
                if status != 200:
                    raise RuntimeError("synthetic_guard_query_failed")
                if writer.request("/retention/clear", "POST", body=b"")[0] != 202:
                    raise RuntimeError("synthetic_guard_clear_failed")
                now_ms = int(time.time() * 1000) + 2000
                payload = json.dumps({"streams": [
                    {"stream": {"stack": "guard-synthetic", "kind": "new"},
                     "values": [[str(now_ms * 1000000), json.dumps({"timestamp": iso(now_ms)})]]},
                    {"stream": {"stack": "guard-synthetic", "kind": "replayed"},
                     "values": [[str(now_ms * 1000000), json.dumps({"timestamp": iso(now_ms - 60000)})]]},
                ]}).encode()
                request = urllib.request.Request("http://127.0.0.1:13100/loki/api/v1/push",
                    data=payload, headers={"Content-Type": "application/json"}, method="POST")
                urllib.request.urlopen(request, timeout=3).close()
                params = urllib.parse.urlencode({"query": '{stack="guard-synthetic"}',
                    "start": str((now_ms - 10000) * 1000000), "end": str((now_ms + 10000) * 1000000)})
                def guard_visible():
                    status, body, _ = read.request("/loki/api/v1/query_range?" + params)
                    if status != 200:
                        return set()
                    return {item["stream"]["kind"] for item in json.loads(body)["data"]["result"]}
                wait_until(lambda: guard_visible() == {"new"})
                if (state_dir / "integrity.sqlite").read_bytes() != b"synthetic broken legacy":
                    raise RuntimeError("synthetic_guard_touched_legacy")
                print("synthetic_guard_tls_direct_query_clear_replay_without_ledger: PASS")
            finally:
                for client in clients:
                    client.close()
                state.stop.set()
                guard.shutdown()
                guard.server_close()
                serving.join(3)
                monitoring.join(3)
        except Exception:
            output.flush()
            print((root / "process.log").read_text(errors="replace")[-6000:])
            raise
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            output.close()


if __name__ == "__main__":
    main()
