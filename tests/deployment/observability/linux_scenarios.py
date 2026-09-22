"""Actual component scenarios. Invoked only after DEP-G ownership validation."""

import base64
import json
import os
from pathlib import Path
import ssl
import time
import urllib.error
import urllib.request
import uuid

from helpers import emit, event
from acceptance import require
from policy import Policy
from query import LokiClient
from reconcile import compare, read_logs


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Scenarios:
    def __init__(self, docker, binding, evidence, lifecycle):
        self.docker, self.binding, self.evidence = docker, binding, evidence
        self.lifecycle = lifecycle
        self.root, self.project = binding.obs_root, binding.obs_project
        self.policy = Policy(binding.snapshot)
        self.client = LokiClient(
            binding.query_url, binding.ca, binding.query_token.read_text().strip()
        )
        self.generated, invalid, partial = read_logs(binding.logs, self.policy)
        require(not invalid and not partial, "baseline_source_incomplete")
        evidence.artifact("baseline-source.json", self.generated)
        self.instances = {p: str(uuid.uuid4()) for p in binding.logs}
        self.sequences = {p: 0 for p in binding.logs}
        self.start = time.time_ns() - 10**9
        self.batch_index = 0
        context = ssl.create_default_context(cafile=str(binding.ca))
        self.opener = urllib.request.build_opener(
            NoRedirect(),
            urllib.request.HTTPSHandler(context=context),
            urllib.request.ProxyHandler({}),
        )

    def dc(self, *args):
        require(args and args[0] == "exec", "scenario_lifecycle_bypass_refused")
        return self.docker.compose(self.root, self.project, *args)

    def api(self, path, method="GET", data=None, auth=True, viewer=False):
        require(path.startswith("/api/") and "://" not in path, "grafana_path_required")
        headers = {"Content-Type": "application/json"}
        if auth:
            credential = (
                "depi-viewer:" + self.viewer_password
                if viewer
                else "admin:" + self.binding.grafana_password.read_text().strip()
            )
            headers["Authorization"] = (
                "Basic " + base64.b64encode(credential.encode()).decode()
            )
        request = urllib.request.Request(
            self.binding.grafana_url + path,
            data=None if data is None else json.dumps(data).encode(),
            headers=headers,
            method=method,
        )
        try:
            response = self.opener.open(request, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            raw = response.read(8 * 1024**2 + 1)
            require(len(raw) <= 8 * 1024**2, "grafana_response_over_budget")
            try:
                value = json.loads(raw)
            except ValueError:
                value = None
            return response.status, value

    def wait(self, function, seconds=180):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                result = function()
                if result:
                    return result
            except (OSError, ValueError, RuntimeError):
                pass
            time.sleep(2)
        raise ValueError("scenario_deadline_exceeded")

    def batch(self):
        self.batch_index += 1
        for product, directory in self.binding.logs.items():
            records = []
            for _ in range(100):
                self.sequences[product] += 1
                records.append(
                    event(self.sequences[product], product, self.instances[product])
                )
            for row in records:
                self.policy.validate(row)
            # Exclusive scenario scope was checked empty; no pre-existing file is touched.
            emit(directory / "depi.jsonl", records)
            self.generated.extend(records)
        self.evidence.artifact(f"generated-{self.batch_index}.json", self.generated)

    def reconcile(self, case):
        def probe():
            rows = self.client.range('{stack="tianshu"}', self.start, time.time_ns())
            retrieved = [self.policy.line(line.encode() + b"\n") for _, line in rows]
            landed, invalid, partial = read_logs(self.binding.logs, self.policy)
            result = compare(self.generated, landed, retrieved, invalid, partial)
            if result["status"] != "passed":
                return None
            return {"result": result, "retrieved": retrieved, "landed": landed}

        result = self.wait(probe)
        name = self.evidence.artifact(case + ".json", result)
        self.evidence.record(case, "passed", artifacts=[name])

    def prom(self, expression):
        from urllib.parse import urlencode

        status, response = self.api(
            "/api/datasources/proxy/uid/obs-prometheus/api/v1/query?"
            + urlencode({"query": expression})
        )
        require(
            status == 200 and response["status"] == "success", "prometheus_query_failed"
        )
        return response["data"]["result"]

    def metrics_value(self, expression):
        result = self.prom(expression)
        return sum(float(row["value"][1]) for row in result)

    def ui_and_metrics(self):
        def ready():
            status, value = self.api("/api/health", auth=False)
            return status == 200 and value.get("database") == "ok"

        self.wait(ready)
        status, dashboard = self.api("/api/dashboards/uid/tianshu-logs")
        require(
            status == 200 and len(dashboard["dashboard"]["panels"]) == 5,
            "dashboard_unavailable",
        )
        sources = []
        for uid in ("obs-loki", "obs-prometheus"):
            status, value = self.api(f"/api/datasources/uid/{uid}/health")
            require(
                status == 200 and value["status"].lower() == "ok",
                "datasource_unhealthy",
            )
            sources.append({"uid": uid, "status": value["status"]})
        artifact = self.evidence.artifact(
            "grafana-health.json",
            {
                "dashboard_uid": dashboard["dashboard"]["uid"],
                "panels": 5,
                "sources": sources,
            },
        )
        self.evidence.record(
            "grafana_datasources_dashboard", "passed", artifacts=[artifact]
        )
        self.current_case = "prometheus_targets"

        def targets_ready():
            rows = self.prom("up")
            jobs = {row["metric"].get("job"): row["value"][1] for row in rows}
            return (
                rows
                if all(jobs.get(job) == "1" for job in ("guard", "vector", "loki"))
                else None
            )

        targets = self.wait(targets_ready)
        artifact = self.evidence.artifact("prometheus-targets.json", targets)
        self.evidence.record("prometheus_targets", "passed", artifacts=[artifact])
        self.current_case = "grafana_viewer_permissions"
        import secrets

        self.viewer_password = secrets.token_urlsafe(32)
        status, user = self.api(
            "/api/admin/users",
            "POST",
            {
                "name": "DEP-I synthetic viewer",
                "login": "depi-viewer",
                "password": self.viewer_password,
            },
        )
        require(status == 200, "viewer_create_failed")
        status, _ = self.api(
            f"/api/org/users/{user['id']}", "PATCH", {"role": "Viewer"}
        )
        require(status == 200, "viewer_role_failed")
        read_status, _ = self.api("/api/dashboards/uid/tianshu-logs", viewer=True)
        write_status, _ = self.api(
            "/api/dashboards/db",
            "POST",
            {"dashboard": {"title": "DENIED", "uid": "depi-denied"}},
            viewer=True,
        )
        anonymous_status, _ = self.api("/api/dashboards/uid/tianshu-logs", auth=False)
        require(
            read_status == 200 and write_status == 403 and anonymous_status == 401,
            "grafana_permission_failure",
        )
        artifact = self.evidence.artifact(
            "grafana-permissions.json",
            {
                "viewer_read": read_status,
                "viewer_write": write_status,
                "anonymous": anonymous_status,
            },
        )
        self.evidence.record(
            "grafana_viewer_permissions", "passed", artifacts=[artifact]
        )

    def query_permissions(self):
        writer = LokiClient(
            self.binding.query_url,
            self.binding.ca,
            self.binding.writer_token.read_text().strip(),
        )
        anonymous = LokiClient(self.binding.query_url, self.binding.ca)
        try:
            statuses = {
                "writer_read": writer.request("/loki/api/v1/labels")[0],
                "query_write": self.client.request("/loki/api/v1/push", "POST", b"{}")[
                    0
                ],
                "anonymous": anonymous.request("/loki/api/v1/labels")[0],
            }
            require(set(statuses.values()) == {401}, "query_permission_failure")
            artifact = self.evidence.artifact("query-permissions.json", statuses)
            self.evidence.record("query_permissions", "passed", artifacts=[artifact])
        finally:
            writer.close()
            anonymous.close()

    def recovery(self):
        self.current_case = "numbered_collection"
        self.batch()
        self.reconcile("numbered_collection")
        self.current_case = "storage_outage_disk_buffer"
        self.lifecycle.stop(["obs-loki"])
        self.batch()
        buffered = self.wait(
            lambda: (
                self.metrics_value('sum(vector_buffer_size_bytes{component_id="loki"})')
                > 0
            )
        )
        # Capture disk artifacts while storage is unavailable, not only a later query hit.
        files = [
            {
                "name": p.relative_to(self.root / "data/vector").as_posix(),
                "size": p.stat().st_size,
            }
            for p in (self.root / "data/vector").rglob("*")
            if p.is_file()
        ]
        require(
            any(f["size"] > 0 and "buffer" in f["name"] for f in files),
            "disk_buffer_not_observed",
        )
        artifact = self.evidence.artifact(
            "outage-buffer.json",
            {"buffer_metric_positive": buffered, "disk_files": files},
        )
        self.evidence.record(
            "storage_outage_disk_buffer", "passed", artifacts=[artifact]
        )
        self.current_case = "reconnect_replay"
        self.lifecycle.recreate_vector()
        self.lifecycle.start("obs-loki")
        self.reconcile("reconnect_replay")
        self.current_case = "collector_recreate_rotation"
        self.lifecycle.stop(["obs-vector"])
        for directory in self.binding.logs.values():
            require(not (directory / "depi.jsonl.1").exists(), "rotation_target_exists")
            (directory / "depi.jsonl").rename(directory / "depi.jsonl.1")
        self.batch()
        self.lifecycle.recreate_vector()
        self.reconcile("collector_recreate_rotation")

    def canary(self):
        before = self.metrics_value(
            "sum(vector_component_discarded_events_total) or vector(0)"
        )
        poison = event(1)
        poison["secret"] = "DEP_I_SYNTHETIC_SECRET_CANARY"
        target = self.binding.logs["platform"] / "depi-canary.jsonl"
        with target.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(poison) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        after = self.wait(
            lambda: (
                value
                if (
                    value := self.metrics_value(
                        "sum(vector_component_discarded_events_total) or vector(0)"
                    )
                )
                > before
                else None
            )
        )
        rows = self.client.range(
            '{stack="tianshu"} |= "DEP_I_SYNTHETIC_SECRET_CANARY"',
            self.start,
            time.time_ns(),
        )
        require(not rows, "sensitive_canary_leaked")
        artifact = self.evidence.artifact(
            "sensitive-canary.json",
            {
                "discard_before": before,
                "discard_after": after,
                "central_matches": len(rows),
                "source_preserved": target.exists(),
            },
        )
        self.evidence.record("sensitive_canary", "passed", artifacts=[artifact])

    def alert_receiver(self):
        import secrets

        token = secrets.token_urlsafe(32)
        token_file = self.root / "data/guard/depi-receiver-token"
        with token_file.open("x", encoding="utf-8") as stream:
            stream.write(token)
        os.chmod(token_file, 0o640)
        os.chown(token_file, 10001, 10001)
        script = Path(__file__).with_name("linux_receiver.py").read_text()
        self.dc("exec", "-d", "obs-guard", "python", "-B", "-c", script)
        status, _ = self.api(
            "/api/v1/provisioning/contact-points",
            "POST",
            {
                "uid": "depi-local",
                "name": "DEP-I local recording only",
                "type": "webhook",
                "settings": {
                    "url": "http://obs-guard:18081/record",
                    "httpMethod": "POST",
                    "authorization_scheme": "Bearer",
                    "authorization_credentials": token,
                },
                "disableResolveMessage": False,
            },
        )
        require(status in (200, 202), "local_contact_point_failed")
        status, _ = self.api(
            "/api/v1/provisioning/policies",
            "PUT",
            {
                "receiver": "DEP-I local recording only",
                "group_by": ["alertname"],
                "group_wait": "1s",
                "group_interval": "10s",
                "repeat_interval": "1h",
            },
        )
        require(status in (200, 202), "local_notification_policy_failed")

    def verify_alert(self):
        path = self.root / "data/guard/depi-alerts.jsonl"

        def recorded():
            if not path.exists():
                return None
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            return (
                rows
                if any(
                    a["name"] in {"InvalidOrChangedSource", "CollectorDiscardOrError"}
                    and a["status"] == "firing"
                    for row in rows
                    for a in row["alerts"]
                )
                else None
            )

        rows = self.wait(recorded, seconds=180)
        artifact = self.evidence.artifact(
            "local-alert-delivery.json",
            {
                "receiver": "internal_guard_recorder",
                "messages": rows,
                "external_delivery": False,
            },
        )
        self.evidence.record("local_alert_delivery", "passed", artifacts=[artifact])

    def capacity_probe(self):
        script = Path(__file__).with_name("linux_capacity_probe.py").read_text()
        result = json.loads(
            self.dc("exec", "-T", "obs-guard", "python", "-B", "-c", script)
        )
        require(
            result.get("actual_five_volume_enospc") is False
            and result.get("enospc") is True,
            "bounded_probe_invalid",
        )
        artifact = self.evidence.artifact("bounded-tmpfs-capacity.json", result)
        self.evidence.record(
            "bounded_tmpfs_capacity_probe", "passed", artifacts=[artifact]
        )

    def run(self):
        self.current_case = "grafana_datasources_dashboard"
        self.ui_and_metrics()
        self.current_case = "local_alert_delivery"
        self.alert_receiver()
        self.current_case = "query_permissions"
        self.query_permissions()
        self.recovery()
        self.current_case = "sensitive_canary"
        self.canary()
        self.current_case = "local_alert_delivery"
        self.verify_alert()
