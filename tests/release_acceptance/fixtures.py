"""Runner self-test doubles. This is NOT product initialization or a four-product deployment."""

import copy
import ipaddress
import json
import os
import secrets
import ssl
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from acceptance.evidence import canonical
from acceptance.inputs import ROLES


def certificates(directory):
    # Optional test-only dependency; the runner itself uses only Python 3.12 stdlib.
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "DEP-D temporary fixture only")]
    )
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    ca, private = (
        Path(directory) / "fixture-ca.pem",
        Path(directory) / "fixture-key.pem",
    )
    ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return ca, private


class Harness:
    def __init__(self, directory, binding, tls=False, mutation=None):
        self.directory, self.binding, self.tls, self.mutation = (
            Path(directory),
            binding,
            tls,
            mutation,
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        self.counts = {
            "model_calls": 0,
            "send_calls": 0,
            "candidate_count": 0,
            "pending_candidates": 0,
        }
        self.turns, self.submissions, self.sessions, self.faults, self.events = (
            [],
            {},
            {},
            set(),
            [],
        )
        self.servers, self.threads, self.saved_env = [], [], {}
        self.instances = {role: str(uuid.uuid4()) for role in ROLES}
        self.sequences = dict.fromkeys(ROLES, 0)
        self.password = secrets.token_urlsafe(30)
        self.token = secrets.token_urlsafe(30)
        self.catalog = {
            role: {
                "events": ["request.finished"],
                "error_codes": ["dependency_unavailable"],
            }
            for role in ROLES
        }
        (self.directory / "catalog.json").write_bytes(canonical(self.catalog))

    def __enter__(self):
        if self.tls:
            self.ca, self.key = certificates(self.directory)
        endpoints = {}
        for role in (*ROLES, "adapter"):
            harness = self

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_):
                    pass

                def do_GET(self):
                    self.respond(None)

                def do_POST(self):
                    self.respond(
                        json.loads(
                            self.rfile.read(int(self.headers.get("Content-Length", 0)))
                        )
                    )

                def respond(self, body):
                    status, value, cookie = harness.handle(
                        self.server.role, self.path, body, self.headers
                    )
                    raw = canonical(value)
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    if cookie:
                        self.send_header(
                            "Set-Cookie",
                            "tianshu_session="
                            + cookie
                            + "; Path=/; HttpOnly; SameSite=Strict"
                            + ("; Secure" if harness.tls else ""),
                        )
                    self.end_headers()
                    self.wfile.write(raw)

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.role = role
            if self.tls:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(self.ca, self.key)
                server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.servers.append(server)
            self.threads.append(thread)
            endpoints[role] = {
                "url": f"{'https' if self.tls else 'http'}://127.0.0.1:{server.server_port}",
                "diagnostics_token_env": "DEP_D_FIXTURE_TOKEN",
            }
            if self.tls:
                endpoints[role]["ca_file"] = str(self.ca)
        self.config = {
            "input_version": "dep-d/1",
            "scope": "synthetic_isolated",
            "mode": "local",
            "runtime_kind": "synthetic",
            "model_kind": "recorded",
            "expected_config_sha256": {
                role: str(i + 1) * 64 for i, role in enumerate(ROLES)
            },
            "case_timeout_seconds": 1,
            "endpoints": {role: endpoints[role] for role in ROLES},
            "adapter": {"http": endpoints["adapter"]},
            "web": {
                "username_env": "DEP_D_FIXTURE_USER",
                "password_env": "DEP_D_FIXTURE_PASSWORD",
                "conversation": "synthetic-conversation",
                "actor": "synthetic-actor",
            },
            "features": {"automatic_memory": True, "chat_archive": True},
            "logs": {
                "catalog_file": str(self.directory / "catalog.json"),
                "success": {
                    "required": [[r, "request.finished", "succeeded"] for r in ROLES]
                },
                "failure": {
                    "required": [["gateway", "request.finished", "unknown"]],
                    "forbidden": [["gateway", "request.finished", "succeeded"]],
                },
            },
            "canary_env_names": ["DEP_D_FIXTURE_PASSWORD", "DEP_D_FIXTURE_TOKEN"],
        }
        for name, value in {
            "DEP_D_FIXTURE_USER": "fixture-user",
            "DEP_D_FIXTURE_PASSWORD": self.password,
            "DEP_D_FIXTURE_TOKEN": self.token,
        }.items():
            self.saved_env[name] = os.environ.get(name)
            os.environ[name] = value
        return self

    def __exit__(self, *_):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(2)
        for name, value in self.saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def emit(self, role, correlation, outcome):
        self.sequences[role] += 1
        value = {
            "schema_version": "1.0.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": role,
            "instance_id": self.instances[role],
            "sequence": self.sequences[role],
            "event_id": str(uuid.uuid4()),
            "level": "INFO",
            "event": "request.finished",
            "outcome": outcome,
            "correlation_id": correlation,
            "duration_ms": 1,
            "error_code": None,
        }
        if self.mutation == "secret_log":
            value["message"] = self.password
        self.events.append(json.dumps(value) + "\n")

    def handle(self, role, path, body, headers):
        if role == "adapter":
            return 200, self.control(body), None
        if path == "/health/live":
            return 200, {"status": "alive"}, None
        if path == "/health/ready":
            if headers.get("Authorization") != "Bearer " + self.token:
                return 401, {"code": "unauthorized"}, None
            failed = "logs_unavailable:" + role in self.faults
            if self.mutation == "false_ready":
                failed = False
            keys = {
                "platform": "config contract store sidecars web_static tls credentials logging runtime",
                "companion": "configuration logs runtime dependencies",
                "memory": "configuration contract database guard mode log assembled owner remote",
                "gateway": "configuration contracts runtime ledger logging platform model native",
            }
            checks = dict.fromkeys(keys[role].split(), "ok")
            log_key = {
                "platform": "logging",
                "companion": "logs",
                "memory": "log",
                "gateway": "logging",
            }[role]
            checks[log_key] = "failed" if failed else "ok"
            if role == "gateway":
                checks.update(
                    platform="not_verified",
                    model="not_verified",
                    native="not_configured",
                )
            return (
                503 if failed else 200,
                {
                    "status": "not_ready" if failed else "ready",
                    "service": role,
                    "checks": checks,
                },
                None,
            )
        cookie = headers.get("Cookie", "").removeprefix("tianshu_session=")
        session = self.sessions.get(cookie)
        if path == "/api/web/session":
            if not session:
                cookie = secrets.token_hex(16)
                session = {"authenticated": False, "csrf": secrets.token_hex(16)}
                self.sessions[cookie] = session
            return 200, session, cookie
        origin = self.config["endpoints"]["platform"]["url"]
        if (
            headers.get("Origin") != origin
            or not session
            or headers.get("X-CSRF-Token") != session["csrf"]
        ):
            return 403, {"code": "forbidden"}, None
        if path == "/api/web/login":
            if body != {"username": "fixture-user", "password": self.password}:
                return 401, {"code": "unauthorized"}, None
            self.sessions.pop(cookie)
            cookie = secrets.token_hex(16)
            session = {"authenticated": True, "csrf": secrets.token_hex(16)}
            self.sessions[cookie] = session
            return 200, session, cookie
        if not session["authenticated"]:
            return 401, {"code": "unauthorized"}, None
        if path == "/api/web/messages":
            if (
                any(x.startswith("logs_unavailable:") for x in self.faults)
                or "model_revoked" in self.faults
            ):
                return 503, {"code": "dependency_unavailable"}, None
            key = body["client_id"]
            if key in self.submissions:
                if self.mutation == "resend_unknown":
                    self.counts["model_calls"] += 1
                return 200, self.submissions[key], None
            self.counts["model_calls"] += 1
            unknown = "model_disconnect_after_accept" in self.faults
            pending = "model_timeout" in self.faults
            timed_out = False
            if pending:
                self.timeout_requests += 1
                timed_out = self.timeout_requests == 1
            message_id, turn_id = "message:" + key, "turn:" + key
            phase = (
                "closed_unknown"
                if unknown
                else "failed"
                if timed_out
                else "generating"
                if pending
                else "sent"
            )
            reply = {
                "reply_id": "reply:" + key,
                "state": "unknown" if unknown else "sent",
                "text": None if unknown else "Synthetic recorded reply",
                "content_state": "unavailable" if unknown else "available",
            }
            self.turns.append(
                {
                    "turn": {"turn_id": turn_id, "version": 1, "phase": phase},
                    "messages": [{"message_id": message_id}],
                    "replies": [] if pending else [reply],
                }
            )
            result = {"message_id": message_id, "state": "accepted", "result": {}}
            self.submissions[key] = result
            if not unknown and not pending:
                self.counts["send_calls"] += 1
                self.counts["candidate_count"] += 1
                if self.mutation == "queue_growth":
                    self.counts["pending_candidates"] += 1
            for service in ROLES:
                outcome = "unknown" if unknown else "succeeded"
                if unknown and self.mutation == "false_success":
                    outcome = "succeeded"
                self.emit(service, headers.get("X-Tianshu-Correlation-Id"), outcome)
            return 200, result, None
        if path == "/api/web/snapshot":
            return (
                200,
                {
                    "snapshot": {
                        "history": copy.deepcopy(self.turns),
                        "active_turns": [],
                    }
                },
                None,
            )
        if path == "/api/web/cancel":
            turn = next(
                t for t in self.turns if t["turn"]["turn_id"] == body["turn_id"]
            )
            turn["turn"]["phase"] = "cancelled"
            return 200, {"state": "cancelled"}, None
        return 404, {"code": "not_found"}, None

    def control(self, request):
        operation = request["operation"]
        result = {"adapter_version": "dep-d/1", "scope": "synthetic_isolated"}
        if operation == "identity":
            result.update(
                runtime_kind="synthetic",
                release_sha256=self.binding["manifest_sha256"],
                products=self.binding["products"],
            )
        elif operation == "configuration":
            result.update(
                basis="runtime_loaded",
                loaded_config_sha256=self.config["expected_config_sha256"],
            )
        elif operation == "state":
            result.update(self.counts)
        elif operation == "memory_candidate":
            result.update(
                turn_id=request["turn_id"],
                candidate_id="candidate:fixture",
                state="queued",
            )
        elif operation == "memory_finalized":
            result.update(
                candidate_id=request["candidate_id"],
                state="queued" if self.mutation == "queued_only" else "committed",
                memory_id="memory:fixture",
                revision=1,
                readback_id="memory:fixture",
            )
        elif operation == "archive":
            result.update(
                turn_id=request["turn_id"],
                archive_id="archive:fixture",
                state="archived",
                readback_id="archive:fixture",
            )
        elif operation == "fault":
            if request["enabled"]:
                self.faults.add(request["name"])
                if request["name"] == "model_timeout":
                    self.timeout_requests = 0
            else:
                self.faults.discard(request["name"])
                result["restored"] = self.mutation != "cleanup_failure"
        elif operation == "restart":
            result["before_instances"] = self.instances.copy()
            self.instances = {role: str(uuid.uuid4()) for role in ROLES}
            self.sequences = dict.fromkeys(ROLES, 0)
            result.update(after_instances=self.instances, restarted=list(ROLES))
            self.sessions.clear()
            if self.mutation == "restart_loss":
                self.turns.clear()
        elif operation == "revoke_source":
            for turn in self.turns:
                if any(
                    m["message_id"] == request["message_id"] for m in turn["messages"]
                ):
                    for reply in turn["replies"]:
                        reply.update(text=None, content_state="unavailable")
            result["revoked"] = True
        elif operation == "recall_revoked_source":
            result.update(excluded=True, source_revision=2)
        elif operation == "cancel_observation":
            result.update(
                late_reply_deliveries=0, duplicate_model_calls=0, timeout_observed=True
            )
        elif operation == "logs":
            result["lines"] = [
                line
                for line in self.events
                if json.loads(line)["correlation_id"] == request["correlation_id"]
            ]
        else:
            result["status"] = "unsupported"
        return result
