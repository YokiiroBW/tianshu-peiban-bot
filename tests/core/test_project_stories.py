"""A real PG project story: reviewed files, typed evidence, revocation and scopes.

Uses only the explicitly disposable database/core fixtures. No model, provider,
watcher, external account or old product fixture participates in this story.
"""

import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tianshu.capabilities.module import build_module
from tianshu.capabilities.projects.types import ReadUnitRef
from tianshu.runtime.config import Secrets
from tianshu.runtime.credentials import CredentialStore
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import migrate
from tianshu.runtime.serialization import wire
from tianshu.types import Action, MediaRef, OriginKind, ServiceClass, SourceRef


pytestmark = pytest.mark.asyncio


async def grant_actor(core, principal_id, permissions, intent):
    # Identity's management command owns the write; obtain its actual CAS head in
    # the same way as ordinary_users, without inserting privileged fixture rows.
    async with core.database.transaction(read_only=True) as conn:
        actor = await (
            await conn.execute(
                "SELECT access_epoch FROM identity.actors WHERE actor_id=%s",
                (core.actor,),
            )
        ).fetchone()
    await core.management.execute(
        core.owner,
        "actors.grant",
        {
            "principal_id": principal_id,
            "actor_id": core.actor,
            "permissions": permissions,
            "expected_revision": actor["access_epoch"],
            "client_request_id": intent,
        },
    )


async def project_permissions(core, original, *, configure):
    directory = await core.management.directory(core.owner)
    principal = next(
        row
        for row in directory["principals"]
        if row["principal_id"] == original.principal_id
    )
    permissions = set(principal["permissions"]) | {"projects.read", "projects.write"}
    if configure:
        permissions.add("config.publish")
    else:
        permissions.discard("config.publish")
    permissions = sorted(permissions)
    phase = "configure" if configure else "ordinary"
    await core.management.execute(
        core.owner,
        "principals.save",
        {
            "principal_id": principal["principal_id"],
            "display_name": principal["display_name"],
            "permissions": permissions,
            "expected_revision": principal["revision"],
            "client_request_id": phase + ":" + principal["principal_id"],
        },
    )
    await grant_actor(
        core,
        original.principal_id,
        permissions,
        "actor-" + phase + ":" + original.principal_id,
    )
    return await core.identity.context(
        original.principal_id,
        actor_id=core.actor,
        conversation_id=original.conversation_id,
    )


async def execute(caps, context, operation, body, intent, *, source_refs=()):
    action = Action(
        action_id=str(uuid4()),
        intent_key=intent,
        capability_id="projects." + operation.replace("/", "."),
        actor_id=context.actor_id,
        context=context,
        arguments=body,
        origin_kind=OriginKind.HUMAN,
        source_refs=tuple(source_refs),
        deadline_at=datetime.now(UTC) + timedelta(minutes=2),
    )
    # Exercise the actual worker entry and its Action/Attempt/Receipt ledger.
    # This does not start an unrelated Procrastinate or external-provider worker.
    receipt = await caps.actions.invoke(
        action, background=False, service_class=ServiceClass.ELASTIC
    )
    assert receipt.action_id == action.action_id
    assert receipt.attempt_id is not None
    return receipt


async def successful(caps, context, operation, body, intent, *, source_refs=()):
    receipt = await execute(
        caps, context, operation, body, intent, source_refs=source_refs
    )
    assert receipt.execution == "succeeded", receipt.error_code
    assert receipt.error_code is None
    saved = await caps.actions.get(receipt.action_id, context)
    assert saved["receipt"]["receipt_id"] == receipt.receipt_id
    assert saved["attempt"]["attempt_id"] == receipt.attempt_id
    return receipt


async def read(caps, context, operation, body):
    return wire(await caps.projects.read(operation, context, body))


async def test_reviewed_project_evidence_revokes_without_crossing_private_projects(
    core, ordinary_users, tmp_path
):
    alice_original, bob_original = ordinary_users
    managed = tmp_path / "managed-projects"
    payload = (
        "# Release observation\n"
        "The registered widget uses a blue status indicator.\n"
        "A green indicator remains an unverified alternative.\n"
    ).encode("utf-8")
    for name in ("alice", "bobby"):
        docs = managed / name / "docs"
        docs.mkdir(parents=True)
        (docs / "release.md").write_bytes(payload)

    # The official module supplies both native migrations and the actual Projects
    # registration. Only local Projects is exercised; prepare() would additionally
    # open unrelated media/plugin owners and is deliberately unnecessary here.
    runtime = SimpleNamespace(
        role="worker",
        database=core.database,
        files=core.files,
        jobs=core.memory.jobs,
        models=core.models,
        credentials=CredentialStore(
            core.database,
            Secrets(
                {"TIANSHU_MASTER_KEY": base64.urlsafe_b64encode(bytes(range(32))).decode()}
            ),
        ),
    )
    module, caps = build_module(
        runtime,
        settings={"projects": {"allowed_roots": [str(managed)]}},
        authority=core.identity,
        sources=core.sources,
        life=None,
        expression=None,
        authorize_actor=core.identity.authorize_actor,
        authorize_conversation=core.identity.authorize_conversation,
    )
    try:
        await migrate(core.database, module.migrations)
        core.sources.register_dependant(caps.withdraw_sources)

        # Registration is an explicit configuration privilege. Subsequent imports,
        # research and revocation use fresh ordinary-user contexts without it.
        configured = []
        for name, original in (("alice", alice_original), ("bobby", bob_original)):
            context = await project_permissions(core, original, configure=True)
            registered = await successful(
                caps, context, "registry/save",
                {
                    "project_id": name + "-project",
                    "label": name + " private project",
                    "root": str(managed / name),
                    "directories": ["docs"],
                    "expected_revision": 0,
                },
                "register:" + name,
            )
            assert registered.result["conversation_id"] == original.conversation_id
            assert registered.result["scope_ids"] == list(context.scope_ids)
            configured.append(original)
        alice, bob = [
            await project_permissions(core, context, configure=False)
            for context in configured
        ]
        assert alice.actor_id == bob.actor_id == core.actor
        assert alice.conversation_id != bob.conversation_id
        assert "config.publish" not in alice.permissions | bob.permissions
        assert "*" not in alice.permissions | bob.permissions
        assert set(alice.scope_ids) != set(bob.scope_ids)

        originals = {}
        documents = {}
        units = {}
        for name, context in (("alice", alice), ("bobby", bob)):
            project_id = name + "-project"
            preview = await successful(
                caps, context, "directories/preview",
                {"project_id": project_id, "directory": "docs"},
                "preview:" + name,
            )
            plan = preview.result
            assert plan["imported"] is False
            assert plan["coverage"]["complete"] is True
            assert [row["locator"] for row in plan["items"]] == ["docs/release.md"]
            assert (await read(caps, context, "documents", {"project_id": project_id}))["items"] == []

            imported = await successful(
                caps, context, "directories/import",
                {
                    "project_id": project_id,
                    "plan_id": plan["plan_id"],
                    "plan_hash": plan["sha256"],
                    "files": ["docs/release.md"],
                    "tombstones": [],
                },
                "import:" + name,
            )
            assert imported.result["state"] == "completed"
            listing = await read(caps, context, "documents", {"project_id": project_id})
            assert len(listing["items"]) == 1
            document = listing["items"][0]
            assert document["source_validation"] == "not_checked"
            documents[name] = document
            original = await read(
                caps, context, "documents/original",
                {"project_id": project_id, "document_id": document["document_id"]},
            )
            assert original["freshness"]["state"] == "current"
            media = MediaRef(**original["media"])
            assert media.media_id.startswith("source:")
            async with core.sources.open_media(context, media) as stream:
                assert stream.read() == payload
            originals[name] = original

            obtained = await read(
                caps, context, "documents/read",
                {"project_id": project_id, "document_id": document["document_id"]},
            )
            assert obtained["text"] == payload.decode("utf-8")
            unit = ReadUnitRef(**obtained["reference"])
            assert unit.document_id == document["document_id"]
            assert unit.version == document["version"]
            assert len(unit.sha256) == 64
            units[name] = obtained
            checked = await read(
                caps, context, "check",
                {"project_id": project_id, "reference": wire(unit)},
            )
            assert checked["source_refs"]

        # Identical underlying bytes never confer project or original-source access.
        assert (
            originals["alice"]["media"]["sha256"]
            == originals["bobby"]["media"]["sha256"]
        )
        assert originals["alice"]["source_ref"] != originals["bobby"]["source_ref"]
        for context, own_project, other_project, other_name in (
            (alice, "alice-project", "bobby-project", "bobby"),
            (bob, "bobby-project", "alice-project", "alice"),
        ):
            visible = await read(caps, context, "view", {})
            assert [row["project_id"] for row in visible["items"]] == [own_project]
            for operation, selection in (
                ("registry/get", {}),
                ("documents/read", {"document_id": documents[other_name]["document_id"]}),
            ):
                with pytest.raises(AppError) as denied:
                    await read(
                        caps, context, operation,
                        {"project_id": other_project, **selection},
                    )
                assert denied.value.status == 404
        with pytest.raises(AppError) as denied:
            await core.sources.read(bob, SourceRef(**originals["alice"]["source_ref"]), {})
        assert denied.value.status == 403

        citation = units["alice"]["reference"]
        note = {
            "question": "Which status indicator is actually supported by the document?",
            "source_statements": [
                {
                    "kind": "quote",
                    "statement": "The registered widget uses a blue status indicator.",
                    "source": citation,
                }
            ],
            "inferences": ["Blue is the supported current choice; green has not been verified."],
            "open_questions": ["Has the green alternative been tested?"],
            "decision": {
                "summary": "Keep blue for this revision.",
                "basis": [{"kind": "unit", "reference": citation}],
            },
        }
        saved = await successful(
            caps, alice, "notes/save",
            {"project_id": "alice-project", "note": note, "expected_revision": 0},
            "evidence-note",
            source_refs=tuple(SourceRef(**ref) for ref in units["alice"]["source_refs"]),
        )
        note_id = saved.result["note_id"]
        current_note = await read(
            caps, alice, "notes/get", {"project_id": "alice-project", "note_id": note_id}
        )
        assert current_note["current"] is True
        assert current_note["payload"] == note
        assert {"kind": "unit", "reference": citation} in current_note["citations"]
        assert current_note["source_refs"]

        smuggled = await execute(
            caps, bob, "notes/save",
            {"project_id": "bobby-project", "note": note, "expected_revision": 0},
            "foreign-project-citation",
        )
        assert smuggled.execution == "failed"
        assert smuggled.error_code == "document_unavailable"
        assert (await read(caps, bob, "notes", {"project_id": "bobby-project"}))["items"] == []

        created = await core.management.execute(
            core.owner,
            "principals.save",
            {
                "username": "charlie",
                "display_name": "charlie",
                "password": "isolated-password",
                "permissions": ["companion.read"],
                "expected_revision": 0,
                "client_request_id": "create-project-outsider",
            },
        )
        await grant_actor(
            core,
            created.data["principal_id"],
            ["companion.read"],
            "grant-project-outsider",
        )
        outsider = await core.identity.context(created.data["principal_id"], actor_id=core.actor)
        with pytest.raises(AppError) as denied:
            await read(caps, outsider, "registry/get", {"project_id": "alice-project"})
        assert denied.value.status == 403

        # Withdraw through Sources, not by altering project rows. Its callback must
        # clear dependent private bodies and evidence in the very same PG commit.
        source = SourceRef(**originals["alice"]["source_ref"])
        withdrawn = await core.sources.invalidate(
            alice,
            {
                "source_id": source.source_id,
                "expected_revision": source.revision,
                "client_request_id": "withdraw-project-original",
            },
            forgotten=True,
        )
        assert withdrawn.data["state"] == "forgotten"
        for operation, body in (
            ("documents/original", {"document_id": documents["alice"]["document_id"]}),
            ("documents/read", {"document_id": documents["alice"]["document_id"]}),
            ("check", {"reference": citation}),
        ):
            with pytest.raises(AppError):
                await read(caps, alice, operation, {"project_id": "alice-project", **body})
        with pytest.raises(AppError):
            await core.sources.read(alice, source, {})

        invalid_note = await read(
            caps, alice, "notes/get", {"project_id": "alice-project", "note_id": note_id}
        )
        assert invalid_note["current"] is False
        assert invalid_note["payload"] == {}
        assert invalid_note["source_refs"] == []
        stale_write = await execute(
            caps, alice, "notes/save",
            {"project_id": "alice-project", "note": note, "expected_revision": 0},
            "do-not-relearn-withdrawn-reference",
        )
        assert stale_write.execution == "failed"
        assert stale_write.error_code == "document_unavailable"

        async with core.database.transaction(read_only=True) as conn:
            stored = await (
                await conn.execute(
                    "SELECT state,payload,citations,source_refs FROM capability_projects.note_versions "
                    "WHERE note_id=%s AND version=%s",
                    (note_id, saved.result["version"]),
                )
            ).fetchone()
            assert stored == {
                "state": "source_revoked",
                "payload": {},
                "citations": [],
                "source_refs": [],
            }

        still_current = await read(
            caps, bob, "documents/read",
            {"project_id": "bobby-project", "document_id": documents["bobby"]["document_id"]},
        )
        assert still_current["text"] == payload.decode("utf-8")
        unaffected = await read(
            caps,
            bob,
            "check",
            {"project_id": "bobby-project", "reference": units["bobby"]["reference"]},
        )
        assert unaffected["source_refs"]
    finally:
        await caps.close()
