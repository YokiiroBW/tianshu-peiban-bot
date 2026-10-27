"""Offline protocol/ownership checks; no Docker, PostgreSQL or credentials used."""

from contextlib import redirect_stdout, redirect_stderr
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import linux_clone as clone
import linux_clone_clients as clients


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.stage, self.snapshot = self.root / "stage", self.root / "snapshot"
        self.stage.mkdir()
        self.snapshot.mkdir()
        self.identity = "a" * 32
        self.role, self.database = (
            "ts325_role_" + self.identity,
            "ts325_" + self.identity,
        )
        resource = {
            "kind": "pg2",
            "isolation_id": self.identity,
            "role": self.role,
            "database": self.database,
            "comment": "tianshu:ts325:" + "b" * 64 + ":pg2:" + self.role,
            "role_created": True,
            "database_created": True,
            "cleaned": False,
            "binding_file": str(self.root / "never-follow-this-binding.json"),
        }
        self.state = {
            "version": 1,
            "purpose": "isolated-group-modes-release-pg2",
            "token": "b" * 64,
            "status": "prepared_not_run",
            "admin_binding_file": str(self.root / "never-read-admin.json"),
            "resources": [resource],
        }
        self.state_file = self.root / "state.json"
        self.state_file.write_text(json.dumps(self.state))
        self.manifest = {
            "authority_root": str(self.root / "production"),
            "owners": [],
            "postgresql": [],
        }
        for index, service in enumerate(
            ("platform", "gateway", "memory", "knowledge", "companion", "actor-memory")
        ):
            source = self.snapshot / (str(index) + ".dump")
            source.write_bytes(b"synthetic archive " + service.encode())
            schema = "schema_" + str(index)
            self.manifest["postgresql"].append(
                {
                    "service": service,
                    "archive": source.name,
                    "sha256": clone.sha(source),
                    "bytes": source.stat().st_size,
                    "schemas": {"main": schema},
                    "logical_rows": {
                        schema + ".table_one": {"kind": "r"},
                        schema + ".sequence_one": {"kind": "S"},
                    },
                }
            )
        (self.snapshot / "manifest.json").write_text(json.dumps(self.manifest))
        self.context = {
            "version": 1,
            "purpose": "owned-pg17-clone-clients",
            "run_id": "c" * 32,
            "resource": {
                "isolation_id": self.identity,
                "database": self.database,
                "role": self.role,
            },
            "query_sha256": clients.allowed_queries(self.manifest),
            "image": clients.IMAGE,
            "network": {"name": "host", "id": "d" * 64},
            "docker": "/usr/bin/docker",
        }
        self.environment = {
            "PGHOST": clients.PG_HOST,
            "PGPORT": clients.PG_PORT,
            "PGUSER": self.role,
            "PGDATABASE": self.database,
            "PGPASSWORD": "SYNTHETIC_ONLY",
        }

    def prepare(self):
        with (
            patch.object(
                clone, "manifest", return_value=(self.snapshot, self.manifest)
            ),
            patch.object(
                clients, "inspect_runtime", return_value=self.context["network"]
            ),
        ):
            return clients.prepare_clients(
                self.stage,
                self.state_file,
                snapshot=self.snapshot,
                network="host",
                docker="/usr/bin/docker",
                python="/usr/bin/python3",
            )

    def test_generation_keeps_original_archive_permissions_and_no_secrets_in_context(
        self,
    ):
        before = [
            (clone.sha(path), path.stat().st_mode)
            for path in self.snapshot.glob("*.dump")
        ]
        receipt = self.prepare()
        after = [
            (clone.sha(path), path.stat().st_mode)
            for path in self.snapshot.glob("*.dump")
        ]
        self.assertEqual(before, after)
        self.assertEqual(receipt["archives"], 6)
        self.assertFalse(receipt["source_archive_permissions_changed"])
        context_path = Path(receipt["context"])
        content = context_path.read_text()
        self.assertNotIn("SYNTHETIC_ONLY", content)
        self.assertNotIn("b" * 64, content)
        context = clients.load_context(context_path)
        self.assertEqual(context["resource"]["database"], self.database)
        for record in context["archives"]:
            self.assertEqual(clone.sha(record["copy"]), record["sha256"])
        for path in receipt["postgresql_tools"].values():
            compile(Path(path).read_bytes(), path, "exec")
        self.assertEqual(
            clone.read(receipt["tools_configuration"]),
            {"postgresql_tools": receipt["postgresql_tools"]},
        )

    def test_changed_state_is_refused_but_cleanup_can_still_find_clients(self):
        receipt = self.prepare()
        self.state["resources"][0]["cleaned"] = True
        self.state_file.write_text(json.dumps(self.state))
        with self.assertRaises(clone.CloneError):
            clients.load_context(receipt["context"])
        self.assertEqual(
            clients.load_context(receipt["context"], check_state=False)["run_id"],
            clone.read(receipt["context"])["run_id"],
        )

    def test_psql_only_accepts_frozen_tools_query_and_exact_arguments(self):
        valid = [*clients.QUERY_PREFIX, clone.IDENTITY_SQL]
        self.assertEqual(
            clients.client_arguments(self.context, "psql", valid), (valid, False)
        )
        for sql in (
            "SELECT 1",
            "DROP DATABASE production",
            "SELECT set_config('transaction_read_only','off',false)",
            clone.IDENTITY_SQL + ";DROP DATABASE production",
            "SELECT pg_terminate_backend(1)",
        ):
            with self.subTest(sql=sql), self.assertRaises(clone.CloneError):
                clients.client_arguments(
                    self.context, "psql", [*clients.QUERY_PREFIX, sql]
                )
        with self.assertRaises(clone.CloneError):
            clients.client_arguments(self.context, "psql", ["-h", "other", *valid])
        with self.assertRaises(clone.CloneError):
            clients.client_arguments(self.context, "pg_dump", ["--format=custom"])
        for tool in ("psql", "pg_dump", "pg_restore"):
            self.assertEqual(
                clients.client_arguments(self.context, tool, ["--version"]),
                (["--version"], False),
            )

    def test_restore_only_owned_database_and_exact_hashed_copy(self):
        receipt = self.prepare()
        context = clients.load_context(receipt["context"])
        original = context["archives"][0]["original"]
        valid = [
            "--no-password",
            "--dbname=" + self.database,
            *clients.RESTORE_FLAGS[1:],
            original,
        ]
        translated, mounted = clients.client_arguments(context, "pg_restore", valid)
        self.assertTrue(mounted)
        self.assertEqual(translated[-1], "/archives/0.dump")
        for wrong in (
            [*valid[:1], "--dbname=production", *valid[2:]],
            [*valid[:-1], "/not-approved.dump"],
            [*valid, "--clean"],
        ):
            with self.subTest(arguments=wrong), self.assertRaises(clone.CloneError):
                clients.client_arguments(context, "pg_restore", wrong)
        copied = Path(context["archives"][0]["copy"])
        copied.chmod(0o600)
        copied.write_bytes(b"corrupt")
        with self.assertRaises(clone.CloneError):
            clients.client_arguments(context, "pg_restore", valid)

    def test_connection_is_fixed_and_psql_ignores_unsafe_ambient_options(self):
        inherited = {
            **self.environment,
            "PGOPTIONS": "-c role=postgres",
            "PGSERVICE": "production",
            "PGPASSFILE": "/credential",
            "DOCKER_CONTEXT": "remote",
        }
        value = clients.client_environment(
            self.context, "psql", [*clients.QUERY_PREFIX, clone.IDENTITY_SQL], inherited
        )
        self.assertEqual(
            value["PGOPTIONS"],
            "-c extra_float_digits=3 -c default_transaction_read_only=on",
        )
        self.assertNotIn("PGSERVICE", value)
        self.assertNotIn("PGPASSFILE", value)
        self.assertNotIn("DOCKER_CONTEXT", value)
        for field, wrong in (
            ("PGHOST", "production"),
            ("PGPORT", "5432"),
            ("PGUSER", "postgres"),
            ("PGDATABASE", "production"),
            ("PGHOSTADDR", "192.0.2.1"),
        ):
            with self.subTest(field=field), self.assertRaises(clone.CloneError):
                clients.client_environment(
                    self.context, "psql", [], {**self.environment, field: wrong}
                )

    def test_docker_command_has_only_readonly_archive_bind_password_env_and_fixed_image(
        self,
    ):
        context = {
            **self.context,
            "archive_directory": "/new-stage/pg17-clients/archives",
        }
        value = clients.client_environment(context, "pg_restore", [], self.environment)
        command = clients.docker_command(
            context, "pg_restore", ["--no-owner"], value, True, "owned-client"
        )
        self.assertIn(clients.IMAGE, command)
        self.assertNotIn("SYNTHETIC_ONLY", " ".join(command))
        self.assertIn("PGPASSWORD", command)
        self.assertEqual(command[command.index("--network") + 1], "host")
        self.assertEqual(command[command.index("--user") + 1], "10001:10001")
        self.assertEqual(command[command.index("--cap-drop") + 1], "ALL")
        self.assertIn(
            "type=bind,src=/new-stage/pg17-clients/archives,dst=/archives,readonly",
            command,
        )
        self.assertNotIn("--privileged", command)
        query = clients.docker_command(
            context, "psql", [], value, False, "owned-client"
        )
        self.assertNotIn("--mount", query)

    def test_stdout_remains_byte_exact_and_child_does_not_inherit_tools_pipe(self):
        raw = b'{"field":"\xe4\xb8\xad"}\r\n[1,true]\n'
        output = BytesIO()
        process = Mock()
        process.stdout = BytesIO(raw)
        process.wait.return_value = 0
        process.poll.return_value = 0
        factory = Mock()
        factory.__enter__ = Mock(return_value=process)
        factory.__exit__ = Mock(return_value=False)
        with (
            patch.object(clients, "load_context", return_value=self.context),
            patch.object(
                clients, "inspect_json", return_value=self.context["network"]["id"]
            ),
            patch.object(clients.subprocess, "Popen", return_value=factory) as popen,
            patch.dict(os.environ, self.environment, clear=True),
        ):
            self.assertEqual(
                clients.run_client(
                    "explicit",
                    "psql",
                    [*clients.QUERY_PREFIX, clone.IDENTITY_SQL],
                    output=output,
                ),
                0,
            )
        self.assertEqual(output.getvalue(), raw)
        self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(popen.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_wrapper_refusal_never_contaminates_stdout_or_reveals_error_text(self):
        stdout, stderr = StringIO(), StringIO()
        with (
            patch.object(
                clients, "run_client", side_effect=RuntimeError("SYNTHETIC_ONLY")
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            self.assertEqual(clients.wrapper_main("context", "psql", []), 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn("SYNTHETIC_ONLY", stderr.getvalue())

    def test_cleanup_validates_all_clients_before_any_remove(self):
        identifiers = ["e" * 64, "f" * 64]
        listed = SimpleNamespace(
            returncode=0, stdout=("\n".join(identifiers) + "\n").encode()
        )
        with (
            patch.object(clients, "load_context", return_value=self.context),
            patch.object(clients.subprocess, "run", return_value=listed) as run,
            patch.object(
                clients,
                "owned_container",
                side_effect=[identifiers[0], clone.CloneError("wrong image")],
            ),
        ):
            with self.assertRaises(clone.CloneError):
                clients.cleanup_clients(self.stage)
        self.assertEqual(run.call_count, 1)
        with (
            patch.object(clients, "load_context", return_value=self.context),
            patch.object(clients, "owned_container", side_effect=identifiers),
            patch.object(
                clients.subprocess,
                "run",
                side_effect=[
                    listed,
                    SimpleNamespace(returncode=0),
                    SimpleNamespace(returncode=0),
                    SimpleNamespace(returncode=0, stdout=b""),
                ],
            ) as run,
        ):
            result = clients.cleanup_clients(self.stage)
        self.assertEqual(result["containers_removed"], 2)
        self.assertFalse(result["database_created_or_dropped"])
        self.assertEqual(
            [call.args[0][3] for call in run.call_args_list], ["ps", "rm", "rm", "ps"]
        )

    def test_existing_network_and_fixed_image_metadata_are_required(self):
        with patch.object(
            clients,
            "inspect_json",
            side_effect=[
                clients.IMAGE,
                {"Name": "host", "Id": "d" * 64, "Driver": "host"},
            ],
        ):
            self.assertEqual(
                clients.inspect_runtime("/usr/bin/docker", "host"),
                self.context["network"],
            )
        with (
            patch.object(clients, "inspect_json", return_value="wrong-image"),
            self.assertRaises(clone.CloneError),
        ):
            clients.inspect_runtime("/usr/bin/docker", "host")
        with self.assertRaises(clone.CloneError):
            clients.inspect_runtime("/usr/bin/docker", "")

    @unittest.skipUnless(
        os.environ.get("TIANSHU_CLONE_RECOVERY_BUNDLE"),
        "explicit frozen recovery fixture not selected",
    )
    def test_allowlist_accepts_actual_frozen_tools_fingerprint_protocol(self):
        recovery = clone.load_recovery(
            Path(os.environ["TIANSHU_CLONE_RECOVERY_BUNDLE"])
        )
        backend, resident = recovery["postgres_backend"], recovery["resident_snapshot"]
        manifest = {
            "postgresql": [
                {
                    "service": "platform",
                    "schemas": {"main": "schema_0"},
                    "logical_rows": {
                        "schema_0.table_one": {"kind": "r"},
                        "schema_0.sequence_one": {"kind": "S"},
                    },
                }
            ]
        }
        expected = clients.allowed_queries(manifest)
        observed = []

        class Capture(backend.Tools):
            def query(self, environment, statement):
                observed.append(statement)
                return [
                    ["schema_0", "table_one", "r"],
                    ["schema_0", "sequence_one", "S"],
                ]

        def child(arguments, **kwargs):
            observed.append(arguments[-1])
            process = Mock()
            process.stdout = BytesIO(b"[1,true]\n")
            process.wait.return_value = 0
            process.__enter__ = Mock(return_value=process)
            process.__exit__ = Mock(return_value=False)
            return process

        tools = Capture(
            {name: name for name in ("psql", "pg_restore", "pg_dump")},
            resident.Deadline(10),
        )
        with patch.object(backend.subprocess, "Popen", side_effect=child):
            result = tools.fingerprint({}, ["schema_0"])
        self.assertEqual(len(result), 2)
        self.assertTrue(
            all(clients.sql_digest(statement) in expected for statement in observed)
        )


if __name__ == "__main__":
    unittest.main()
