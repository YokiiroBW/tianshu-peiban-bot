"""Controls confined to a freshly created synthetic stack and owned process handles."""

import json
import os
import signal
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

from acceptance.health import validate_ready
from acceptance.transport import Missing, check
from product_inputs import ROLES, write
from product_sender import SenderProxy


class FaultControl:
    def __init__(self, stack):
        self.stack = stack
        self.observations = []
        self.locks = []
        self.sender = SenderProxy(
            stack.root,
            stack.urls["platform"],
            stack.envs["companion"]["TS_CORE_PLATFORM_SENDER"],
        )
        stack.configs["companion"]["services"]["platform_sender"]["url"] = (
            self.sender.url
        )
        stack.configs["companion"]["policy"]["delivery_reconcile_timeout_ms"] = 1000
        stack.configs["companion"]["short_context"]["max_turns"] = 4
        # Explicit synthetic budget; no change to the published authority document.
        stack.configs["gateway"]["max_timeout_ms"] = 2000
        for role in ("companion", "gateway"):
            write(stack.root / "config" / (role + ".json"), stack.configs[role])

    def counts(self):
        return {"model_calls": self.stack.model.calls, "send_calls": self.sender.calls}

    def ready(self, role):
        return self.stack.client(role).request(
            "GET",
            "/health/ready",
            headers={
                "Authorization": "Bearer "
                + self.stack.envs[role]["TIANSHU_DIAGNOSTICS_TOKEN"]
            },
        )

    def restart(self, roles=ROLES):
        before = {r: self.stack.children[r].pid for r in roles}
        stops = {}
        for role in reversed(roles):
            child = self.stack.children[role]
            check(child.poll() is None, "restart_requires_live_owned_process")
            child.send_signal(
                signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM
            )
            try:
                child.wait(timeout=5)
                method = "signal_exit"
            except subprocess.TimeoutExpired:
                # Deliberate owned-process crash recovery, never a normal-stop proof.
                child.kill()
                child.wait(timeout=5)
                method = "forced_owned_process_crash"
            stops[role] = {
                "method": method,
                "exit_code": child.returncode,
                "normal_stop_proven": False,
            }
        for role in roles:
            self.stack.start_role(role)
        for role in roles:
            validate_ready(role, *self.ready(role))
        # Re-read the passive parser observations against the new PID, not stale launch args.
        configuration = self.stack.call("configuration")
        result = {
            "restarted": list(roles),
            "before_instances": before,
            "after_instances": {r: self.stack.children[r].pid for r in roles},
            "stops": stops,
            "normal_stop_proven": False,
            "loaded_config_sha256": configuration["loaded_config_sha256"],
        }
        self.observations.append({"operation": "restart", **result})
        return result

    def local(self, action, data):
        check(
            action in {"register-input", "dispatch-fanout", "revoke-config"},
            "unsupported_fault_cli",
        )
        request = self.stack.root / ("fault-" + uuid.uuid4().hex + ".json")
        write(request, data)
        request.chmod(0o600)
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "services.platform",
                    "--settings",
                    str(self.stack.root / "config/platform.json"),
                    "local",
                    "--credential-env",
                    "TS_ADMIN_TOKEN",
                    action,
                    "--input",
                    str(request),
                ],
                env=self.stack.envs["platform"],
                capture_output=True,
                timeout=20,
            )
            check(
                result.returncode == 0,
                "source_public_cli_failed_" + action.replace("-", "_"),
            )
            return json.loads(result.stdout)
        finally:
            request.unlink()

    def retract(self, message_id):
        entry = self.stack.configs["platform"]["input_entries"]["web-input"]
        now = datetime.now(timezone.utc)

        def stamp(value):
            return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")

        data = {
            "message_key": {
                "channel": entry["channel"],
                "message_id": message_id,
                "revision": 2,
            },
            "author": entry["account"],
            "sent_at": stamp(now),
            "kind": "retract",
            "parts": [],
            "reply_refs": [],
            "mentioned_accounts": [],
        }
        receipt = self.local("register-input", {"entry_id": "web-input", "input": data})
        result = self.local(
            "dispatch-fanout",
            {
                "schema_version": 1,
                "command": {
                    "schema_version": 1,
                    "request_id": "request:" + uuid.uuid4().hex,
                    "idempotency_key": "retract:" + uuid.uuid4().hex,
                    "origin": {"assertion_ref": receipt["assertion_ref"]},
                    "deadline_at": stamp(now + timedelta(seconds=30)),
                },
                "input": data,
                "target_actor_ids": [],
            },
        )
        return result

    def lock_logs(self, role):
        if os.name != "nt":
            raise Missing("posix_log_fault_not_implemented")
        import msvcrt

        check(not self.locks, "log_fault_already_active")
        try:
            for path in (self.stack.root / "logs" / role).glob("*.jsonl*"):
                handle = path.open("rb")
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 16777216)
                except BaseException:
                    handle.close()
                    raise
                self.locks.append(handle)
            check(bool(self.locks), "no_real_log_file_to_lock")
        except BaseException:
            self.unlock_logs()
            raise

    def unlock_logs(self):
        for handle in self.locks:
            handle.close()
        self.locks.clear()

    def close(self):
        self.unlock_logs()
        self.stack.model.release.set()
        self.sender.close()
