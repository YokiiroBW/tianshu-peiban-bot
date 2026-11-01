"""Native import intent, actual media authority and selection; disposable PG only."""

import base64
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import zipfile

from PIL import Image
from psycopg.errors import LockNotAvailable
import pytest

from tianshu.capabilities.api import invoke
from tianshu.capabilities.artifacts import Artifacts
from tianshu.capabilities.configuration import Configuration
from tianshu.capabilities.contracts import Fault
from tianshu.capabilities.dto import ReactionIntent
from tianshu.capabilities.external import ProviderError
from tianshu.capabilities.reactions.service import Reactions
from tianshu.capabilities.service import Operation
from tianshu.life.capabilities import ReactionSelectionHandler
from tianshu.models.types import ModelCompletion, ModelPart, ModelReceipt, ModelResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.serialization import wire
from tianshu.types import Action, Attempt, OriginKind, ServiceClass


pytestmark = pytest.mark.asyncio


def gif_bytes():
    output = BytesIO()
    first = Image.new("RGB", (4, 4), "blue")
    second = Image.new("RGB", (4, 4), "white")
    first.save(output, format="GIF", save_all=True, append_images=[second], duration=100, loop=0)
    return output.getvalue()


async def library(core):
    from tianshu.capabilities import artifacts as artifacts_module

    await migrate(core.database, (Migration(
        "capabilities-0001",
        Path(artifacts_module.__file__).with_name("schema.sql").read_text(encoding="utf-8"),
    ),))
    artifacts = Artifacts(core.database, core.files, core.identity, core.sources)
    reactions = Reactions(Configuration(core.database, None), artifacts, None, None, core.identity)
    owner = await core.identity.context(core.owner.principal_id, actor_id=core.actor)
    return owner, reactions, artifacts


async def imported_reaction(core):
    owner, reactions, artifacts = await library(core)
    pack = await reactions.import_pack(owner, {
        "id": "model-json", "name": "Synthetic reaction", "import_scope": "actor",
        "files": [{"filename": "happy.gif", "data_base64": base64.b64encode(gif_bytes()).decode()}],
    })
    asset = next(item for item in (await reactions.view(owner))["assets"] if item["pack_id"] == pack["id"])
    return owner, reactions, artifacts, asset


async def indexed_reaction(core):
    owner, reactions, artifacts, asset = await imported_reaction(core)
    vector = [1.0] + [0.0] * 1023
    async with core.database.transaction() as connection:
        await connection.execute(
            "INSERT INTO capabilities.reaction_embedding_profiles(id,dimensions,model) VALUES('model-json',1024,'synthetic')"
        )
        await connection.execute(
            "UPDATE capabilities.reaction_assets SET embedding=%s::vector,profile_id='model-json' WHERE id=%s",
            (str(vector), asset["id"]),
        )
    reactions.embed = AsyncMock(return_value=(vector, "model-json", None))
    reactions.broker = SimpleNamespace(generate=AsyncMock())
    intent = ReactionIntent(
        meaning="happy", purpose="reply", attitude="gentle", intensity=0.5,
        actor_id=core.actor, scope_ids=owner.scope_ids,
    )
    return owner, reactions, artifacts, asset, intent


def selection_result(text, *, completion="complete"):
    return ModelResult(
        (ModelPart("text", text=text),),
        ModelReceipt("select", "select", "succeeded", "submitted", "fixture", 1,
            "fixture", "fixture", "gemini.generate", "fixture"),
        completion=ModelCompletion(completion, ("STOP" if completion == "complete" else "MAX_TOKENS",)),
    )


async def import_http_arguments(reactions, context, body):
    """Use the same public adapter and registered Operation without starting a host."""
    owner = SimpleNamespace(reactions=reactions, prepared=True, authority=reactions.authority)

    async def operate(domain, operation, arguments, actual, _action, _attempt):
        assert (domain, operation) == ("reactions", "packs.import")
        return await reactions.import_pack(actual, arguments)

    owner.operate = operate
    operation = Operation(owner, "reactions", "packs.import", permission="config.publish")
    captured = []

    async def call(action, **_kwargs):
        captured.append(action)
        await reactions.authority.check(action.context)
        return await operation.execute(action, Attempt("import-attempt", action.action_id))

    service = SimpleNamespace(
        actions=SimpleNamespace(handler=AsyncMock(return_value=operation), invoke=call),
    )
    result = await invoke(service, "reactions", "packs.import", body, context)
    assert captured[0].context is context
    assert "actor_id" not in captured[0].arguments
    return result.data


async def test_actor_import_native_selection_uses_shared_original_in_another_chat(core, ordinary_users):
    _, bob = ordinary_users
    owner, reactions, artifacts = await library(core)
    raw = gif_bytes()
    uploaded = await artifacts.ingest(owner, raw, "image/gif", locator={"kind": "upload", "id": "gif"})
    pack = await import_http_arguments(reactions, owner, {
        "client_request_id": "native-import", "actor_id": core.actor,
        "id": "role-gifs", "name": "Shared GIFs", "expected_revision": 0,
        "import_scope": "actor", "files": [{"filename": "blue.gif", "media": wire(uploaded.media)}],
    })
    assert pack["actor_ids"] == [core.actor] and pack["import_scope"] == "actor"
    assert pack["asset_count"] == 1 and len(pack["scope_ids"]) == 1
    bot = replace(bob, origin_kind=OriginKind.ACTOR)
    await core.identity.check_context(bot)
    view = await reactions.view(bot)
    asset = next(value for value in view["assets"] if value["pack_id"] == pack["id"])
    assert asset["animated"] is True
    selected_original = await artifacts.reference(bot, asset["media"]["media_id"])
    assert selected_original.source_refs != uploaded.source_refs
    assert await artifacts.get(bot, asset["media"]["media_id"]) == (raw, "image/gif")
    with pytest.raises(Fault):
        await artifacts.reference(bot, uploaded.media.media_id)
    async with core.database.transaction(read_only=True) as conn:
        stored = await (await conn.execute(
            "SELECT scopes,audience FROM capabilities.artifacts WHERE media_id=%s",
            (asset["media"]["media_id"],),
        )).fetchone()
        source = await (await conn.execute(
            "SELECT scope_ids,audience,disclosure FROM sources.versions WHERE source_id=%s AND revision=%s",
            (selected_original.source_refs[0].source_id, selected_original.source_refs[0].revision),
        )).fetchone()
    assert stored["scopes"] == stored["audience"] == source["scope_ids"] == source["audience"] == pack["scope_ids"]
    assert source["disclosure"] is True

    vector = [1.0] + [0.0] * 1023
    async with core.database.transaction() as conn:
        await conn.execute(
            "INSERT INTO capabilities.reaction_embedding_profiles(id,dimensions,model) VALUES('fixture',1024,'synthetic')"
        )
        await conn.execute(
            "UPDATE capabilities.reaction_assets SET embedding=%s::vector,profile_id='fixture' WHERE id=%s",
            (str(vector), asset["id"]),
        )
    reactions.embed = AsyncMock(return_value=(vector, "fixture", None))
    reactions.broker = SimpleNamespace(generate=AsyncMock(return_value=ModelResult(
        (
            ModelPart("text", text='{"id":"reasoning-only"}', native={"thought": True}),
            ModelPart("tool_result", text='{"id":"tool-only"}', result={"id": "tool-only"}),
            ModelPart("text", text="```json\n" + json.dumps({"id": asset["id"]}) + "\n```"),
        ),
        ModelReceipt("select", "select", "succeeded", "submitted", "fixture", 1, "fixture", "fixture", "openai.chat", "fixture"),
        completion=ModelCompletion("complete", ("stop",)),
    )))
    handler = ReactionSelectionHandler(reactions, artifacts)
    action = Action("select-action", "select-intent", "reactions.select", core.actor, bot,
        {"meaning": "happy", "purpose": "reply", "attitude": "gentle", "intensity": 0.5}, OriginKind.ACTOR)
    receipt = await handler.execute(action, Attempt("select-attempt", action.action_id))
    assert receipt.execution == "succeeded"
    assert receipt.artifacts[0].media.media_id == selected_original.media.media_id
    assert receipt.result["selected"]["id"] == asset["id"]
    assert await artifacts.get(bot, receipt.artifacts[0].media.media_id) == (raw, "image/gif")
    request = reactions.broker.generate.call_args.args[0]
    assert request.function == "reaction.search" and request.max_output_tokens == 1000
    assert request.service_class == ServiceClass.ELASTIC
    assert all(ref in request.source_refs for ref in selected_original.source_refs)


async def test_annotation_accepts_complete_model_json_and_keeps_original_media(core):
    owner, reactions, artifacts, asset = await imported_reaction(core)
    original = await artifacts.reference(owner, asset["media"]["media_id"])
    annotation = {"description": "A gentle congratulations", "ocr": "Great!",
        "actions": ["claps"], "purposes": ["congratulate"]}
    document = json.dumps(annotation)
    reactions.perception = SimpleNamespace(describe=AsyncMock())
    for text in (document, "```json\n" + document + "\n```", "```\n" + document + "\n```"):
        reactions.perception.describe.return_value = {"text": text}
        previous = await reactions.asset(owner, asset["id"])
        updated = await reactions.annotate(owner, {"id": asset["id"]})
        assert updated["revision"] == previous["revision"] + 1
        assert all(updated[key] == value for key, value in annotation.items())
        assert updated["media"] == asset["media"] and updated["index_state"] == "pending"
        assert (await artifacts.reference(owner, asset["media"]["media_id"])).source_refs == original.source_refs


async def test_annotation_rejects_invalid_model_json_without_changing_metadata(core):
    owner, reactions, _, asset = await imported_reaction(core)
    reactions.perception = SimpleNamespace(describe=AsyncMock())
    for text in ("[]", '```json\n{"description":"one","description":"two"}\n```'):
        reactions.perception.describe.return_value = {"text": text}
        with pytest.raises(ProviderError) as invalid:
            await reactions.annotate(owner, {"id": asset["id"]})
        assert invalid.value.code == "reaction_annotation_invalid" and invalid.value.status == 502
        assert invalid.value.effect == "none"
    assert (await reactions.asset(owner, asset["id"]))["body"] == asset


async def test_selection_distinguishes_explicit_null_from_invalid_model_choices(core):
    owner, reactions, _, asset, intent = await indexed_reaction(core)
    reactions.broker.generate.return_value = selection_result('```json\n{"id":null}\n```')
    result = await reactions.search(owner, intent)
    assert result == {"selected": None, "reason": "no_suitable_reaction", "candidates": [asset["id"]]}
    for text, code in (
        ("null", "reaction_selection_invalid"),
        ("[]", "reaction_selection_invalid"),
        ("{}", "reaction_selection_invalid"),
        ('{"id":false}', "reaction_selection_invalid"),
        ('{"id":[]}', "reaction_selection_invalid"),
        ('{"id":{}}', "reaction_selection_invalid"),
        ('{"id":"not-an-authorized-candidate"}', "reaction_selection_out_of_range"),
        ('```json\n{"id":null}\n```\nextra', "reaction_selection_invalid"),
    ):
        reactions.broker.generate.return_value = selection_result(text)
        with pytest.raises(ProviderError) as invalid:
            await reactions.search(owner, intent)
        assert invalid.value.code == code and invalid.value.status == 502
        assert invalid.value.effect == "none"


async def test_selection_rechecks_completion_and_selected_original_after_model_result(core):
    owner, reactions, artifacts, asset, intent = await indexed_reaction(core)
    document = "```json\n" + json.dumps({"id": asset["id"]}) + "\n```"
    reactions.broker.generate.return_value = selection_result(document, completion="incomplete")
    with pytest.raises(ProviderError) as incomplete:
        await reactions.search(owner, intent)
    assert incomplete.value.code == "reaction_selection_incomplete" and incomplete.value.effect == "none"
    original = await artifacts.reference(owner, asset["media"]["media_id"])

    async def revoke_selected_original(_request):
        await core.sources.invalidate(owner, {
            "source_id": original.source_refs[0].source_id,
            "expected_revision": original.source_refs[0].revision,
            "client_request_id": "withdraw-selected-reaction",
        }, forgotten=True)
        return selection_result(document)

    reactions.broker.generate.side_effect = revoke_selected_original
    with pytest.raises(Fault) as withdrawn:
        await reactions.search(owner, intent)
    assert withdrawn.value.code == "source_revoked"


async def test_default_import_and_ordinary_upload_remain_private_and_actor_bound(core, ordinary_users):
    _, bob = ordinary_users
    owner, reactions, artifacts = await library(core)
    raw = gif_bytes()
    pack = await reactions.import_pack(owner, {"id": "private", "name": "Private", "files": [
        {"filename": "private.gif", "data_base64": base64.b64encode(raw).decode()},
    ]})
    assert pack["import_scope"] == "current" and len(pack["scope_ids"]) == 1
    assert not (await reactions.view(bob))["assets"]
    asset = (await reactions.view(owner))["assets"][0]
    with pytest.raises(Fault):
        await artifacts.reference(bob, asset["media"]["media_id"])
    async with core.database.transaction() as conn:
        other_actor = await core.identity.register_actor(core.owner, connection=conn)
    other = await core.identity.context(core.owner.principal_id, actor_id=other_actor)
    shared = await reactions.import_pack(owner, {"id": "shared", "name": "Shared", "import_scope": "actor",
        "files": [{"filename": "shared.gif", "data_base64": base64.b64encode(raw).decode()}]})
    shared_asset = next(x for x in (await reactions.view(owner))["assets"] if x["pack_id"] == shared["id"])
    with pytest.raises(Fault):
        await artifacts.reference(other, shared_asset["media"]["media_id"])
    with pytest.raises(Fault) as changed:
        await reactions.update_pack(owner, {"id": shared["id"], "expected_revision": shared["revision"],
            "actor_ids": [core.actor, other_actor]})
    assert changed.value.code == "reaction_pack_scope_immutable"


async def test_actor_import_rejects_missing_privilege_wrong_actor_scope_and_stale_context(core, ordinary_users):
    _, bob = ordinary_users
    owner, reactions, _ = await library(core)
    base = {"name": "Blocked", "import_scope": "actor", "files": []}
    with pytest.raises(Fault) as permission:
        await reactions.import_pack(bob, base)
    assert permission.value.code == "forbidden"
    with pytest.raises(Fault) as actors:
        await reactions.import_pack(owner, {**base, "actor_ids": []})
    assert actors.value.code == "reaction_import_actor_mismatch"
    with pytest.raises(Fault) as scopes:
        await reactions.import_pack(owner, {**base, "scope_ids": list(owner.scope_ids)})
    assert scopes.value.code == "reaction_import_scope_mismatch"
    with pytest.raises(AppError) as stale:
        await reactions.import_pack(replace(owner, access_epoch=owner.access_epoch - 1), base)
    assert stale.value.code == "access_changed"
    assert not await reactions.config.list("reaction_pack")


async def test_archive_disclosure_keeps_original_dependency_and_native_content_dedup(core, ordinary_users):
    _, bob = ordinary_users
    owner, reactions, artifacts = await library(core)
    raw = gif_bytes()
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("happy/one.gif", raw)
        output.writestr("like/two.gif", raw)
    uploaded = await artifacts.ingest(owner, archive.getvalue(), "application/zip",
        locator={"kind": "upload", "id": "archive"})
    pack = await reactions.import_pack(owner, {"id": "archive", "name": "Archive", "import_scope": "actor",
        "archive_media": wire(uploaded.media)})
    assert pack["asset_count"] == 1
    asset = (await reactions.view(bob))["assets"][0]
    artifact = await artifacts.reference(bob, asset["media"]["media_id"])
    assert await artifacts.get(bob, artifact.media.media_id) == (raw, "image/gif")
    await core.sources.invalidate(owner, {"source_id": uploaded.source_refs[0].source_id,
        "expected_revision": 1, "client_request_id": "forget-upload"}, forgotten=True)
    with pytest.raises(Fault) as revoked:
        await artifacts.reference(bob, artifact.media.media_id)
    assert revoked.value.code == "source_revoked"
    assert not (await core.sources.check_many(bob, (artifact.source_refs,)))[0]
    assert not (await reactions.view(bob))["assets"]


@pytest.mark.parametrize("input_kind", ("artifact", "source"))
async def test_current_import_copies_readable_public_media_and_keeps_withdrawal(core, ordinary_users, input_kind):
    _, bob = ordinary_users
    owner, reactions, artifacts = await library(core)
    raw = gif_bytes()
    shared = await reactions.import_pack(owner, {
        "id": "public-original", "name": "Public original", "import_scope": "actor",
        "files": [{"filename": "public.gif", "data_base64": base64.b64encode(raw).decode()}],
    })
    public_asset = next(item for item in (await reactions.view(owner))["assets"] if item["pack_id"] == shared["id"])
    original = await artifacts.reference(owner, public_asset["media"]["media_id"])
    if input_kind == "source":
        selected_media = (await core.sources.read(owner, original.source_refs[0], {}))["media"]
    else:
        selected_media = wire(original.media)
    private_pack = await import_http_arguments(reactions, owner, {
        "client_request_id": "copy-public-" + input_kind,
        "id": "private-copy", "name": "Private copy", "import_scope": "current",
        "files": [{"filename": "copied.gif", "media": selected_media}],
    })
    assert private_pack["scope_ids"] != shared["scope_ids"]
    copied_asset = next(item for item in (await reactions.view(owner))["assets"] if item["pack_id"] == private_pack["id"])
    private_only = replace(owner, scope_ids=tuple(private_pack["scope_ids"]), audience=tuple(private_pack["scope_ids"]))
    await core.identity.check_context(private_only)
    # The caller can read the public original, while the stored target does not
    # inherit public/other authorized source scopes merely to pass validation.
    with pytest.raises(Fault):
        await artifacts.reference(private_only, original.media.media_id)
    copied = await artifacts.reference(private_only, copied_asset["media"]["media_id"])
    assert copied.source_refs != original.source_refs
    assert await artifacts.get(private_only, copied.media.media_id) == (raw, "image/gif")
    with pytest.raises(Fault):
        await artifacts.reference(bob, copied.media.media_id)
    async with core.database.transaction(read_only=True) as conn:
        stored = await (await conn.execute(
            "SELECT scopes,audience FROM capabilities.artifacts WHERE media_id=%s", (copied.media.media_id,),
        )).fetchone()
        source = await (await conn.execute(
            "SELECT scope_ids,disclosure FROM sources.versions WHERE source_id=%s AND revision=%s",
            (copied.source_refs[0].source_id, copied.source_refs[0].revision),
        )).fetchone()
    assert stored["scopes"] == stored["audience"] == source["scope_ids"] == private_pack["scope_ids"]
    assert source["disclosure"] is True
    await core.sources.invalidate(owner, {
        "source_id": original.source_refs[0].source_id, "expected_revision": 1,
        "client_request_id": "forget-public-" + input_kind,
    }, forgotten=True)
    with pytest.raises(Fault) as revoked:
        await artifacts.reference(private_only, copied.media.media_id)
    assert revoked.value.code == "source_revoked"
    assert not (await core.sources.check_many(private_only, (copied.source_refs,)))[0]


async def test_artifact_reads_allow_read_only_transactions_and_metadata_writes_lock_sources(core):
    owner, reactions, artifacts = await library(core)
    pack = await reactions.import_pack(owner, {
        "id": "source-lock", "name": "Source lock", "import_scope": "actor",
        "files": [{"filename": "shared.gif", "data_base64": base64.b64encode(gif_bytes()).decode()}],
    })
    asset = next(item for item in (await reactions.view(owner))["assets"] if item["pack_id"] == pack["id"])
    async with core.database.transaction(read_only=True) as connection:
        artifact = await artifacts.reference(owner, asset["media"]["media_id"], connection=connection)
    async with core.database.transaction() as writer:
        updated = await reactions.update(owner, {
            "id": asset["id"], "expected_revision": asset["revision"], "description": "A synthetic shared GIF",
        }, conn=writer)
        assert updated["revision"] == asset["revision"] + 1
        # Metadata and source validity commit together. Withdrawal's exclusive
        # source lock must wait for this writer, even though reads take no lock.
        with pytest.raises(LockNotAvailable):
            async with core.database.transaction() as withdrawal:
                await withdrawal.execute(
                    "SELECT source_id FROM sources.originals WHERE source_id=%s FOR UPDATE NOWAIT",
                    (artifact.source_refs[0].source_id,),
                )
