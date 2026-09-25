"""A1 short-lived origin admission using a stopped synthetic Platform store."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from ops.recovery.drill_origin_admission import check_origin_admission
from ops.recovery.safety import RecoveryError


class OriginAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.source = root / "source"
        self.inputs = root / "inputs"
        self.now = 1_800_000_000.0
        for directory in (
            self.source / "config/platform", self.source / "data/platform",
            self.inputs / "private",
        ):
            directory.mkdir(parents=True)
        self.settings = {
            "mode": "service_https",
            "database_path": "/var/lib/tianshu/platform.sqlite",
            "entries": {
                "config-entry": {"ttl_seconds": 300, "owner": "synthetic-admin"},
                "web-source-actor": {"ttl_seconds": 300, "owner": "synthetic-admin"},
            },
        }
        self._write(self.source / "config/platform/settings.json", self.settings)
        self.refs = {
            "config": "origin:" + "a" * 32,
            "actor": "origin:" + "b" * 32,
        }
        self.expiry = self.now + 240
        self.deadline = self.now + 220
        self.index = {
            "a1_origin_admission": {"minimum_remaining_seconds": 180},
            "files": {},
            "assertions": [],
        }
        for name, entry in (("config", "config-entry"), ("actor", "web-source-actor")):
            request = f"private/a1-{name}-origin-request.json"
            issue = f"private/a1-{name}-origin-issue.json"
            self._write(self.inputs / request, {"entry_id": entry})
            self._write(self.inputs / issue, {
                "action": "issue",
                "receipt": {
                    "assertion_ref": self.refs[name],
                    "expires_at": self._utc(self.expiry),
                    "mode": "service_https",
                },
            })
            self.index["files"].update({request: "sealed", issue: "sealed"})
        (self.inputs / "private/gateway.env").write_text(
            "TS_GATEWAY_ORIGIN=" + self.refs["config"] + "\n", encoding="utf-8"
        )
        for assertion_id, name in (
            ("model_revoked", "config"),
            ("forgotten", "actor"),
            ("source_revoked", "actor"),
            ("unknown_no_resend", "actor"),
        ):
            request = {"query": {"origin": {"assertion_ref": self.refs[name]}}}
            if assertion_id == "unknown_no_resend":
                request["deadline_at"] = self._utc(self.deadline)
            self.index["assertions"].append({"id": assertion_id, "request_json": request})
        self.db_path = self.source / "data/platform/platform.sqlite"
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("CREATE TABLE origins (ref TEXT PRIMARY KEY, entry_id TEXT, "
                       "entry_digest TEXT, expires_at REAL, revoked INTEGER)")
            db.execute("CREATE TABLE revoked_entries (id TEXT PRIMARY KEY)")
            db.execute("CREATE TABLE revoked_principals (id TEXT PRIMARY KEY)")
            db.executemany("INSERT INTO origins VALUES (?,?,?,?,0)", [
                (self.refs["config"], "config-entry", self._entry_digest("config-entry"), self.expiry),
                (self.refs["actor"], "web-source-actor", self._entry_digest("web-source-actor"), self.expiry),
            ])
            db.commit()

    def _entry_digest(self, entry_id):
        return hashlib.sha256(json.dumps(
            self.settings["entries"][entry_id], ensure_ascii=False,
            sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest()

    @staticmethod
    def _utc(seconds):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _write(path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def check(self, *, now=None, minimum=180):
        return check_origin_admission(
            self.source, self.inputs, self.index,
            now=self.now if now is None else now,
            minimum_remaining=minimum,
        )

    def test_fixed_product_receipts_source_rows_and_exact_boundary(self):
        self.assertEqual(self.check()["remaining_at_check_seconds"], 220)
        self.assertEqual(self.check(now=self.now + 40)["remaining_at_check_seconds"], 180)
        with self.assertRaisesRegex(RecoveryError, "drill_origin_lifetime_insufficient") as short:
            self.check(now=self.now + 40.001)
        self.assertEqual(short.exception.remaining, 179)
        self.assertTrue(short.exception.checked_at.endswith("Z"))
        self.assertEqual(self.check(now=self.now + 219, minimum=0)[
            "remaining_at_check_seconds"], 1)
        with self.assertRaisesRegex(RecoveryError, "drill_origin_lifetime_insufficient"):
            self.check(now=self.deadline, minimum=0)

    def test_shorter_config_or_actor_expiry_controls_admission(self):
        for name in ("config", "actor"):
            with self.subTest(name=name):
                issue_file = self.inputs / f"private/a1-{name}-origin-issue.json"
                original = json.loads(issue_file.read_text())
                receipt = json.loads(issue_file.read_text())
                receipt["receipt"]["expires_at"] = self._utc(self.now + 179)
                self._write(issue_file, receipt)
                with closing(sqlite3.connect(self.db_path)) as db:
                    db.execute("UPDATE origins SET expires_at=? WHERE ref=?",
                               (self.now + 179, self.refs[name]))
                    db.commit()
                with self.assertRaisesRegex(RecoveryError, "drill_origin_lifetime_insufficient"):
                    self.check()
                self._write(issue_file, original)
                with closing(sqlite3.connect(self.db_path)) as db:
                    db.execute("UPDATE origins SET expires_at=? WHERE ref=?",
                               (self.expiry, self.refs[name]))
                    db.commit()

    def test_current_source_expiry_accepts_normal_renewal_but_rejects_rollback(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("UPDATE origins SET expires_at=? WHERE ref=?",
                       (self.now + 290, self.refs["config"]))
            db.commit()
        # The sealed issue receipt remains at +240; the stopped source row is
        # renewed and is the current product authority for the same ref.
        self.assertEqual(self.check()["remaining_at_check_seconds"], 220)
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("UPDATE origins SET expires_at=? WHERE ref=?",
                       (self.now + 239, self.refs["config"]))
            db.commit()
        with self.assertRaisesRegex(RecoveryError, "drill_origin_source_mismatch"):
            self.check()

    def test_source_actual_expiry_entry_and_product_ttl_are_enforced(self):
        self.index["assertions"][-1]["request_json"]["deadline_at"] = self._utc(
            self.now + 290
        )
        with self.assertRaisesRegex(RecoveryError, "drill_origin_lifetime_insufficient"):
            self.check(now=self.now + 241)
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("UPDATE origins SET entry_id=? WHERE ref=?",
                       ("wrong-entry", self.refs["config"]))
            db.commit()
        with self.assertRaisesRegex(RecoveryError, "drill_origin_source_mismatch"):
            self.check()
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("UPDATE origins SET entry_id=?, expires_at=? WHERE ref=?",
                       ("config-entry", self.now + 301, self.refs["config"]))
            db.commit()
        with self.assertRaisesRegex(RecoveryError, "drill_origin_source_mismatch"):
            self.check()

    def test_fully_rebound_but_unknown_reference_and_digest_fail(self):
        config_issue = self.inputs / "private/a1-config-origin-issue.json"
        original = json.loads(config_issue.read_text())
        unknown = "origin:" + "c" * 32
        changed = json.loads(config_issue.read_text())
        changed["receipt"]["assertion_ref"] = unknown
        self._write(config_issue, changed)
        (self.inputs / "private/gateway.env").write_text(
            "TS_GATEWAY_ORIGIN=" + unknown + "\n", encoding="utf-8"
        )
        self.index["assertions"][0]["request_json"]["query"]["origin"]["assertion_ref"] = unknown
        with self.assertRaisesRegex(RecoveryError, "drill_origin_source_mismatch"):
            self.check()
        self._write(config_issue, original)
        (self.inputs / "private/gateway.env").write_text(
            "TS_GATEWAY_ORIGIN=" + self.refs["config"] + "\n", encoding="utf-8"
        )
        self.index["assertions"][0]["request_json"]["query"]["origin"]["assertion_ref"] = self.refs["config"]
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("UPDATE origins SET entry_digest=? WHERE ref=?",
                       ("0" * 64, self.refs["config"]))
            db.commit()
        with self.assertRaisesRegex(RecoveryError, "drill_origin_source_mismatch"):
            self.check()

    def test_mismatched_refs_env_and_source_ledger_are_rejected(self):
        config_issue = self.inputs / "private/a1-config-origin-issue.json"
        original = json.loads(config_issue.read_text())
        changed = json.loads(config_issue.read_text())
        changed["receipt"]["assertion_ref"] = "origin:" + "c" * 32
        self._write(config_issue, changed)
        with self.assertRaisesRegex(RecoveryError, "drill_origin_binding_invalid"):
            self.check()
        self._write(config_issue, original)
        changed = json.loads(config_issue.read_text())
        changed["receipt"]["expires_at"] = self._utc(self.expiry + 1)
        self._write(config_issue, changed)
        with self.assertRaisesRegex(RecoveryError, "drill_origin_source_mismatch"):
            self.check()
        self._write(config_issue, original)
        self.index["assertions"][0]["request_json"]["query"]["origin"]["assertion_ref"] = self.refs["actor"]
        with self.assertRaisesRegex(RecoveryError, "drill_origin_binding_invalid"):
            self.check()
        self.index["assertions"][0]["request_json"]["query"]["origin"]["assertion_ref"] = self.refs["config"]
        (self.inputs / "private/gateway.env").write_text(
            "TS_GATEWAY_ORIGIN=" + self.refs["actor"] + "\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(RecoveryError, "drill_origin_binding_invalid"):
            self.check()
        (self.inputs / "private/gateway.env").write_text(
            "TS_GATEWAY_ORIGIN=" + self.refs["config"] + "\n", encoding="utf-8"
        )
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("UPDATE origins SET revoked=1 WHERE ref=?", (self.refs["actor"],))
            db.commit()
        with self.assertRaisesRegex(RecoveryError, "drill_origin_source_mismatch"):
            self.check()

    def test_receipt_shape_dates_deadline_and_sealed_files_fail_closed(self):
        actor_issue = self.inputs / "private/a1-actor-origin-issue.json"
        original = json.loads(actor_issue.read_text())
        for expiry in (None, "2027-02-30T00:00:00Z", "2099-01-01T00:00:00+00:00"):
            changed = json.loads(actor_issue.read_text())
            changed["receipt"]["expires_at"] = expiry
            self._write(actor_issue, changed)
            with self.subTest(expiry=expiry), self.assertRaisesRegex(
                RecoveryError, "drill_origin_expiry_invalid"
            ):
                self.check()
            self._write(actor_issue, original)
        self.index["assertions"][-1]["request_json"]["deadline_at"] = "invalid"
        with self.assertRaisesRegex(RecoveryError, "drill_origin_expiry_invalid"):
            self.check()
        self.index["assertions"][-1]["request_json"]["deadline_at"] = self._utc(self.deadline)
        del self.index["files"]["private/a1-actor-origin-issue.json"]
        with self.assertRaisesRegex(RecoveryError, "drill_origin_receipt_missing"):
            self.check()

    def test_nonempty_wal_refused_without_mutation(self):
        wal = self.source / "data/platform/platform.sqlite-wal"
        wal.write_bytes(b"synthetic-unmerged")
        with self.assertRaisesRegex(RecoveryError, "drill_origin_source_wal_unreadable"):
            self.check()
        self.assertEqual(wal.read_bytes(), b"synthetic-unmerged")


if __name__ == "__main__":
    unittest.main()
