"""Extra CONNECT-A checks for the fixed Memory browser candidate.

Run against a read-only archive of CONNECT-M commit 0dd542a by setting
CONNECT_M_SNAPSHOT to that archive root. The imported fixture keeps every
database, TLS certificate, and owner response inside that snapshot's .runtime.
"""

import os
import sys
from pathlib import Path

import pytest

snapshot = Path(os.environ.get("CONNECT_M_SNAPSHOT", ""))
if not (snapshot / "tests/test_browser_catalog.py").is_file():
    pytest.skip(
        "CONNECT_M_SNAPSHOT must point at the fixed 0dd542a test archive",
        allow_module_level=True,
    )

sys.path.insert(0, str(snapshot / "src"))
sys.path.insert(0, str(snapshot / "tests"))
pytest_plugins = ("conftest", "test_browser_catalog")

from test_browser_catalog import body, post  # noqa: E402


@pytest.mark.parametrize("change", ["disabled", "account", "actor", "scope"])
def test_reader_registration_change_after_owner_barrier_is_denied(catalog, change):
    """Fresh config is checked after owner facts, before any row is returned."""
    seeded, _, _ = catalog.seed()

    def during_access(kind, request, response):
        if kind != "current":
            return
        reader = catalog.config["browser_readers"]["platform"]
        if change == "disabled":
            catalog.config["browser_readers"].pop("platform")
        elif change == "account":
            reader["account"] = {"namespace": "web", "immutable_account_id": "other"}
        elif change == "actor":
            reader["actor_id"] = "actor:b"
        else:
            reader["scopes"] = [catalog.scope(1)]
        catalog.save()

    catalog.mutate = during_access
    response = post(catalog, "records", body(catalog))
    assert response.status_code == 403, response.text
    assert "items" not in response.json()
    assert "咖啡" not in response.text

    # The source authority has its own durable effect before the post-barrier reader check.
    # A later authorization refusal cannot roll a committed source negative back.
    if change == "disabled":
        with catalog.store.transaction() as db:
            group = db.execute(
                "SELECT state FROM groups WHERE id=?", (seeded["group_ids"][0],)
            ).fetchone()
            assert group["state"] == "active"


def test_reader_disabled_and_source_withdrawn_keeps_negative_state(catalog):
    seeded, _, _ = catalog.seed()

    def during_access(kind, request, response):
        if kind != "current":
            return
        response["grants"][0]["state"] = "denied"
        catalog.grants[0]["state"] = "denied"
        catalog.platform_head["sequence"] += 1
        response["head"]["sequence"] = catalog.platform_head["sequence"]
        catalog.config["browser_readers"].pop("platform")
        catalog.save()

    catalog.mutate = during_access
    response = post(catalog, "records", body(catalog))
    assert response.status_code == 403, response.text
    assert "items" not in response.json()
    with catalog.store.transaction() as db:
        group = db.execute(
            "SELECT state FROM groups WHERE id=?", (seeded["group_ids"][0],)
        ).fetchone()
        assert group["state"] != "active"
