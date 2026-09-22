"""Current public web/diagnostics ports plus an explicit isolated control adapter."""

import os
import secrets
import time
import uuid

from .diagnostics import causal
from .evidence import digest, read_json
from .inputs import ROLES, require
from .health import LOG_KEY, validate_ready
from .transport import (
    Adapter,
    Client,
    Failed,
    Missing,
    check,
    deadline_after,
    deadline_scope,
)

CASES = (
    "runtime_binding",
    "configuration_loading",
    "health_and_auth",
    "web_login_csrf",
    "dialogue_model_reply",
    "memory_candidate",
    "memory_finalized",
    "chat_archive",
    "memory_backlog_boundary",
    "unknown_no_resend",
    "restart_recovery",
    "source_revocation",
    "model_revocation",
    "timeout_cancel",
    "log_causality",
    "failure_truthfulness",
    "abnormal_readiness",
    "browser_rendering",
    "real_model_quality",
    "observation_24h",
)


def env_value(name):
    value = os.environ.get(name or "")
    if not value:
        raise Missing("credential_environment_missing")
    return value


class Suite:
    def __init__(self, config, binding, report):
        self.config, self.binding, self.report = config, binding, report
        self.synthetic = config["runtime_kind"] == "synthetic"
        self.clients = {}
        self.adapter = Adapter(config.get("adapter"), self.synthetic)
        self.success = {}
        self.chat = None
        self.failed_chat = None
        self.stop_mutations = False

    def client(self, role):
        if role not in self.config.get("endpoints", {}):
            raise Missing("service_endpoint_missing")
        if role not in self.clients:
            self.clients[role] = Client(self.config["endpoints"][role], self.synthetic)
        return self.clients[role]

    @property
    def web(self):
        return self.client("platform")

    def need(self, name):
        if not self.success.get(name):
            raise Missing("prerequisite_" + name)

    def state(self):
        result = self.adapter.call("state", run_id=self.report.data["run_id"])
        check(result.get("scope") == "synthetic_isolated", "adapter_scope_mismatch")
        for key in (
            "model_calls",
            "send_calls",
            "candidate_count",
            "pending_candidates",
        ):
            check(
                type(result.get(key)) is int and result[key] >= 0,
                "adapter_counter_invalid",
            )
        return result

    def login(self):
        status, session = self.web.request("GET", "/api/web/session")
        check(
            status == 200 and isinstance(session.get("csrf"), str),
            "session_unavailable",
        )
        self.web.csrf = session["csrf"]
        credentials = self.config.get("web", {})
        status, result = self.web.post(
            "/api/web/login",
            {
                "username": env_value(credentials.get("username_env")),
                "password": env_value(credentials.get("password_env")),
            },
        )
        check(status == 200 and result.get("authenticated") is True, "login_failed")
        check(
            isinstance(result.get("csrf"), str) and result["csrf"] != self.web.csrf,
            "csrf_not_rotated",
        )
        self.web.csrf = result["csrf"]

    def selection(self):
        value = self.config.get("web", {})
        if not value.get("conversation") or not value.get("actor"):
            raise Missing("synthetic_selection_missing")
        return {"conversation": value["conversation"], "actor": value["actor"]}

    def submit(self, client_id=None):
        body = {
            **self.selection(),
            "text": "DEP-D synthetic acceptance " + secrets.token_hex(8),
            "client_id": client_id or str(uuid.uuid4()),
        }
        correlation = secrets.token_hex(16)
        status, result = self.web.post("/api/web/messages", body, correlation)
        return {
            "body": body,
            "correlation": correlation,
            "status": status,
            "result": result,
        }

    def snapshot(self):
        status, result = self.web.post(
            "/api/web/snapshot", {**self.selection(), "before": None}
        )
        check(status == 200, "snapshot_failed")
        return result.get("snapshot") or {}

    def wait_turn(self, message_id, terminal=True):
        deadline = deadline_after(self.config.get("case_timeout_seconds", 20))
        while time.monotonic() < deadline:
            with deadline_scope(deadline):
                snapshot = self.snapshot()
            for item in snapshot.get("history", []) + snapshot.get("active_turns", []):
                if not any(
                    m.get("message_id") == message_id for m in item.get("messages", [])
                ):
                    continue
                if not terminal or item["turn"]["phase"] in {
                    "sent",
                    "failed",
                    "cancelled",
                    "observed",
                    "closed_unknown",
                }:
                    return item
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        raise Failed("turn_deadline_exceeded")

    def runtime_binding(self):
        identity = self.adapter.call(
            "identity", release_sha256=self.binding["manifest_sha256"]
        )
        check(identity.get("scope") == "synthetic_isolated", "adapter_scope_mismatch")
        check(
            identity.get("runtime_kind") == self.config["runtime_kind"],
            "runtime_kind_mismatch",
        )
        check(
            identity.get("release_sha256") == self.binding["manifest_sha256"],
            "running_release_mismatch",
        )
        for role in ROLES:
            current = identity.get("products", {}).get(role, {})
            check(
                current.get("commit") == self.binding["products"][role]["commit"],
                "running_commit_mismatch",
            )
            if self.config["mode"] == "container":
                pin = self.binding["products"][role]["digest"]
                check(
                    pin is not None and current.get("digest") == pin,
                    "running_image_unverified",
                )
        if self.config["mode"] == "container":
            check(identity.get("container_os") == "linux", "linux_runtime_unverified")
            # Candidate images can be tested in order to produce acceptance evidence;
            # requiring 'verified' here would create a circular release gate. The
            # report retains candidate status and never promotes the release.
        if not self.synthetic:
            enabled = {f["id"]: f["enabled"] for f in self.binding.get("features", [])}
            for option, feature in (
                ("automatic_memory", "automatic_long_term_memory"),
                ("chat_archive", "chat_audit_archive"),
            ):
                check(
                    enabled.get(feature)
                    is self.config.get("features", {}).get(option, False),
                    "feature_binding_mismatch",
                )
        return {
            "basis": "test_adapter_attestation",
            "adapter_sha256": self.adapter.fingerprint(),
            "static_manifest_sha256": self.binding["manifest_sha256"],
        }

    def health_and_auth(self):
        checks = {}
        for role in ROLES:
            client = self.client(role)
            status, result = client.request("GET", "/health/live")
            check(status == 200 and result == {"status": "alive"}, "liveness_invalid")
            status, _ = client.request("GET", "/health/ready")
            check(status == 401, "ready_anonymous_not_denied")
            status, _ = client.request(
                "GET",
                "/health/ready",
                headers={"Authorization": "Bearer DEP-D-invalid"},
            )
            check(status == 401, "ready_wrong_token_not_denied")
            token = env_value(
                self.config["endpoints"][role].get("diagnostics_token_env")
            )
            status, result = client.request(
                "GET", "/health/ready", headers={"Authorization": "Bearer " + token}
            )
            checks[role] = validate_ready(role, status, result)
        return checks

    def configuration_loading(self):
        self.need("runtime_binding")
        expected = self.config.get("expected_config_sha256", {})
        if set(expected) != set(ROLES):
            raise Missing("effective_configuration_inputs_missing")
        import re

        check(
            all(
                isinstance(v, str) and re.fullmatch("[a-f0-9]{64}", v)
                for v in expected.values()
            ),
            "invalid_configuration_hash",
        )
        receipt = self.adapter.call("configuration")
        if receipt.get("basis") != "runtime_loaded":
            raise Missing("effective_configuration_observation_missing")
        check(
            receipt.get("loaded_config_sha256") == expected,
            "loaded_configuration_mismatch",
        )
        return {
            "configuration_sha256": expected,
            "basis": "test_adapter_runtime_observation",
            "independent_process_verification": "required_by_coordinator",
        }

    def web_login_csrf(self):
        credentials = self.config.get("web", {})
        body = {
            "username": env_value(credentials.get("username_env")),
            "password": env_value(credentials.get("password_env")),
        }
        status, session = self.web.request("GET", "/api/web/session")
        check(
            status == 200 and session.get("authenticated") is False,
            "fresh_session_not_anonymous",
        )
        self.web.csrf = session["csrf"]
        status, _ = self.web.post("/api/web/login", body, csrf=False)
        check(status == 403, "missing_csrf_accepted")
        status, _ = self.web.post(
            "/api/web/login", body, origin="https://invalid.example"
        )
        check(status == 403, "foreign_origin_accepted")
        status, _ = self.web.post(
            "/api/web/login", {**body, "password": "DEP-D-wrong-password"}
        )
        check(status == 401, "wrong_password_accepted")
        self.login()
        cookies = list(self.web.jar)
        check(
            any(
                c.name == "tianshu_session"
                and c.has_nonstandard_attr("HttpOnly")
                and c.get_nonstandard_attr("SameSite") == "Strict"
                and (self.synthetic or c.secure)
                for c in cookies
            ),
            "session_cookie_flags",
        )
        status, result = self.web.request("GET", "/api/web/session")
        check(
            status == 200 and result.get("authenticated") is True,
            "session_not_authenticated",
        )
        return {
            "http_cookie_session": True,
            "missing_csrf": 403,
            "foreign_origin": 403,
            "incorrect_password": 401,
            "rendered_browser": False,
        }

    def dialogue_model_reply(self):
        if not self.synthetic and not next(
            (
                f["enabled"]
                for f in self.binding.get("features", [])
                if f["id"] == "web_text_dialogue"
            ),
            False,
        ):
            raise Missing("web_dialogue_not_enabled")
        self.need("web_login_csrf")
        self.need("runtime_binding")
        self.need("configuration_loading")
        before = self.state()
        request = self.submit()
        check(
            request["status"] == 200 and request["result"].get("state") == "accepted",
            "dialogue_not_accepted",
        )
        turn = self.wait_turn(request["result"]["message_id"])
        check(turn["turn"]["phase"] == "sent", "reply_not_sent")
        replies = turn["replies"]
        check(
            replies
            and all(
                r["state"] == "sent"
                and r["content_state"] == "available"
                and isinstance(r.get("text"), str)
                and r["text"]
                for r in replies
            ),
            "reply_not_available",
        )
        after = self.state()
        check(
            after["model_calls"] > before["model_calls"]
            and after["send_calls"] > before["send_calls"],
            "model_or_sender_not_observed",
        )
        self.chat = {**request, "turn": turn, "before": before, "after": after}
        return {
            "reply_count": len(replies),
            "model_calls_delta": after["model_calls"] - before["model_calls"],
            "model_kind": self.config["model_kind"],
            "counter_basis": "adapter_attested",
        }

    def memory_candidate(self):
        if not self.synthetic and not self.config.get("features", {}).get(
            "automatic_memory"
        ):
            return self.disabled_capabilities()
        self.need("dialogue_model_reply")
        result = self.adapter.call(
            "memory_candidate", turn_id=self.chat["turn"]["turn"]["turn_id"]
        )
        check(
            result.get("turn_id") == self.chat["turn"]["turn"]["turn_id"]
            and result.get("state") in {"accepted", "queued", "consumed"}
            and result.get("candidate_id"),
            "memory_candidate_not_accepted",
        )
        self.candidate_id = result["candidate_id"]
        return {
            "stage": "candidate_accepted",
            "final_memory_proven": False,
            "basis": "adapter_attested",
        }

    def memory_finalized(self):
        self.need("memory_candidate")
        if not self.synthetic and not self.config.get("features", {}).get(
            "automatic_memory"
        ):
            return self.disabled_capabilities()
        if not self.config.get("features", {}).get("automatic_memory"):
            raise Missing("automatic_memory_not_enabled")
        result = self.adapter.call("memory_finalized", candidate_id=self.candidate_id)
        check(
            result.get("candidate_id") == self.candidate_id
            and result.get("state") == "committed"
            and result.get("memory_id")
            and type(result.get("revision")) is int
            and result["revision"] > 0
            and result.get("readback_id") == result["memory_id"],
            "candidate_is_not_final_memory",
        )
        return {"stage": "committed_and_read_back", "basis": "adapter_attested"}

    def chat_archive(self):
        if not self.synthetic and not self.config.get("features", {}).get(
            "chat_archive"
        ):
            return self.disabled_capabilities()
        self.need("dialogue_model_reply")
        if not self.config.get("features", {}).get("chat_archive"):
            raise Missing("archive_not_enabled")
        result = self.adapter.call(
            "archive", turn_id=self.chat["turn"]["turn"]["turn_id"]
        )
        check(
            result.get("state") == "archived"
            and result.get("archive_id")
            and result.get("turn_id") == self.chat["turn"]["turn"]["turn_id"]
            and result.get("readback_id") == result["archive_id"],
            "archive_receipt_missing",
        )
        return {"stage": "archived_and_read_back", "basis": "adapter_attested"}

    def disabled_capabilities(self):
        from .capabilities import disabled_facts

        self.need("configuration_loading")
        token = env_value(
            self.config["endpoints"]["companion"]["diagnostics_token_env"]
        )
        return disabled_facts(self.client("companion"), token)

    def memory_backlog_boundary(self):
        self.need("dialogue_model_reply")
        before, after = self.chat["before"], self.state()
        if not self.config.get("features", {}).get("automatic_memory"):
            check(
                after["pending_candidates"] <= before["pending_candidates"],
                "unconsumed_memory_queue_growing",
            )
        else:
            self.need("memory_finalized")
            check(
                after["pending_candidates"] <= before["pending_candidates"],
                "memory_consumer_not_draining",
            )
        return {
            "pending_before": before["pending_candidates"],
            "pending_after": after["pending_candidates"],
        }

    def with_fault(self, name, action):
        restore = True
        finish = deadline_after(self.config.get("case_timeout_seconds", 20))
        # Reserve cleanup *inside* the same case budget. An expired action must
        # not replenish 30 seconds per adapter call, nor starve fault restoration.
        reserve = min(2, max(0, finish - time.monotonic()) / 4)
        with deadline_scope(finish):
            try:
                with deadline_scope(finish - reserve):
                    try:
                        self.adapter.call("fault", name=name, enabled=True)
                    except Missing:
                        # Unsupported means no mutation; ambiguous timeout does not.
                        restore = False
                        raise
                    return action()
            finally:
                if restore:
                    try:
                        with deadline_scope(deadline_after(2)):
                            response = self.adapter.call(
                                "fault", name=name, enabled=False
                            )
                        check(
                            response.get("restored") is True,
                            "fault_restore_not_confirmed",
                        )
                    except BaseException:
                        self.stop_mutations = True
                        raise Failed("fault_restore_failed") from None

    def unknown_no_resend(self):
        self.need("dialogue_model_reply")

        def exercise():
            before = self.state()
            request = self.submit()
            check(
                request["status"] == 200 and request["result"].get("message_id"),
                "unknown_input_not_accepted",
            )
            turn = self.wait_turn(request["result"]["message_id"])
            check(turn["turn"]["phase"] == "closed_unknown", "unknown_not_preserved")
            check(
                all(
                    r.get("text") is None and r.get("content_state") == "unavailable"
                    for r in turn.get("replies", [])
                ),
                "unknown_content_exposed",
            )
            state = self.state()
            check(
                state["model_calls"] == before["model_calls"] + 1,
                "unknown_first_attempt_count",
            )
            for _ in range(2):
                status, result = self.web.post(
                    "/api/web/messages", request["body"], request["correlation"]
                )
                check(
                    status == 200
                    and result.get("message_id") == request["result"]["message_id"],
                    "replay_identity_changed",
                )
                self.wait_turn(request["result"]["message_id"])
            after = self.state()
            check(
                after["model_calls"] == state["model_calls"]
                and after["send_calls"] == state["send_calls"],
                "unknown_resent",
            )
            self.failed_chat = request
            self.unknown_state = state
            return {"replays": 2, "model_calls_delta": 1, "resend_count": 0}

        return self.with_fault("model_disconnect_after_accept", exercise)

    def restart_recovery(self):
        self.need("unknown_no_resend")
        before = self.state()
        response = self.adapter.call("restart", services=list(ROLES))
        check(set(response.get("restarted", [])) == set(ROLES), "restart_incomplete")
        for role in ROLES:
            check(
                response.get("before_instances", {}).get(role)
                and response.get("after_instances", {}).get(role)
                and response["before_instances"][role]
                != response["after_instances"][role],
                "restart_instance_unchanged",
            )
        status, result = self.web.request("GET", "/api/web/session")
        check(
            status in (200, 401) and result.get("authenticated") is not True,
            "session_survived_restart",
        )
        self.login()
        turn = self.wait_turn(self.chat["result"]["message_id"])
        check(
            turn["replies"] == self.chat["turn"]["replies"],
            "history_lost_after_restart",
        )
        status, result = self.web.post("/api/web/messages", self.failed_chat["body"])
        check(
            status == 200
            and result.get("message_id") == self.failed_chat["result"]["message_id"],
            "unknown_receipt_lost",
        )
        turn = self.wait_turn(result["message_id"])
        check(turn["turn"]["phase"] == "closed_unknown", "unknown_lost_after_restart")
        after = self.state()
        check(
            after["model_calls"] == before["model_calls"]
            and after["send_calls"] == before["send_calls"],
            "restart_resent",
        )
        return {
            "services_restarted": list(ROLES),
            "old_session_expired": True,
            "unknown_resent": False,
            "process_identity_basis": "adapter_attested",
        }

    def source_revocation(self):
        self.need("dialogue_model_reply")
        before = self.state()
        response = self.adapter.call(
            "revoke_source", message_id=self.chat["result"]["message_id"]
        )
        check(response.get("revoked") is True, "source_revoke_unconfirmed")
        turn = self.wait_turn(self.chat["result"]["message_id"])
        check(
            all(
                r.get("text") is None and r.get("content_state") == "unavailable"
                for r in turn["replies"]
            ),
            "revoked_source_still_visible",
        )
        result = self.adapter.call(
            "recall_revoked_source", message_id=self.chat["result"]["message_id"]
        )
        check(
            result.get("excluded") is True and result.get("source_revision", 0) > 0,
            "revoked_source_recalled",
        )
        after = self.state()
        check(
            after["model_calls"] == before["model_calls"], "source_check_called_model"
        )
        return {"history_content_unavailable": True, "recall_basis": "adapter_attested"}

    def model_revocation(self):
        self.need("dialogue_model_reply")

        def exercise():
            before = self.state()
            request = self.submit()
            if (
                request["status"] == 200
                and request["result"].get("state") == "accepted"
            ):
                turn = self.wait_turn(request["result"]["message_id"])
                check(
                    turn["turn"]["phase"] in {"failed", "cancelled"},
                    "revoked_model_succeeded",
                )
            else:
                check(
                    request["status"] in {403, 410, 503}
                    or request["result"].get("state") == "not_started",
                    "model_revoke_not_rejected",
                )
            after = self.state()
            check(
                after["model_calls"] == before["model_calls"]
                and after["send_calls"] == before["send_calls"],
                "revoked_model_called",
            )
            return {"upstream_calls_delta": 0}

        return self.with_fault("model_revoked", exercise)

    def timeout_cancel(self):
        self.need("dialogue_model_reply")

        def exercise():
            before = self.state()
            timed = self.submit()
            check(timed["status"] == 200, "timeout_input_not_accepted")
            timed_turn = self.wait_turn(timed["result"]["message_id"])
            check(
                timed_turn["turn"]["phase"] in {"failed", "closed_unknown"},
                "timeout_fake_success",
            )
            check(
                all(r.get("text") is None for r in timed_turn.get("replies", [])),
                "timeout_content_exposed",
            )
            after_timeout = self.state()
            check(
                after_timeout["model_calls"] == before["model_calls"] + 1
                and after_timeout["send_calls"] == before["send_calls"],
                "timeout_retried_or_sent",
            )
            request = self.submit()
            check(request["status"] == 200, "cancel_input_not_accepted")
            turn = self.wait_turn(request["result"]["message_id"], terminal=False)
            status, result = self.web.post(
                "/api/web/cancel",
                {
                    **self.selection(),
                    "turn_id": turn["turn"]["turn_id"],
                    "expected_version": turn["turn"]["version"],
                },
            )
            check(status == 200 and isinstance(result, dict), "cancel_not_acknowledged")
            terminal = self.wait_turn(request["result"]["message_id"])
            check(
                terminal["turn"]["phase"] in {"cancelled", "closed_unknown", "failed"},
                "cancel_fake_success",
            )
            check(
                all(r.get("text") is None for r in terminal.get("replies", [])),
                "cancel_late_content",
            )
            fact = self.adapter.call(
                "cancel_observation",
                turn_id=turn["turn"]["turn_id"],
                timeout_turn_id=timed_turn["turn"]["turn_id"],
            )
            check(
                fact.get("late_reply_deliveries") == 0
                and fact.get("duplicate_model_calls") == 0
                and fact.get("timeout_observed") is True,
                "timeout_cancel_not_observed",
            )
            return {
                "terminal_phase": terminal["turn"]["phase"],
                "timeout_phase": timed_turn["turn"]["phase"],
                "late_reply_deliveries": 0,
            }

        return self.with_fault("model_timeout", exercise)

    def log_check(self, chat, name):
        specification = self.config.get("logs", {})
        if not specification.get(name) or not specification.get("catalog_file"):
            raise Missing("diagnostic_expectations_missing")
        catalog = read_json(specification["catalog_file"])
        if not self.synthetic:
            check(
                catalog.get("catalog_version") == "dep-d/1"
                and catalog.get("products") == self.binding["products"],
                "catalog_not_bound_to_products",
            )
            if not specification.get("catalog_sha256"):
                raise Missing("catalog_hash_missing")
            from pathlib import Path

            check(
                digest(Path(specification["catalog_file"]).read_bytes())
                == specification["catalog_sha256"],
                "catalog_hash_mismatch",
            )
            catalog = catalog["services"]
        result = self.adapter.call("logs", correlation_id=chat["correlation"])
        for canary in self.config.get("canary_env_names", []):
            secret = env_value(canary)
            check(
                not any(secret in line for line in result.get("lines", [])),
                "secret_canary_in_logs",
            )
        expectation = specification[name]
        return causal(
            result.get("lines", []),
            catalog,
            chat["correlation"],
            expectation["required"],
            expectation.get("forbidden", []),
        )

    def log_causality(self):
        self.need("dialogue_model_reply")
        required = self.config.get("logs", {}).get("success", {}).get("required", [])
        check({x[0] for x in required} == set(ROLES), "four_service_causality_required")
        return self.log_check(self.chat, "success")

    def failure_truthfulness(self):
        self.need("unknown_no_resend")
        specification = self.config.get("logs", {}).get("failure", {})
        if not specification.get("forbidden"):
            raise Missing("failed_operation_success_exclusions_missing")
        return self.log_check(self.failed_chat, "failure")

    def abnormal_readiness(self):
        self.need("runtime_binding")
        self.need("web_login_csrf")
        facts = {}
        for role in ROLES:

            def exercise():
                before = self.state()
                token = env_value(
                    self.config["endpoints"][role].get("diagnostics_token_env")
                )
                status, result = self.client(role).request(
                    "GET", "/health/ready", headers={"Authorization": "Bearer " + token}
                )
                check(
                    status == 503
                    and result.get("status") == "not_ready"
                    and result.get("checks", {}).get(LOG_KEY[role]) == "failed",
                    "fault_ready_false_success",
                )
                request = self.submit()
                if (
                    request["status"] == 200
                    and request["result"].get("state") == "accepted"
                ):
                    turn = self.wait_turn(request["result"]["message_id"])
                    check(
                        turn["turn"]["phase"]
                        in {"failed", "cancelled", "closed_unknown"},
                        "log_failure_business_success",
                    )
                else:
                    check(
                        request["status"] in {403, 503}
                        or request["result"].get("state") in {"unknown", "not_started"},
                        "log_failure_not_rejected",
                    )
                after = self.state()
                check(
                    after["model_calls"] == before["model_calls"]
                    and after["send_calls"] == before["send_calls"],
                    "log_failure_allowed_side_effect",
                )
                return {"ready_status": status, "side_effect_delta": 0}

            facts[role] = self.with_fault("logs_unavailable:" + role, exercise)
        return facts

    def execute(self, plan=False):
        for case in CASES:
            started = time.monotonic()
            if plan:
                self.report.add(case, "not_run", "plan_only")
                continue
            if case in {"browser_rendering", "real_model_quality", "observation_24h"}:
                self.report.add(
                    case,
                    "not_run",
                    {
                        "browser_rendering": "http_suite_does_not_render_ui",
                        "real_model_quality": "recorded_model_only",
                        "observation_24h": "use_observe_command",
                    }[case],
                )
                continue
            if self.stop_mutations:
                self.report.add(case, "not_run", "fault_cleanup_failed")
                continue
            if case in self.config.get("skip", {}):
                # Do not turn an operator skip into a release pass.
                self.report.add(
                    case,
                    "skipped",
                    "operator_skip",
                    {"reason_sha256": digest(str(self.config["skip"][case]).encode())},
                )
                continue
            try:
                with deadline_scope(
                    started + self.config.get("case_timeout_seconds", 20)
                ):
                    facts = getattr(self, case)()
                self.success[case] = True
                self.report.add(
                    case,
                    "pass",
                    "assertions_satisfied",
                    facts,
                    time.monotonic() - started,
                )
            except Missing as exc:
                self.report.add(
                    case,
                    "dependency_missing",
                    str(exc),
                    elapsed=time.monotonic() - started,
                )
            except Failed as exc:
                self.report.add(
                    case, "fail", str(exc), elapsed=time.monotonic() - started
                )
            except KeyboardInterrupt:
                self.report.add(
                    case, "not_run", "interrupted", elapsed=time.monotonic() - started
                )
                self.stop_mutations = True
            except Exception:
                # No exception message/traceback: it may contain credentials or bodies.
                self.report.add(
                    case,
                    "fail",
                    "invalid_or_unexpected_evidence",
                    elapsed=time.monotonic() - started,
                )
        return self.report


def validate_config(config):
    require(config.get("input_version") == "dep-d/1", "input_version_invalid")
    require(config.get("mode") in {"local", "container"}, "mode_invalid")
    require(
        config.get("runtime_kind") in {"synthetic", "product"}, "runtime_kind_invalid"
    )
    require(config.get("scope") == "synthetic_isolated", "synthetic_scope_required")
    require(config.get("model_kind") == "recorded", "real_model_not_authorized")
    require(
        config.get("mode") != "container" or config.get("runtime_kind") == "product",
        "container_cannot_be_synthetic",
    )
    duration = config.get("case_timeout_seconds", 20)
    require(
        type(duration) in (int, float) and 1 <= duration <= 60, "case_timeout_invalid"
    )
    require(set(config.get("skip", {})) <= set(CASES), "unknown_skip_case")
    return config
