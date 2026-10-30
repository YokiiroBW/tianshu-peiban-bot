import copy
import unittest
import tempfile
from pathlib import Path

from alerts import AlertStore
from configs import alert_rules, dashboard, project_existing
from retention import apply_retention


class SimpleProvisioningTests(unittest.TestCase):
    def test_retention_removes_only_old_count_settings(self):
        guard = {"ledger_max_events": 10000, "log_max_events": 10000,
                 "keep": {"unchanged": True}}
        loki = {"limits_config": {"existing": "keep"}}
        result, storage = apply_retention(guard, loki, 7)
        self.assertNotIn("ledger_max_events", result)
        self.assertNotIn("log_max_events", result)
        self.assertEqual(result["keep"], guard["keep"])
        self.assertEqual(storage["limits_config"]["retention_period"], "168h")
        self.assertEqual(storage["limits_config"]["existing"], "keep")

    def test_old_rules_and_panels_retire_without_overwriting_custom_entries(self):
        alert = alert_rules()
        group = alert["groups"][0]
        group["name"] = "TianShu log integrity"
        group["rules"].append({"uid": "obs-pendingdelivery", "title": "obsolete"})
        group["rules"].append({"uid": "custom-rule", "title": "keep"})
        board = dashboard()
        board["title"] = "TianShu runtime and log integrity"
        board["panels"][0]["targets"][0]["expr"] = "tianshu_obs_pending_events"
        board["panels"].append({"id": 88, "title": "custom"})
        files = {"observability/provisioning/alerting/test.yaml": alert,
                 "observability/dashboards/test.json": board}
        project_existing(files)
        rules = files["observability/provisioning/alerting/test.yaml"]["groups"][0]["rules"]
        self.assertEqual(group["name"], "TianShu log integrity")
        self.assertNotIn("obs-pendingdelivery", [r["uid"] for r in rules])
        self.assertIn({"uid": "custom-rule", "title": "keep"}, rules)
        self.assertIn({"uid": "obs-pendingdelivery", "orgId": 1}, alert["deleteRules"])
        self.assertIn({"id": 88, "title": "custom"}, board["panels"])
        self.assertEqual(board["panels"][0]["title"], "Vector buffer bytes")
        once = copy.deepcopy(files)
        project_existing(files)
        self.assertEqual(files, once)

    def test_retired_alerts_are_removed_without_touching_other_current_state(self):
        with tempfile.TemporaryDirectory() as root:
            store = AlertStore(Path(root) / "alerts.sqlite")
            try:
                with store.db:
                    store.db.execute("INSERT INTO current VALUES ('delivery_backlog', 1, 1)")
                    store.db.execute("INSERT INTO current VALUES ('custom_state', 1, 1)")
                store.update({"monitor_success": 1})
                names = {r[0] for r in store.db.execute("SELECT name FROM current")}
                self.assertNotIn("delivery_backlog", names)
                self.assertIn("custom_state", names)
            finally:
                store.close()
