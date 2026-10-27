import copy
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import linux_origin_diagnostic as tool


TOKEN = "synthetic-only-token-" + "x" * 24
REF = "origin:" + "a" * 32


def settings(path):
    return {
        "mode": "service_https",
        "model_origin_renewal_http": True,
        "database_path": str(path),
        "principals": {
            "gateway": {
                "kind": "service",
                "service": "gateway",
                "token_env": "GW_TOKEN",
                "actions": ["config.snapshot"],
            }
        },
        "entries": {"model": {"owner": "gateway", "routes": [tool.ROUTE]}},
    }


class Database:
    def __init__(self, config, expiry, *, identity=None, revoked=False, missing=False):
        from services.platform.contracts import digest

        self.db = sqlite3.connect(":memory:")
        self.db.executescript(
            "CREATE TABLE origins(ref,entry_id,entry_digest,expires_at,revoked);"
            "CREATE TABLE revoked_entries(id);CREATE TABLE revoked_principals(id);"
        )
        self.db.execute(
            "INSERT INTO origins VALUES(?,?,?,?,?)",
            (REF, "model", digest(config["entries"]["model"]), expiry, int(revoked)),
        )
        self.db.commit()
        if missing:
            self.db.execute("DELETE FROM origins")
            self.db.commit()
        self.db.execute("PRAGMA query_only=ON")
        self.identity = identity

    def execute(self, sql, parameters=()):
        if sql == "SHOW default_transaction_read_only":
            return SimpleNamespace(fetchone=lambda: ("on",))
        if sql == tool.clone.IDENTITY_SQL:
            return SimpleNamespace(fetchone=lambda: (self.identity,))
        if sql == "SELECT current_database()":
            return SimpleNamespace(fetchone=lambda: ("original-source-database",))
        return self.db.execute(sql, parameters)

    def close(self):
        self.db.close()


class OriginDiagnosticTests(unittest.TestCase):
    def test_snapshot_ref_comparison_uses_hashed_original_backup_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup = root / "backup"
            backup.mkdir()
            gateway = {"platform_origin_env": "MODEL_REF"}
            config, env = backup / "gateway.json", backup / "gateway.env"
            config.write_text(json.dumps(gateway))
            old_ref = "origin:" + "b" * 32
            env.write_bytes(
                b"OTHER='unchanged'\r\nMODEL_REF='" + old_ref.encode() + b"'\r\n"
            )
            manifest = {
                "inventory": {
                    "entries": {
                        path.name: {"kind": "file", "sha256": tool.clone.sha(path)}
                        for path in (config, env)
                    }
                }
            }
            (root / "manifest.json").write_text(json.dumps(manifest))
            (root / "receipt.json").write_text(
                json.dumps(
                    {
                        "status": "offline_restore_verified",
                        "manifest_sha256": tool.clone.sha(root / "manifest.json"),
                    }
                )
            )
            _, result = tool.snapshot_origin(
                root, "gateway.json", "gateway.env", gateway, REF
            )
            self.assertTrue(result["snapshot_gateway_ref_present"])
            self.assertFalse(result["snapshot_gateway_ref_equals_live"])
            self.assertNotIn(old_ref, json.dumps(result))
            env.write_bytes(env.read_bytes() + b"CHANGED=1\n")
            with self.assertRaisesRegex(
                tool.clone.CloneError, "snapshot_gateway_input_changed"
            ):
                tool.snapshot_origin(root, "gateway.json", "gateway.env", gateway, REF)

    def test_missing_origin_still_checks_registered_entry_by_source_entry_id(self):
        config = settings("/data/authority.sqlite")
        with (
            tool.closing(Database(config, 1100)) as source,
            tool.closing(Database(config, 900, missing=True)) as target,
        ):
            left = tool.facts(source, config, {"GW_TOKEN": TOKEN}, REF, TOKEN, 1000)
            right = tool.facts(
                target,
                config,
                {"GW_TOKEN": TOKEN},
                REF,
                TOKEN,
                1000,
                source_origin=left["row"],
            )
            result = tool.comparison(left, right, 1000)
            self.assertEqual(
                result["clone_failed_checks"],
                ["origin_exists", "origin_not_revoked", "origin_unexpired"],
            )
            self.assertTrue(result["missing_same_ref_clone_registry_matches"])
            self.assertFalse(result["expired_same_ref_clone_candidate"])
            changed = copy.deepcopy(config)
            changed["entries"]["model"]["routes"] = []
            wrong = tool.facts(
                target,
                changed,
                {"GW_TOKEN": TOKEN},
                REF,
                TOKEN,
                1000,
                source_origin=left["row"],
            )
            self.assertFalse(
                tool.comparison(left, wrong, 1000)[
                    "missing_same_ref_clone_registry_matches"
                ]
            )

    def test_expired_same_ref_is_a_candidate_without_writes_or_http_claims(self):
        config = settings("/data/authority.sqlite")
        with (
            tool.closing(Database(config, 1100)) as source,
            tool.closing(Database(config, 900)) as target,
        ):
            left = tool.facts(source, config, {"GW_TOKEN": TOKEN}, REF, TOKEN, 1000)
            right = tool.facts(target, config, {"GW_TOKEN": TOKEN}, REF, TOKEN, 1000)
            result = tool.comparison(left, right, 1000)
            self.assertTrue(result["expired_same_ref_clone_candidate"])
            self.assertEqual(result["clone_failed_checks"], ["origin_unexpired"])
            self.assertEqual(result["source_expires_in_seconds"], 100)
            self.assertNotIn(REF, json.dumps(result))
            with self.assertRaises(sqlite3.OperationalError):
                target.execute("UPDATE origins SET expires_at=1200")

    def test_revocation_different_entry_and_different_complete_row_refuse_candidate(
        self,
    ):
        config = settings("/data/authority.sqlite")
        with tool.closing(Database(config, 1100)) as source:
            left = tool.facts(source, config, {"GW_TOKEN": TOKEN}, REF, TOKEN, 1000)
            for kind in ("revoked", "entry", "ref", "owner", "digest"):
                with (
                    self.subTest(kind=kind),
                    tool.closing(Database(config, 900)) as target,
                ):
                    changed = copy.deepcopy(config)
                    if kind == "entry":
                        changed["entries"]["model"]["routes"] = []
                    right = tool.facts(
                        target, changed, {"GW_TOKEN": TOKEN}, REF, TOKEN, 1000
                    )
                    if kind == "revoked":
                        right["row"]["revoked"] = 1
                    elif kind == "ref":
                        right["row"]["ref"] = "origin:" + "b" * 32
                    elif kind == "owner":
                        right["checks"]["owner_not_revoked"] = False
                    elif kind == "digest":
                        right["row"]["entry_digest"] = "changed"
                    self.assertFalse(
                        tool.comparison(left, right, 1000)[
                            "expired_same_ref_clone_candidate"
                        ]
                    )

    def test_explicit_inputs_resolve_actual_owner_mounts_and_secret_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root, target_root = root / "source", root / "clone"
            source_root.mkdir()
            target_root.mkdir()
            source = settings("/data/authority.sqlite")
            target = settings(target_root / "authority.sqlite")
            target["storage_backends"] = {
                "postgresql_url": "postgresql://scratch:fictional@localhost/scratch"
            }
            gateway = {
                "platform_origin_env": "MODEL_REF",
                "platform_credential_ref": "platform-secret-ref",
                "secret_references": {"platform-secret-ref": "GW_TOKEN"},
            }
            owners = {"unused" + str(index): {} for index in range(11)}
            for name, config, env in (
                ("platform", source, {"GW_TOKEN": TOKEN}),
                ("gateway", gateway, {"GW_TOKEN": TOKEN, "MODEL_REF": REF}),
            ):
                path = source_root / (name + ".json")
                path.write_text(json.dumps(config))
                owners[name] = {
                    "Config": {
                        "Cmd": ["--settings=/etc/tianshu/settings.json"],
                        "Env": [key + "=" + value for key, value in env.items()],
                    },
                    "Mounts": [
                        {
                            "Type": "bind",
                            "Source": str(path),
                            "Destination": "/etc/tianshu/settings.json",
                        },
                        {
                            "Type": "bind",
                            "Source": str(source_root),
                            "Destination": "/data",
                        },
                    ],
                }
            paths = {}
            for name, document in (
                ("owners", owners),
                ("settings", target),
                ("binding", {}),
                ("state", {}),
            ):
                paths[name] = root / (name + ".json")
                paths[name].write_text(json.dumps(document))
            resource = {"database": "scratch", "role": "scratch", "comment": "owned"}
            identity = [
                "scratch",
                "scratch",
                "scratch",
                "scratch",
                "owned",
                False,
                False,
                False,
                False,
                False,
                True,
                "owned",
            ]
            db_source, db_target = (
                Database(source, 1100),
                Database(target, 900, identity=identity),
            )
            with (
                patch.object(
                    tool.clone,
                    "owned_binding",
                    return_value=(
                        {
                            "postgresql_url": target["storage_backends"][
                                "postgresql_url"
                            ]
                        },
                        resource,
                    ),
                ),
                patch.object(
                    tool,
                    "open_database",
                    side_effect=[
                        (db_source, SimpleNamespace(postgresql_url="different-source")),
                        (db_target, None),
                    ],
                ),
                patch.object(tool.time, "time", return_value=1000),
            ):
                result = tool.diagnose(
                    owners_inspect=paths["owners"],
                    source_root=source_root,
                    clone_settings=paths["settings"],
                    clone_root=target_root,
                    binding=paths["binding"],
                    state=paths["state"],
                )
            self.assertTrue(result["expired_same_ref_clone_candidate"])
            self.assertFalse(result["clone_gateway_environment_checked"])
            self.assertFalse(result["http_authentication_or_readiness_claimed"])
            self.assertFalse(result["database_or_marker_modified"])
            self.assertNotIn(TOKEN, json.dumps(result))
            self.assertNotIn("postgresql://", json.dumps(result))

    def test_cli_failure_does_not_print_database_error_or_credentials(self):
        args = [
            value
            for name in (
                "owners-inspect",
                "source-root",
                "clone-settings",
                "clone-root",
                "binding",
                "state",
                "output",
            )
            for value in ("--" + name, "unused")
        ]
        with (
            patch.object(tool, "diagnose", side_effect=RuntimeError(TOKEN)),
            patch("builtins.print") as output,
        ):
            self.assertEqual(tool.main(args), 2)
        document = json.loads(output.call_args.args[0])
        self.assertEqual(document["reason"], "readonly_diagnostic_failed")
        self.assertNotIn(TOKEN, output.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
