"""Native client bridge without a Docker daemon, credentials, or live database."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ops.recovery import postgres_backend, postgres_container as bridge


class PostgresContainerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="postgres-client-bridge-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.configuration = {
            "docker": str(self.root / "docker"),
            "container_id": "a" * 64,
            "image_id": "sha256:" + "b" * 64,
            "endpoint": {
                "host": "198.51.100.10",
                "port": 25008,
                "container_port": 5432,
            },
        }
        self.document = {"postgres_container": self.configuration}
        self.environment = dict(
            os.environ,
            PGHOST="198.51.100.10",
            PGPORT="25008",
            PGUSER="synthetic",
            PGDATABASE="fixture",
            PGPASSWORD="PRIVATE_PASSWORD_CANARY",
            PGSSLMODE="verify-full",
            PGOPTIONS="-c default_transaction_read_only=on",
            PGSERVICE="must-not-forward",
        )
        self.identity = {
            "id": self.configuration["container_id"],
            "image_id": self.configuration["image_id"],
            "running": True,
            "paused": False,
            "restarting": False,
            "ports": {"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": "25008"}]},
        }
        self.calls = []

    def runner(self, arguments, **options):
        self.calls.append((arguments, options))
        if arguments[1] == "inspect":
            return subprocess.CompletedProcess(
                arguments, 0, stdout=json.dumps(self.identity).encode()
            )
        self.assertEqual(arguments[1:3], ["exec", "-i"])
        return subprocess.CompletedProcess(arguments, 0)

    def execute(self, tool, arguments, **options):
        return bridge.execute(
            self.document,
            tool,
            arguments,
            environment=self.environment,
            input_stream=subprocess.DEVNULL,
            output_stream=subprocess.DEVNULL,
            runner=options.pop("runner", self.runner),
            **options,
        )

    def test_environment_names_only_and_target_identity_precede_every_client(self):
        self.execute("psql", ["-X", "--no-password", "--command", "SELECT 1"])
        inspected, executed = self.calls
        self.assertEqual(inspected[0][-1], self.configuration["container_id"])
        self.assertNotIn("Config", inspected[0][-2])
        self.assertIn("NetworkSettings.Ports", inspected[0][-2])
        self.assertIn("PGPASSWORD", executed[0])
        self.assertNotIn(
            "PRIVATE_PASSWORD_CANARY", repr([call[0] for call in self.calls])
        )
        self.assertNotIn("PGSERVICE", executed[0])
        self.assertNotIn("PGSERVICE", executed[1]["env"])
        self.assertEqual(executed[1]["env"]["PGPASSWORD"], "PRIVATE_PASSWORD_CANARY")
        self.assertEqual(executed[1]["env"]["PGHOST"], "198.51.100.10")
        self.assertEqual(executed[1]["env"]["PGPORT"], "25008")
        self.assertEqual(executed[1]["env"]["PGSSLMODE"], "verify-full")
        self.assertEqual(executed[1]["stderr"], subprocess.DEVNULL)

    def test_changed_container_image_running_state_and_port_refuse_without_exec_or_new_file(
        self,
    ):
        changes = [
            ("id", "c" * 64),
            ("image_id", "sha256:" + "c" * 64),
            ("running", False),
            ("paused", True),
            ("restarting", True),
            ("ports", {"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": "25009"}]}),
            ("ports", {"5433/tcp": [{"HostIp": "0.0.0.0", "HostPort": "25008"}]}),
            ("ports", {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "25008"}]}),
        ]
        for key, value in changes:
            with (
                self.subTest(field=key, value=value),
                patch.dict(self.identity, {key: value}),
            ):
                self.calls.clear()
                output = self.root / "must-not-exist.dump"
                with self.assertRaisesRegex(
                    bridge.BridgeError, "postgresql_container_identity_mismatch"
                ):
                    self.execute(
                        "pg_dump", ["--format=custom", "--file=" + str(output)]
                    )
                self.assertEqual(len(self.calls), 1)
                self.assertFalse(output.exists())

    def test_endpoint_and_connection_overrides_refuse_before_docker(self):
        for key, value in (
            ("PGHOST", "other.example"),
            ("PGPORT", "5432"),
            ("PGHOSTADDR", "198.51.100.11"),
        ):
            with (
                self.subTest(environment=key),
                patch.dict(self.environment, {key: value}),
            ):
                with self.assertRaisesRegex(
                    bridge.BridgeError, "postgresql_container_endpoint_mismatch"
                ):
                    self.execute("psql", ["-X"])
        for arguments in (
            ["--host=other"],
            ["-h", "other"],
            ["--dbname=other"],
            ["other"],
            ["--port=1"],
            ["-U", "other"],
            ["--password"],
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaisesRegex(
                    bridge.BridgeError, "postgresql_container_arguments_invalid"
                ):
                    self.execute("psql", arguments)
        self.assertEqual(self.calls, [])

    def test_psql_binary_stdin_and_stdout_pass_through_real_local_process_without_decoding(
        self,
    ):
        payload = b"SQL fixture\x00\xff\r\n\xe4\xb8\xad\xe6\x96\x87\n"

        def runner(arguments, **options):
            if arguments[1] == "inspect":
                return self.runner(arguments, **options)
            return subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())",
                ],
                **options,
            )

        with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as target:
            source.write(payload)
            source.seek(0)
            bridge.execute(
                self.document,
                "psql",
                ["-X"],
                environment=self.environment,
                input_stream=source,
                output_stream=target,
                runner=runner,
            )
            target.seek(0)
            self.assertEqual(target.read(), payload)

    def test_cli_streaming_uses_internal_stdout_pipe_and_fixed_client_failure(self):
        payload = b"\x00\xff\r\n" + b"x" * 70000
        native_popen = subprocess.Popen
        for returncode in (0, 3):

            def spawn(command, **options):
                self.assertEqual(command[1:3], ["exec", "-i"])
                self.assertEqual(options["stdout"], subprocess.PIPE)
                self.assertEqual(options["stderr"], subprocess.DEVNULL)
                self.assertNotIn("PRIVATE_PASSWORD_CANARY", repr(command))
                program = (
                    "import sys;sys.stdout.buffer.write(sys.stdin.buffer.read());sys.stderr.write('PRIVATE_PASSWORD_CANARY');sys.exit("
                    + str(returncode)
                    + ")"
                )
                return native_popen([sys.executable, "-c", program], **options)

            with (
                self.subTest(returncode=returncode),
                tempfile.TemporaryFile() as source,
                tempfile.TemporaryFile() as target,
            ):
                source.write(payload)
                source.seek(0)
                with patch.object(bridge.subprocess, "Popen", side_effect=spawn):

                    def invoke():
                        return bridge.execute(
                            self.document,
                            "psql",
                            ["-X"],
                            environment=self.environment,
                            input_stream=source,
                            output_stream=target,
                            runner=self.runner,
                            streaming_psql=True,
                        )

                    if returncode:
                        with self.assertRaisesRegex(
                            bridge.BridgeError, "^postgresql_container_client_failed$"
                        ):
                            invoke()
                    else:
                        self.assertEqual(invoke(), 0)
                target.seek(0)
                self.assertEqual(target.read(), payload)

    def test_dump_stream_uses_exclusive_host_file_and_never_passes_host_path_to_container(
        self,
    ):
        payload = b"PGDMP\x00\xff\r\narchive payload"
        output = self.root / "archive.dump"

        def runner(arguments, **options):
            if arguments[1] == "inspect":
                return self.runner(arguments, **options)
            self.calls.append((arguments, options))
            self.assertNotIn(str(output), repr(arguments))
            self.assertNotIn("--file", repr(arguments))
            return subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import sys;sys.stdout.buffer.write(" + repr(payload) + ")",
                ],
                **options,
            )

        self.execute(
            "pg_dump",
            [
                "--no-password",
                "--format=custom",
                "--strict-names",
                "--schema=ts_fixture",
                "--file=" + str(output),
            ],
            runner=runner,
        )
        self.assertEqual(output.read_bytes(), payload)
        if os.name == "posix":
            self.assertEqual(output.stat().st_mode & 0o077, 0)
        self.calls.clear()
        with self.assertRaisesRegex(
            bridge.BridgeError, "postgresql_container_client_failed"
        ):
            self.execute("pg_dump", ["--format=custom", "--file=" + str(output)])
        self.assertEqual(output.read_bytes(), payload)
        self.assertEqual(len(self.calls), 1)

    def test_restore_streams_host_archive_to_stdin_and_only_allows_current_scratch_database(
        self,
    ):
        archive = self.root / "archive.dump"
        payload = b"PGDMP\x00\xff\r\nrestore payload"
        archive.write_bytes(payload)
        scratch = "ts_restore_" + "c" * 32
        self.environment["PGDATABASE"] = scratch
        arguments = [
            "--no-password",
            "--dbname=" + scratch,
            "--no-owner",
            "--no-privileges",
            "--single-transaction",
            "--exit-on-error",
            str(archive),
        ]

        def runner(command, **options):
            if command[1] == "inspect":
                return self.runner(command, **options)
            self.calls.append((command, options))
            self.assertNotIn(str(archive), command)
            self.assertEqual(options["stdin"].read(), payload)
            return subprocess.CompletedProcess(command, 0)

        self.execute("pg_restore", arguments, runner=runner)
        self.assertEqual(archive.read_bytes(), payload)
        self.assertIn(scratch, self.calls[-1][0])
        for target in (
            "production",
            "postgresql://fixture:PRIVATE_PASSWORD_CANARY@other/db",
            "ts_restore_" + "d" * 32,
        ):
            self.calls.clear()
            with (
                self.subTest(target=target),
                self.assertRaisesRegex(
                    bridge.BridgeError, "postgresql_container_restore_target_invalid"
                ),
            ):
                self.execute("pg_restore", ["--dbname=" + target, str(archive)])
            self.assertEqual(self.calls, [])

    def test_restore_file_mutation_is_detected_and_ordinary_read_is_accepted(self):
        archive = self.root / "archive.dump"
        archive.write_bytes(b"PGDMP fixture")
        scratch = "ts_restore_" + "c" * 32
        self.environment["PGDATABASE"] = scratch
        for mutate in (False, True):

            def runner(command, **options):
                if command[1] == "inspect":
                    return self.runner(command, **options)
                options["stdin"].read()
                if mutate:
                    info = archive.stat()
                    os.utime(
                        archive, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000000)
                    )
                return subprocess.CompletedProcess(command, 0)

            with self.subTest(mutate=mutate):
                if mutate:
                    with self.assertRaisesRegex(
                        bridge.BridgeError, "postgresql_container_restore_file_changed"
                    ):
                        self.execute(
                            "pg_restore",
                            ["--dbname=" + scratch, str(archive)],
                            runner=runner,
                        )
                else:
                    self.execute(
                        "pg_restore",
                        ["--dbname=" + scratch, str(archive)],
                        runner=runner,
                    )

    def test_client_failure_and_timeout_keep_partial_new_archive_and_report_fixed_code(
        self,
    ):
        for timeout in (False, True):
            output = self.root / (str(timeout) + ".dump")

            def runner(command, **options):
                if command[1] == "inspect":
                    return self.runner(command, **options)
                options["stdout"].write(b"unverified partial")
                if timeout:
                    raise subprocess.TimeoutExpired("PRIVATE_PASSWORD_CANARY", 1)
                return subprocess.CompletedProcess(command, 1)

            with (
                self.subTest(timeout=timeout),
                self.assertRaisesRegex(
                    bridge.BridgeError, "^postgresql_container_client_failed$"
                ),
            ):
                self.execute(
                    "pg_dump",
                    ["--format=custom", "--file=" + str(output)],
                    runner=runner,
                )
            self.assertEqual(output.read_bytes(), b"unverified partial")

    def test_private_configuration_and_standalone_cli_errors_do_not_echo_inputs(self):
        config = self.root / "storage-backup.json"
        config.write_text(json.dumps(self.document))
        config.chmod(0o600)
        self.assertEqual(bridge.load_configuration(config), self.document)
        config.write_text("PRIVATE_PASSWORD_CANARY invalid JSON")
        with patch.object(sys, "stderr", io.StringIO()) as errors:
            self.assertEqual(
                bridge.main(["--config", str(config), "--tool", "psql", "--", "-X"]), 2
            )
        self.assertEqual(
            errors.getvalue().strip(), "postgresql_container_configuration_invalid"
        )


class PrivateToolDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="postgres-tool-discovery-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "config/storage-backup.json"
        self.config.parent.mkdir()
        self.paths = {
            tool: str(self.root / tool) for tool in ("psql", "pg_dump", "pg_restore")
        }

    def test_private_host_tools_are_read_without_backend_connections(self):
        self.assertIsNone(postgres_backend.deployment_tools(self.root))
        self.config.write_text(json.dumps({"postgresql_connections": {}}))
        self.assertIsNone(postgres_backend.deployment_tools(self.root))
        self.config.write_text(
            json.dumps(
                {
                    "postgresql_tools": self.paths,
                    "postgres_container": {"not_read_by_discovery": True},
                }
            )
        )
        self.assertEqual(postgres_backend.deployment_tools(self.root), self.paths)
        for value in (
            {"psql": "relative"},
            {**self.paths, "extra": "x"},
            dict(self.paths, psql="relative"),
        ):
            self.config.write_text(json.dumps({"postgresql_tools": value}))
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(Exception, "postgresql_backup_tools_required"),
            ):
                postgres_backend.deployment_tools(self.root)

    def test_explicit_tools_override_private_configuration_and_absent_config_keeps_path_default(
        self,
    ):
        self.config.write_text("INVALID PRIVATE CONFIG MUST NOT BE READ")
        for explicit in (True, False):
            if not explicit:
                self.config.unlink()
            expected = (
                self.paths
                if explicit
                else {tool: "native-" + tool for tool in self.paths}
            )
            with (
                self.subTest(explicit=explicit),
                patch.object(
                    postgres_backend, "binding_files", return_value=[("fixture", {})]
                ),
                patch.object(
                    postgres_backend, "Tools", side_effect=RuntimeError("selected")
                ) as selected,
                patch.object(
                    postgres_backend.shutil,
                    "which",
                    side_effect=lambda name: "native-" + name,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "selected"):
                    postgres_backend.export_and_verify(
                        self.root,
                        self.root / "operation",
                        {},
                        {},
                        self.paths if explicit else None,
                        lambda: None,
                    )
                self.assertEqual(selected.call_args.args[0], expected)

    def test_sqlite_without_binding_does_not_read_private_tools(self):
        self.config.write_text("INVALID PRIVATE CONFIG MUST NOT BE READ")
        with patch.object(postgres_backend, "binding_files", return_value=[]):
            self.assertEqual(
                postgres_backend.export_and_verify(
                    self.root, self.root / "operation", {}, None, None, lambda: None
                ),
                ([], set()),
            )
