"""Native synthetic write setup shared by the source-write and SQLite controls.

Only benchmark request construction lives here. Product Store, workflows,
SourceAuthority and contracts retain all validation and transaction ownership.
"""

import copy
import hashlib
import json
from datetime import UTC, datetime

from common import memory_features, prepare_memory_schema, settings
from memory_fixture import Harness
from memory_source_sync import SyntheticOwners
from memory_sql import signature
from sync_fixture import SyncHarness
from tianshu_memory.contracts import Contracts
from tianshu_memory.service import MemoryService
from tianshu_memory.source_authority import SourceAuthority
from tianshu_memory.sources import LocalFixtureSources
from tianshu_memory.store import Store
from tianshu_memory.workflow import LocalWorkflow, TrustedWorkflow
from tianshu_storage import StorageOptions

CASES = ("local_commit", "local_full_flow", "source_commit", "source_full_flow")
TABLES = (
    "groups",
    "records",
    "jobs",
    "write_ledger",
    "source_writes",
    "inbox",
    "outbox",
    "sources",
    "source_admissions",
)


def contract_files(stage):
    contracts = Contracts(stage / "contracts/text-dialogue/v1")
    contracts.load_sources()
    return contracts


def require_sqlite_settings(config):
    options = StorageOptions.from_settings(config)
    assert not options.postgresql_url and not options.redis_url
    assert options.namespace == "writecontrol" and options.pool_max_size == 4


class ExistingLocal(Harness):
    def __init__(self, state, contracts, store):
        self.__dict__.update(copy.deepcopy(state["harness"]))
        self.contracts, self.store = contracts, store
        self.examples = {
            row["id"]: row["document"]
            for row in json.loads(
                (contracts.directory / "examples/documents.json").read_bytes()
            )
        }


class ExistingSource(SyncHarness):
    def __init__(self, state, contracts, store):
        self.__dict__.update(copy.deepcopy(state["harness"]))
        self.contracts, self.store = contracts, store


class WriteFixture:
    def __init__(self, harness, contracts, kind, *, sequence=0, template=None):
        self.h, self.contracts, self.kind = harness, contracts, kind
        self.sequence, self.template = sequence, template
        self.owners = SyntheticOwners(harness, contracts) if kind == "source" else None
        authority = (
            SourceAuthority(self.owners, contracts)
            if self.owners
            else LocalFixtureSources()
        )
        self.service = MemoryService(
            harness.store,
            contracts,
            source_authority=authority,
            clock=lambda: datetime(
                2026, 9, 14, 1, 4 if kind == "source" else 0, tzinfo=UTC
            ),
        )
        self.h.service = self.service
        self.workflow = (
            TrustedWorkflow(self.service)
            if self.owners
            else LocalWorkflow(self.service)
        )

    @property
    def store(self):
        return self.service.store

    @property
    def scope(self):
        return self.h.scope() if self.owners else self.h.private

    @property
    def context(self):
        return (
            self.h.contexts["synthetic-viewer:0"]
            if self.owners
            else self.h.config["origins"]["origin-private"]
        )

    def selection(self, query, *, budget=100000):
        request = self.h.selection(budget=budget)
        request.update(query_text=query, selection=["evidence"])
        return self.service.select(request, self.context)

    def prepare(self, indexes):
        """Same source -> zero-budget scope -> consume boundary as memory_sql."""
        self.sequence += 1
        sequence = self.sequence
        if self.owners:
            physical, admission, grant, turn = map(copy.deepcopy, self.template)
            key = dict(physical["key"], message_id=f"write-source:{sequence}")
            physical.update(
                key=key,
                physical_receipt_id=f"write-physical:{sequence}",
                content_digest=hashlib.sha256(str(sequence).encode()).hexdigest(),
            )
            source = dict(
                admission["source"],
                message_key=dict(key, revision=1),
                receipt_id=f"write-admission:{sequence}",
            )
            scope = copy.deepcopy(self.template[1]["scope"])
            admission.update(
                selector=dict(admission["selector"], key=key),
                scope=scope,
                source=source,
                physical_receipt_id=physical["physical_receipt_id"],
            )
            grant.update(
                selector=admission["selector"],
                scope=scope,
                entry_id=f"write-entry:{sequence}",
            )
            event = turn["committed_event"]
            event.update(
                event_id=f"write-event:{sequence}",
                aggregate_id=f"write-turn:{sequence}",
                sources=[source],
                scope=scope,
                turn_sequence=sequence,
            )
            turn.update(
                turn_id=event["aggregate_id"], scope=scope, input_sources=[source]
            )
            self.h.physicals.append(physical)
            self.h.admissions.append(admission)
            self.h.grants.append(grant)
            self.h.turns.append(turn)
            self.h.core_head["sequence"] += 1
            self.h.platform_head["sequence"] += 1
        else:
            source = self.h.source(message=f"write-source:{sequence}")
            scope = self.scope
            self.workflow.observe_source(source, scope)
            event = self.h.event(
                [source],
                turn=f"write-turn:{sequence}",
                event_id=f"write-event:{sequence}",
            )
        event["scope_version"] = self.selection("field:unused", budget=0)[
            "scope_version"
        ]
        job = self.service.consume(
            event, {"authenticated_service": "companion", "allowed_scopes": [scope]}
        )["candidate_job_ref"]
        drafts = []
        for index in indexes:
            if self.owners:
                draft = copy.deepcopy(
                    self.h.examples["memory/candidate-commit"]["drafts"][0]
                )
                draft.update(
                    scope=scope, category="evidence", field_key=f"bench-{index}"
                )
                draft["units"][0].update(
                    statement=f"synthetic fact {index} "
                    + ("Python" if index < 300 and index % 10 == 0 else "garden"),
                    conditions=[],
                    negations=[],
                    sources=[source],
                )
            else:
                draft = self.h.draft(
                    field_key=f"bench-{index}",
                    units=[
                        self.h.unit(
                            source=source,
                            statement=f"合成主题 {index} "
                            + (
                                "Python"
                                if index < 300 and index % 10 == 0
                                else "garden"
                            )
                            + " fixed fact",
                            conditions=[],
                            negations=[],
                        )
                    ],
                )
            drafts.append(draft)
        return {"job_id": job, "drafts": drafts}

    def commit(self, packet):
        if self.owners:
            return self.workflow.commit_candidate(packet)
        return self.workflow.commit_candidate(packet["job_id"], packet["drafts"])

    def full_flow(self, index):
        packet = self.prepare([index])
        return packet, self.commit(packet)

    def state(self):
        names = (
            (
                "account",
                "person",
                "counter",
                "contexts",
                "physicals",
                "admissions",
                "grants",
                "turns",
                "core_head",
                "platform_head",
                "examples",
            )
            if self.owners
            else (
                "account",
                "person",
                "counter",
                "channel",
                "private",
                "group",
                "config",
            )
        )
        return {
            "kind": self.kind,
            "sequence": self.sequence,
            "template": copy.deepcopy(self.template),
            "harness": {name: copy.deepcopy(getattr(self.h, name)) for name in names},
        }

    def counts(self):
        with self.store.transaction() as db:
            result = {
                name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in TABLES
            }
            metadata = dict(db.execute("SELECT key,value FROM metadata"))
        return {**result, "source_revision": int(metadata["source_revision"])}

    def assert_committed(self, packet, result, before):
        assert result["state"] == "committed"
        assert len(result["record_ids"]) == len(result["group_ids"]) == 1
        after = self.counts()
        assert after["groups"] == before["groups"] + 1
        assert after["records"] == before["records"] + 1
        assert after["write_ledger"] == before["write_ledger"] + 1
        assert after["source_revision"] > before["source_revision"]
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT result FROM write_ledger WHERE job_id=?", (packet["job_id"],)
            ).fetchone()
            assert row is not None and json.loads(row[0]) == result
            assert (
                db.execute(
                    "SELECT state FROM jobs WHERE id=?", (packet["job_id"],)
                ).fetchone()[0]
                == "committed"
            )
            if self.store.backend == "sqlite":
                assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
                assert db.execute("PRAGMA foreign_key_check").fetchone() is None
        return after

    def content_signature(self, packet):
        value = self.selection("field:" + packet["drafts"][0]["field_key"])
        assert len(value["dependency_groups"]) == len(value["selected_units"]) == 1
        assert (
            value["selected_units"][0]["statement"]
            == packet["drafts"][0]["units"][0]["statement"]
        )
        return signature(value)


def open_fixture(state, contracts, config):
    store = Store.from_config(config)
    try:
        harness = (
            ExistingSource(state, contracts, store)
            if state["kind"] == "source"
            else ExistingLocal(state, contracts, store)
        )
        return WriteFixture(
            harness,
            contracts,
            state["kind"],
            sequence=state["sequence"],
            template=state["template"],
        )
    except BaseException:
        store.close()
        raise


def seed_fixture(directory, contracts, kind, *, size=300):
    """Public Harness initialization/migrations/registration, never handwritten rows."""
    require_sqlite_settings(settings({}, "writecontrol", "sqlite"))
    if kind == "source":
        stage = contracts.directory.parents[2]
        rows = json.loads(
            (stage / "contracts/source-sync/v1/examples/documents.json").read_bytes()
        )
        examples = {row["id"]: row["document"] for row in rows}
        harness = SyncHarness(
            directory, contracts, examples, directory / "unused-synthetic-certificates"
        )
        prepare_memory_schema(harness.store, directory)
        fixture = WriteFixture(harness, contracts, kind)
        registered = fixture.service.register(
            {"command": harness.command(first=True), "account": harness.account},
            harness.contexts["synthetic-first"],
        )
        harness.bind_person(registered["person_id"])
        fixture.template = tuple(
            copy.deepcopy(items[0])
            for items in (
                harness.physicals,
                harness.admissions,
                harness.grants,
                harness.turns,
            )
        )
        harness.physicals, harness.admissions, harness.grants, harness.turns = (
            [],
            [],
            [],
            [],
        )
    else:
        harness = Harness(directory, contracts)
        harness.store.migrate_profiles(directory / "profiles.backup")
        harness.store.migrate_sources(directory / "sources.backup", contracts)
        prepare_memory_schema(harness.store, directory)
        fixture = WriteFixture(harness, contracts, kind)
    for offset in range(0, size, 10):
        result = fixture.commit(fixture.prepare(range(offset, min(size, offset + 10))))
        assert result["state"] == "committed"
    assert fixture.counts()["groups"] == size
    memory_features(fixture.store)
    return fixture
