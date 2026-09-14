"""Actual P/A receipts, complete Memory writes, negative propagation and approval gaps."""

import asyncio
import copy
import time

from services.platform.contracts import Fault as PlatformFault
from tianshu_companion.clients import command, utc
from tianshu_memory.domain import Fault as MemoryFault
from ts050_source_support import SourceChain


class FanoutSourceChain(SourceChain):
    async def test_private_and_group_same_physical_distinct_actor_receipts_and_candidates(self):
        count = 0
        for audience in ("self_private", "group"):
            physical = self.physical(
                "合成咖啡偏好，上午不喝咖啡。", "same-physical", audience=audience
            )
            first = await self.submit(physical, actors=("actor:a",))
            count += 1
            await self.wait_commits(count)
            second = await self.submit(physical, actors=("actor:b",))
            count += 1
            commits = await self.wait_commits(count)
            a, b = first["outcomes"][0]["admission"], second["outcomes"][0]["admission"]
            self.assertEqual(a["physical_receipt_id"], b["physical_receipt_id"])
            self.assertNotEqual(a["source"]["receipt_id"], b["source"]["receipt_id"])
            self.assertEqual(a["scope"]["conversation_id"], b["scope"]["conversation_id"])
            self.assertEqual(a["scope"]["person_id"], b["scope"]["person_id"])
            writes = []
            for admission in (a, b):
                commit = next(r for r in commits if r["request"]["scope"] == admission["scope"])
                _, result = await self.commit_draft(commit, ["上午不喝咖啡。"])
                self.assertEqual(result["state"], "committed")
                writes.append(result)
                replay = await self.client.post(
                    self.memory_url + "/internal/v1/memory/turn-commits",
                    json=commit["request"],
                    headers={"Authorization": self.bearer("CORE_MEMORY")},
                )
                self.assertEqual(replay.status_code, 200, replay.text)
                self.assertEqual(replay.json()["state"], "duplicate")
                self.assertEqual(
                    replay.json()["candidate_job_ref"], commit["response"]["candidate_job_ref"]
                )
                selected = await self.select(admission, "咖啡")
                self.assertEqual(selected.status_code, 200, selected.text)
                self.assertEqual(
                    [u["record_id"] for u in selected.json()["selected_units"]],
                    result["record_ids"],
                )
            self.assertNotEqual(writes[0]["record_ids"], writes[1]["record_ids"])
            forged = copy.deepcopy(a)
            forged["accepted_origin"] = b["accepted_origin"]
            denied = await self.select(forged, "咖啡")
            self.assertEqual(denied.status_code, 403, denied.text)
            facts = await self.facts([a["selector"], b["selector"]])
            self.assertEqual(len(facts["physicals"]), 1)
            self.assertEqual(len(facts["admissions"]), 2)
            self.check(
                "same_P_distinct_A_" + audience,
                a=a,
                b=b,
                writes=writes,
                owner_facts=facts,
                cross_actor_scope_status=403,
            )
        self.assertEqual(len(self.model_requests), 4)
        self.assertEqual(len(self.channel_requests), 4)
        self.assertEqual(len(await asyncio.to_thread(self.workflow.jobs)), 4)

    async def test_edit_retract_old_evidence_and_candidate_cannot_revive(self):
        original = await self.submit(
            self.physical("上午不喝咖啡。", "edit-source"), actors=("actor:a", "actor:b")
        )
        commits = await self.wait_commits(2)
        old = {o["actor_id"]: o["admission"] for o in original["outcomes"]}
        drafts, written = [], []
        for actor in ("actor:a", "actor:b"):
            commit = next(r for r in commits if r["request"]["scope"]["actor_id"] == actor)
            draft, result = await self.commit_draft(commit, ["上午不喝咖啡。"])
            drafts.append(draft)
            written.append(result)
        profile_before = await self.profiles(old["actor:a"])
        changed = await self.submit(
            self.physical("现在下午偶尔喝咖啡。", "edit-source", revision=2, kind="edit")
        )
        await self.wait_commits(3)
        current_a = changed["outcomes"][0]["admission"]
        self.assertNotEqual(
            current_a["source"]["receipt_id"], old["actor:a"]["source"]["receipt_id"]
        )
        for admission in (current_a, old["actor:b"]):
            selected = await self.select(admission, "咖啡")
            self.assertEqual(selected.status_code, 200, selected.text)
            self.assertEqual(selected.json()["selected_units"], [])
        profile_after = await self.profiles(current_a)
        self.assertEqual(profile_before["scope_version"], profile_after["scope_version"])
        self.assertEqual(profile_after["version_domain"], "profile-memory/v1")
        text = (await self.select(current_a, "咖啡", budget={"tokens": 0, "bytes": 0})).json()
        self.assertGreater(text["scope_version"], profile_after["scope_version"])
        for draft in drafts:
            with self.assertRaises(MemoryFault):
                await asyncio.to_thread(self.workflow.commit_candidate, draft)
        retracted = await self.submit(
            self.physical("", "edit-source", revision=3, kind="retract"), actors=()
        )
        self.assertEqual(retracted["outcomes"], [])
        facts = await self.facts([old["actor:a"]["selector"], old["actor:b"]["selector"]])
        self.assertEqual(facts["physicals"][0]["state"], "withdrawn")
        self.assertEqual(facts["physicals"][0]["revision"], 3)
        for admission in (current_a, old["actor:b"]):
            selected = await self.select(admission, "咖啡")
            self.assertEqual(selected.status_code, 200, selected.text)
            self.assertEqual(selected.json()["selected_units"], [])
        with self.assertRaises(PlatformFault) as denied:
            await self.submit(self.physical("试图复活咖啡旧证据", "edit-source", revision=4))
        self.check(
            "edit_and_global_retract_without_revival",
            previous_writes=written,
            new_a=current_a,
            tombstone=facts,
            revive_error={"code": denied.exception.code, "status": denied.exception.status},
            text_version=text["scope_version"],
            profile_before=profile_before,
            profile_after=profile_after,
            approved_profile_invalidation_covered=False,
        )

    async def test_forget_confirmation_and_profile_publication_remain_real_gaps(self):
        fanout = await self.submit(self.physical("合成本人咖啡偏好。", "approval-source"))
        commit = (await self.wait_commits(1))[0]
        admission = fanout["outcomes"][0]["admission"]
        _, written = await self.commit_draft(commit, ["本人上午不喝咖啡。"])
        resolved = await self.client.post(
            self.platform_url + "/internal/v1/origins/resolve",
            json={
                "schema_version": 1,
                "request_id": "approval:context",
                "assertion_ref": admission["accepted_origin"]["assertion_ref"],
            },
            headers={"Authorization": self.bearer("MEMORY_PLATFORM")},
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        context = resolved.json()["context"]
        request = {
            "command": command(
                admission["accepted_origin"], "forget:without-approval", time.time()
            ),
            "record_id": written["record_ids"][0],
            "expected_version": 1,
            "revision_kind": "forget",
            "confirmation_ref": "synthetic-not-approved",
            "evidence_refs": [admission["source"]],
            "replacement_statement": None,
        }
        confirmation = {
            "request": request,
            "verified_context": context,
            "binding_version": admission["binding_version"],
            "expires_at": utc(time.time() + 20),
        }
        with self.assertRaises(MemoryFault) as unavailable:
            await asyncio.to_thread(self.workflow.confirm_revision, confirmation)
        self.assertEqual(unavailable.exception.status, 503)
        refused = await self.client.post(
            self.memory_url + "/internal/v1/memory/revise",
            json=request,
            headers={"Authorization": self.bearer("CORE_MEMORY")},
        )
        self.assertEqual(refused.status_code, 403, refused.text)
        for method, args in (
            (
                self.workflow.approve_profile,
                ({"synthetic_source": admission["source"]}, context, utc(time.time() + 20)),
            ),
            (
                self.workflow.publish_profile,
                ({"synthetic_source": admission["source"]}, "synthetic-not-approved", context),
            ),
        ):
            with self.assertRaises(MemoryFault) as error:
                await asyncio.to_thread(method, *args)
            self.assertEqual(error.exception.status, 503)
        selected = await self.select(admission, "咖啡")
        self.assertEqual(selected.status_code, 200, selected.text)
        self.assertEqual(
            [u["record_id"] for u in selected.json()["selected_units"]], written["record_ids"]
        )
        self.check(
            "approval_gaps_are_not_fake_forget_success",
            confirmation_status=503,
            revise_status=403,
            profile_approval_status=503,
            profile_publish_status=503,
            memory_retained=selected.json(),
            positive_forget_suppression_and_approved_profile_lineage="blocked: real approval issuer absent",
        )
