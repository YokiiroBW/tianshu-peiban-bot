"""Real loopback HTTPS/mTLS transport tests; Loki backend is an explicit synthetic substitute."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import ssl
import threading
import unittest
from unittest.mock import patch

from helpers import certificates
from guard import Server, State, route
from query import LokiClient, QueryError
from test_observability import WorkspaceTest


class BackendHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.observed.append((self.path, self.headers.get("X-Scope-OrgID")))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {"status": "success", "data": {"resultType": "streams", "result": []}}
            ).encode()
        )

    def do_POST(self):
        self.server.observed.append((self.path, self.headers.get("X-Scope-OrgID")))
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(self.server.push_status)
        self.end_headers()
        self.wfile.write(b"UPSTREAM_SECRET_CANARY")


class TransportTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        certificates(self.root)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.root / "loki.pem", self.root / "loki.key")
        context.load_verify_locations(self.root / "client-ca.pem")
        context.verify_mode = ssl.CERT_REQUIRED
        backend = ThreadingHTTPServer(("127.0.0.1", 0), BackendHandler)
        backend.socket = context.wrap_socket(backend.socket, server_side=True)
        backend.observed, backend.push_status = [], 204
        self.backend = backend
        self.start_server(backend)
        self.tokens = {
            role: char * 40
            for role, char in (("writer", "w"), ("query", "q"), ("metrics", "m"))
        }
        refs = {}
        for role, token in self.tokens.items():
            path = self.root / (role + ".token")
            path.write_text(token)
            refs[role] = str(path)
        settings = {
            "tokens": refs,
            "storage_roots": {"guard": str(self.root)},
            "reserve_bytes": 0,
        }
        client = LokiClient(
            f"https://127.0.0.1:{backend.server_port}",
            self.root / "ca.pem",
            certificate=self.root / "client.pem",
            key=self.root / "client.key",
        )
        self.state = State(settings, client)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.root / "guard.pem", self.root / "guard.key")
        self.guard = Server(("127.0.0.1", 0), self.state, context)
        self.start_server(self.guard)

    def start_server(self, server):
        worker = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        worker.start()

        def close():
            server.shutdown()
            server.server_close()
            worker.join(2)

        self.addCleanup(close)

    def client(self, role=None):
        return LokiClient(
            f"https://127.0.0.1:{self.guard.server_port}",
            self.root / "ca.pem",
            self.tokens.get(role),
        )

    def test_query_roles_and_no_administrative_routes(self):
        path = "/loki/api/v1/query_range?query=test"
        for role in (None, "writer", "metrics"):
            self.assertEqual(self.client(role).request(path)[0], 401)
        self.assertEqual(self.client("query").request(path)[0], 200)
        self.assertEqual(self.backend.observed[-1][1], "tianshu")
        self.assertEqual(self.client("query").request("/config")[0], 404)
        self.assertEqual(
            self.client("query").request("/loki/api/v1/delete", "POST", b"x")[0], 404
        )
        self.assertEqual(self.client("query").request("/metrics")[0], 401)
        self.assertEqual(self.client("metrics").request("/metrics")[0], 200)
        self.assertEqual(self.client("metrics").request("/backend-metrics")[0], 200)
        self.assertEqual(self.client("query").request("/backend-metrics")[0], 401)

    def test_push_roles_capacity_and_backend_error_redaction(self):
        path = "/loki/api/v1/push"
        self.assertEqual(self.client("query").request(path, "POST", b"{}")[0], 401)
        self.assertEqual(self.client("writer").request(path, "POST", b"{}")[0], 204)
        before = len(self.backend.observed)
        with patch.object(self.state, "space_available", return_value=False):
            status, body, _ = self.client("writer").request(path, "POST", b"{}")
            self.assertEqual(status, 503)
        self.assertEqual(len(self.backend.observed), before)
        self.backend.push_status = 500
        status, body, _ = self.client("writer").request(path, "POST", b"SECRET_CANARY")
        self.assertEqual(status, 500)
        self.assertNotIn(b"CANARY", body)
        self.assertNotIn(b"CANARY", self.state.render_metrics())

    def test_direct_backend_requires_mtls_and_wrong_ca_refused(self):
        direct = LokiClient(
            f"https://127.0.0.1:{self.backend.server_port}", self.root / "ca.pem"
        )
        with self.assertRaises(QueryError):
            direct.request("/ready")
        other = self.root / "wrong"
        certificates(other)
        client = LokiClient(
            f"https://127.0.0.1:{self.guard.server_port}",
            other / "ca.pem",
            self.tokens["query"],
        )
        with self.assertRaises(QueryError):
            client.request("/ready")

    def test_noncanonical_route_refused(self):
        for path in (
            "https://elsewhere/loki/api/v1/query",
            "/loki/api/v1/../push",
            "/loki/api/v1/%71uery",
            "//loki/api/v1/query",
            "/loki/api/v1/push?x=1",
        ):
            self.assertIsNone(route("POST", path))


if __name__ == "__main__":
    unittest.main()
