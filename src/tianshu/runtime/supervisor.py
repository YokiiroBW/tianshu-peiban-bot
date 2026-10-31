"""One container owns three core roles and its explicit observation/helper processes."""

import asyncio
import ctypes
import codecs
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import signal
import sys
from uuid import uuid4

from psycopg.conninfo import conninfo_to_dict


def secret_values(environ):
    values = []
    for name, value in environ.items():
        if re.search(r"KEY|TOKEN|SECRET|PASSWORD|DSN|DATABASE_URL", name, re.I) and len(value) >= 4:
            values.append(value)
            if re.search(r"DSN|DATABASE_URL", name, re.I):
                try:
                    password = conninfo_to_dict(value).get("password")
                    if password:
                        values.append(password)
                except Exception:
                    pass
    return sorted(values, key=len, reverse=True)


def redact(text, values):
    for value in values:
        text = text.replace(value, "[redacted]")
    text = re.sub(r"(?i)(Bearer\s+)[^\s\"']+", r"\1[redacted]", text)
    text = re.sub(r"(?i)(https?://)[^/@\s]+:[^/@\s]+@", r"\1[redacted]@", text)
    return text


def _initialize_directory(path, uid, gid):
    if path.is_symlink():
        raise ValueError("unsafe_product_data_directory")
    path.mkdir(parents=True, exist_ok=True)
    os.chown(path, uid, gid)
    # Existing product data already belongs to the business UID. PID 1 therefore
    # needs CAP_FOWNER for chmod, independently of CAP_CHOWN and DAC_OVERRIDE.
    path.chmod(0o750)


class Supervisor:
    def __init__(self, settings, config_path):
        self.settings, self.config_path = settings, str(Path(config_path).resolve())
        self.children, self.readers, self.waiters, self.critical_readers = {}, [], [], []
        self.stop = asyncio.Event()
        self.values = secret_values(os.environ)
        self.logs = settings.artifact_directory.parent / "logs"
        self.timeout = min(
            max(float(settings.get("supervisor", {}).get("term_timeout_seconds", 110)), 5), 120
        )
        self.uid = int(settings.get("supervisor", {}).get("uid", 10001))
        self.gid = int(settings.get("supervisor", {}).get("gid", 10001))

    async def output(self, role, process):
        instance, sequence = str(uuid4()), 0
        path = self.logs / ("stdout-" + role + "-" + instance + ".jsonl")
        with path.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            os.fchown(stream.fileno(), self.uid, self.gid)

            async def record(message):
                nonlocal sequence
                sequence += 1
                value = {
                    "schema_version": "2.0.0",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "service": "tianshu",
                    "role": role,
                    "instance_id": instance,
                    "sequence": sequence,
                    "event_id": str(uuid4()),
                    "event": "process.output",
                    "outcome": "unknown",
                    "level": "INFO",
                    "message": redact(message, self.values),
                }
                encoded = (
                    json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
                )

                def write():
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())

                await asyncio.to_thread(write)
                sys.stdout.buffer.write(encoded)
                sys.stdout.buffer.flush()

            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            pending, oversized = "", False
            while chunk := await process.stdout.read(16384):
                pending += decoder.decode(chunk)
                while "\n" in pending:
                    line, pending = pending.split("\n", 1)
                    if oversized:
                        await record("[oversize output line redacted]")
                        oversized = False
                    else:
                        await record(line)
                if len(pending) > 1024 * 1024:
                    # A request/body dump is not an ordinary diagnostic. Never buffer it
                    # without bound or leak a secret split across transport chunks.
                    oversized = True
                    pending = ""
            pending += decoder.decode(b"", final=True)
            if pending or oversized:
                await record("[oversize output line redacted]" if oversized else pending)

    async def spawn(
        self, name, command, *, uid=None, gid=None, core=False, critical=True, env_refs=None
    ):
        if (
            (name in self.children and self.children[name].returncode is None)
            or not isinstance(command, list)
            or not command
            or any(not isinstance(item, str) for item in command)
        ):
            raise ValueError("invalid_supervised_process")
        environment = (
            dict(os.environ)
            if core
            else {
                key: value
                for key, value in os.environ.items()
                if key
                in {
                    "PATH",
                    "HOME",
                    "LANG",
                    "LC_ALL",
                    "TZ",
                    "PYTHONPATH",
                    "PYTHONDONTWRITEBYTECODE",
                    "PYTHONUNBUFFERED",
                    "PLAYWRIGHT_BROWSERS_PATH",
                }
            }
        )
        for name_, reference in (env_refs or {}).items():
            if not re.fullmatch(r"(?:GF|VECTOR|LOKI|PROMETHEUS)_[A-Z0-9_]+", name_):
                raise ValueError("invalid_observation_environment")
            environment[name_] = self.settings.secrets.resolve(reference)
        child = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
            limit=65536,
            user=self.uid if uid is None else uid,
            group=self.gid if gid is None else gid,
            extra_groups=[self.gid],
            env=environment,
        )
        self.children[name] = child
        reader = asyncio.create_task(self.output(name, child), name="output-" + name)
        self.readers.append(reader)
        if critical:
            self.critical_readers.append(reader)
        if critical:
            self.waiters.append(asyncio.create_task(child.wait(), name="wait-" + name))
        return child

    def terminate(self, name, sig):
        child = self.children.get(name)
        if child and child.returncode is None:
            try:
                os.killpg(child.pid, sig)
            except ProcessLookupError:
                pass

    async def shutdown(self):
        # First close ingress. Workers then stop fetching and drain through Procrastinate.
        self.terminate("interactive", signal.SIGTERM)
        self.terminate("worker", signal.SIGTERM)
        core = [self.children[name] for name in ("interactive", "worker") if name in self.children]
        try:
            async with asyncio.timeout(max(self.timeout - 15, 1)):
                await asyncio.gather(*(child.wait() for child in core))
        except TimeoutError:
            pass
        self.terminate("model-broker", signal.SIGTERM)
        for name in self.children:
            if name not in {"interactive", "worker", "model-broker"}:
                self.terminate(name, signal.SIGTERM)
        try:
            async with asyncio.timeout(10):
                await asyncio.gather(*(child.wait() for child in self.children.values()))
        except TimeoutError:
            pass
        for name in self.children:
            self.terminate(name, signal.SIGKILL)
        await asyncio.gather(*(child.wait() for child in self.children.values()))
        # A browser/plugin may outlive its immediate parent after abrupt failure.
        # PID 1/subreaper owns these descendants; finish the complete container process tree.
        if sys.platform == "linux" and os.getpid() == 1:
            try:
                os.kill(-1, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await asyncio.gather(*self.readers, return_exceptions=False)
        if sys.platform == "linux" and os.getpid() == 1:
            for _ in range(40):
                try:
                    pid, _status = os.waitpid(-1, os.WNOHANG)
                except ChildProcessError:
                    break
                if pid == 0:
                    await asyncio.sleep(0.05)

    async def run(self):
        if sys.platform != "linux":
            raise ValueError("container_supervisor_requires_linux")
        if os.geteuid() != 0 or self.uid <= 0 or self.gid <= 0:
            raise ValueError("supervisor_requires_root_and_unprivileged_business_identity")
        if ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise RuntimeError("subreaper_unavailable")
        data = self.settings.artifact_directory.parent
        if not data.resolve().is_relative_to(Path("/data")):
            raise ValueError("supervised_product_data_must_be_under_data")
        directories = [
            data,
            self.settings.artifact_directory,
            self.logs,
            data / "state",
            data / "channels",
            data / "plugins",
            data / "capabilities",
            data / "observability",
            *(
                data / "observability" / name
                for name in ("vector", "loki", "prometheus", "grafana")
            ),
        ]
        for path in directories:
            _initialize_directory(path, self.uid, self.gid)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.stop.set)
        controller = None
        try:
            comics = self.settings.get("supervisor", {}).get("comics")
            if comics:
                from .comics import ComicsController

                controller = ComicsController(self, comics)
                await controller.open()
            for entry in self.settings.get("observability", {}).get("processes", []):
                if entry["name"] not in {"vector", "loki", "prometheus", "grafana"}:
                    raise ValueError("unknown_observation_process")
                await self.spawn(entry["name"], entry["command"], env_refs=entry.get("env_refs"))
            migration = await self.spawn(
                "migrate",
                [sys.executable, "-m", "tianshu", "migrate", "--config", self.config_path],
                core=True,
                critical=False,
            )
            migration_wait, migration_stop = (
                asyncio.create_task(migration.wait()),
                asyncio.create_task(self.stop.wait()),
            )
            try:
                done, _ = await asyncio.wait(
                    [migration_wait, migration_stop, *self.waiters, *self.critical_readers],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if self.stop.is_set():
                    return
                if migration_wait not in done or migration_wait.result() != 0:
                    raise RuntimeError("product_migration_failed")
            finally:
                migration_stop.cancel()
                migration_wait.cancel()
                await asyncio.gather(migration_stop, migration_wait, return_exceptions=True)
            for role in ("model-broker", "worker", "interactive"):
                await self.spawn(
                    role,
                    [sys.executable, "-m", "tianshu", role, "--config", self.config_path],
                    core=True,
                )
            stopped = asyncio.create_task(self.stop.wait())
            done, _ = await asyncio.wait(
                [stopped, *self.waiters, *self.critical_readers],
                return_when=asyncio.FIRST_COMPLETED,
            )
            stopped.cancel()
            await asyncio.gather(stopped, return_exceptions=True)
            if not self.stop.is_set():
                for task in done:
                    task.result()
                raise RuntimeError("supervised_process_exited")
        finally:
            if controller:
                await controller.close()
            await self.shutdown()
