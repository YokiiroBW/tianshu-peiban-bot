"""Real public product operations plus explicit sender/model/OS fault ports."""

import json
import time
import uuid

from acceptance.health import LOG_KEY
from acceptance.suite import CASES, Suite
from acceptance.transport import Failed, check
from product_inputs import ROLES


class ProductFaultSuite(Suite):
    @property
    def controls(self):
        return self.adapter.faults

    def state(self):
        facts = super().state()
        facts.update(self.controls.counts())
        return facts

    def execute(self, plan=False, cases=None):
        # Irreversible revocation is last: never un-revoke or silently publish a replacement.
        order = [c for c in CASES if c != "model_revocation"] + ["model_revocation"]
        return super().execute(plan, cases=order if cases is None else cases)

    def unknown_no_resend(self):
        self.need("dialogue_model_reply")
        port = self.controls.sender
        before = self.state()
        accepted, dropped = port.accepted, port.dropped
        port.drop = True
        try:
            request = self.submit()
            check(
                request["status"] == 200 and request["result"].get("message_id"),
                "unknown_input_not_accepted",
            )
            turn = self.wait_turn(request["result"]["message_id"])
            check(turn["turn"]["phase"] == "closed_unknown", "unknown_not_preserved")
            check(
                bool(turn["replies"])
                and all(
                    r.get("text") is None and r.get("content_state") == "unavailable"
                    for r in turn["replies"]
                ),
                "unknown_content_exposed",
            )
            state = self.state()
            check(
                state["model_calls"] == before["model_calls"] + 1
                and state["send_calls"] == before["send_calls"] + 1
                and port.accepted == accepted + 1
                and port.dropped == dropped + 1,
                "real_delivery_response_loss_not_observed",
            )
            for _ in range(2):
                status, result = self.web.post(
                    "/api/web/messages", request["body"], request["correlation"]
                )
                check(
                    status == 200
                    and result.get("message_id") == request["result"]["message_id"],
                    "unknown_replay_identity_changed",
                )
                self.wait_turn(result["message_id"])
            check(self.state() == state, "unknown_resent")
            self.failed_chat = request
            self.unknown_state = state
            raw = self.adapter.call("logs", correlation_id=request["correlation"])
            events = [json.loads(line) for line in raw["lines"]]
            check(
                any(
                    e["service"] == "companion"
                    and e["event"] == "turn.delivery.finished"
                    and e["outcome"] == "unknown"
                    for e in events
                ),
                "unknown_event_missing",
            )
            return {
                "replays": 2,
                "upstream_send_accepted": 1,
                "responses_dropped": 1,
                "model_calls_delta": 1,
                "resend_count": 0,
                "injection": "real_sender_success_response_lost",
            }
        finally:
            port.drop = False

    def restart_recovery(self):
        self.need("unknown_no_resend")
        # Keep the existing public snapshot/replay assertions, replacing only lifecycle control.
        original = self.adapter.call

        def dispatch(operation, **kwargs):
            if operation == "restart":
                return self.controls.restart()
            return original(operation, **kwargs)

        self.adapter.call = dispatch
        try:
            facts = super().restart_recovery()
            return {
                **facts,
                "normal_stop_proven": False,
                "memory_cleanup": self.controls.observations[-1]["stops"]["memory"],
            }
        finally:
            self.adapter.call = original

    def source_revocation(self):
        self.need("dialogue_model_reply")
        # Positive control proves that the source actually participated in recall beforehand.
        text = self.chat["body"]["text"]
        request = self.submit()
        check(request["status"] == 200, "source_control_not_accepted")
        check(
            self.wait_turn(request["result"]["message_id"])["turn"]["phase"] == "sent",
            "source_control_not_sent",
        )
        check(
            text in json.dumps(self.adapter.model.requests[-1]),
            "source_recall_positive_control_missing",
        )
        before = self.controls.counts()
        self.controls.retract(self.chat["result"]["message_id"])
        turn = self.wait_turn(self.chat["result"]["message_id"])
        check(
            bool(turn["replies"])
            and all(
                r.get("text") is None and r.get("content_state") == "unavailable"
                for r in turn["replies"]
            ),
            "revoked_source_still_visible",
        )
        check(self.controls.counts() == before, "source_retraction_side_effect")
        request = self.submit()
        check(request["status"] == 200, "source_recall_not_accepted")
        check(
            self.wait_turn(request["result"]["message_id"])["turn"]["phase"] == "sent",
            "source_recall_not_sent",
        )
        check(
            text not in json.dumps(self.adapter.model.requests[-1]),
            "revoked_source_recalled",
        )
        return {
            "source_revision": 2,
            "history_content_unavailable": True,
            "recall_positive_control": True,
            "recall_excluded": True,
            "mutation": "public_operator_register_input_and_dispatch_fanout",
            "browser_retract_claimed": False,
        }

    def model_revocation(self):
        self.need("dialogue_model_reply")
        before = self.controls.counts()
        receipt = self.controls.local("revoke-config", {"id": 1})
        check(receipt.get("revoked") is True, "model_revoke_unconfirmed")
        request = self.submit()
        check(
            request["status"] == 503
            and request["result"].get("code") == "model_not_configured",
            "revoked_model_not_rejected",
        )
        status, _ = self.adapter.client("gateway").request(
            "POST",
            "/v1/chat/completions",
            {
                "messages": [{"role": "user", "content": "synthetic revoked request"}],
                "stream": False,
            },
            headers={
                "Authorization": "Bearer "
                + self.adapter.envs["companion"]["TS_CORE_GATEWAY"],
                "X-Request-ID": "model:" + uuid.uuid4().hex,
                "X-Tianshu-Config-Version": "1",
                "X-Tianshu-Workload": "companion.text",
                "X-Tianshu-Turn-ID": "turn:" + uuid.uuid4().hex,
            },
        )
        check(status in {403, 410, 503}, "revoked_gateway_not_rejected")
        check(self.controls.counts() == before, "revoked_model_called")
        return {
            "public_operator_cli_revoke": True,
            "gateway_status": status,
            "gateway_cache_basis": "cold_after_log_fault_restart",
            "warm_cache_revocation_latency_proven": False,
            "version": 1,
            "upstream_calls_delta": 0,
            "send_calls_delta": 0,
            "restored": False,
        }

    def timeout_cancel(self):
        self.need("dialogue_model_reply")
        model = self.adapter.model
        before = self.controls.counts()
        model.release.clear()
        model.delay = 8
        completed_before = model.completed
        try:
            timed = self.submit()
            check(timed["status"] == 200, "timeout_input_not_accepted")
            terminal = self.wait_turn(timed["result"]["message_id"])
            check(terminal["turn"]["phase"] == "failed", "timeout_fake_success")
            self.adapter.call("logs", correlation_id=timed["correlation"])
            events = self.adapter.logs("gateway")
            check(
                any(
                    e["correlation_id"] == timed["correlation"]
                    and e["event"] == "upstream.call_finished"
                    and e["error_code"] == "timeout"
                    for e in events
                ),
                "real_gateway_timeout_missing",
            )
            request = self.submit()
            check(request["status"] == 200, "cancel_input_not_accepted")
            deadline = time.monotonic() + 10
            while (
                model.calls < before["model_calls"] + 2 and time.monotonic() < deadline
            ):
                time.sleep(0.05)
            check(
                model.calls == before["model_calls"] + 2, "cancel_upstream_not_started"
            )
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
            stopped = self.wait_turn(request["result"]["message_id"])
            check(
                stopped["turn"]["phase"] in {"cancelled", "closed_unknown", "failed"},
                "cancel_fake_success",
            )
            # Release real late responses and wait beyond the provider's bounded delay.
            model.release.set()
            time.sleep(2.2)
            check(
                model.completed == completed_before + 2,
                "late_provider_responses_not_released",
            )
            for item in (timed, request):
                current = self.wait_turn(item["result"]["message_id"])
                check(
                    all(r.get("text") is None for r in current["replies"]),
                    "late_reply_visible",
                )
            after = self.controls.counts()
            check(
                after["model_calls"] == before["model_calls"] + 2
                and after["send_calls"] == before["send_calls"],
                "timeout_cancel_retry_or_delivery",
            )
            self.adapter.call("logs", correlation_id=request["correlation"])
            return {
                "timeout_phase": terminal["turn"]["phase"],
                "cancel_phase": stopped["turn"]["phase"],
                "upstream_calls": 2,
                "late_reply_deliveries": 0,
                "provider_budget_ms": 2000,
                "late_provider_responses_released": 2,
            }
        finally:
            model.delay = 0
            model.release.set()

    def failure_truthfulness(self):
        self.need("dialogue_model_reply")
        before = self.controls.counts()
        self.adapter.model.disconnect = True
        try:
            request = self.submit()
            check(request["status"] == 200, "failure_input_not_accepted")
            turn = self.wait_turn(request["result"]["message_id"])
            check(turn["turn"]["phase"] == "failed", "disconnect_not_explicit_failure")
            facts = self.log_check(request, "failure")
            after = self.controls.counts()
            check(
                after["model_calls"] == before["model_calls"] + 1
                and after["send_calls"] == before["send_calls"],
                "failed_request_retried_or_sent",
            )
            return {**facts, "phase": "failed", "unknown_claimed": False}
        finally:
            self.adapter.model.disconnect = False

    def abnormal_readiness(self):
        self.need("dialogue_model_reply")
        facts = {}
        for role in ROLES:
            before = self.controls.counts()
            self.controls.lock_logs(role)
            try:
                # A real request reaches product logging admission; readiness itself stays read-only.
                try:
                    self.adapter.client(role).request(
                        "POST", "/dep-h-invalid-route", {}
                    )
                except Failed:
                    pass
                deadline = time.monotonic() + 4
                while True:
                    status, body = self.controls.ready(role)
                    if status == 503 or time.monotonic() >= deadline:
                        break
                    time.sleep(0.1)
                check(
                    status == 503
                    and body.get("checks", {}).get(LOG_KEY[role]) == "failed",
                    "log_fault_readiness_not_failed_" + role,
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
                        "log_failure_business_succeeded",
                    )
                else:
                    check(
                        request["status"] in {403, 503}
                        or request["result"].get("state") in {"not_started", "unknown"},
                        "log_failure_not_rejected",
                    )
                check(self.controls.counts() == before, "log_failure_side_effect")
                facts[role] = {
                    "ready_status": status,
                    "side_effect_delta": 0,
                    "fault": "windows_os_exclusive_log_byte_lock",
                }
            finally:
                self.controls.unlock_logs()
                try:
                    self.controls.restart((role,))
                    if role == "platform":
                        self.login()
                except BaseException:
                    self.stop_mutations = True
                    raise
        return facts
