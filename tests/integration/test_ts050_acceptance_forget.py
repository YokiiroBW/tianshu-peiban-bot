"""Real user confirmation, existing revise HTTP, durable suppression and actor isolation."""

import asyncio
import copy

from tianshu_memory.domain import Fault
from ts050_approval_support import ApprovalChain


class ConfirmedForget(ApprovalChain):
    async def test_user_CLI_forget_survives_restart_higher_source_and_old_candidate(self):
        fanout = await self.submit(
            self.physical("上午不喝咖啡，下午偶尔可以。", "forget:shared"),
            actors=("actor:a", "actor:b"),
        )
        commits = await self.wait_commits(2)
        admissions = {o["actor_id"]: o["admission"] for o in fanout["outcomes"]}
        writes, drafts = {}, {}
        for actor in ("actor:a", "actor:b"):
            commit = next(c for c in commits if c["request"]["scope"]["actor_id"] == actor)
            drafts[actor], writes[actor] = await self.commit_draft(commit, ["上午不喝咖啡。"])
        a, b = admissions["actor:a"], admissions["actor:b"]
        self.register_local_user("owner", [a])
        await self.submit(self.physical("另一个待提炼的咖啡输入。", "forget:pending"))
        pending = (await self.wait_commits(3))[2]
        request, forgotten = await self.approve_forget(
            "owner", a, writes["actor:a"]["record_ids"][0], cli=True
        )
        self.assertEqual(forgotten["authoritative_state"], "tombstoned")
        repeat = await self.client.post(
            self.memory_url + "/internal/v1/memory/revise",
            json=request,
            headers={"Authorization": self.bearer("CORE_MEMORY")},
        )
        self.assertEqual(repeat.status_code, 200, repeat.text)
        self.assertEqual(repeat.json(), forgotten)
        reuse = copy.deepcopy(request)
        reuse["command"]["idempotency_key"] += ":new"
        reuse["expected_version"] = forgotten["record_version"]
        refused = await self.client.post(
            self.memory_url + "/internal/v1/memory/revise",
            json=reuse,
            headers={"Authorization": self.bearer("CORE_MEMORY")},
        )
        self.assertEqual(refused.status_code, 403, refused.text)
        for restarted in (False, True):
            if restarted:
                await self.restart_memory_and_core()
            selected_a = await self.select(a, "咖啡")
            selected_b = await self.select(b, "咖啡")
            self.assertEqual(selected_a.status_code, 200, selected_a.text)
            self.assertEqual(selected_a.json()["selected_units"], [])
            self.assertEqual(selected_b.status_code, 200, selected_b.text)
            self.assertEqual(
                [u["record_id"] for u in selected_b.json()["selected_units"]],
                writes["actor:b"]["record_ids"],
            )
            with self.assertRaises(Fault):
                await self.commit_draft(pending, ["另一个待提炼的咖啡输入。"])
            # A historical idempotent receipt, if returned, must not reactivate its record.
            try:
                replay = await asyncio.to_thread(self.workflow.commit_candidate, drafts["actor:a"])
            except Fault as error:
                replay = {"code": error.code, "status": error.status}
            self.assertEqual((await self.select(a, "咖啡")).json()["selected_units"], [])
            self.check(
                "forget_actor_isolation",
                restarted=restarted,
                a=selected_a.json(),
                b=selected_b.json(),
                old_job_result=replay,
                pending_job_rejected=True,
            )
        higher = await self.submit(
            self.physical("更高修订再次提及咖啡。", "forget:shared", revision=2, kind="edit")
        )
        await self.eventually(
            lambda: all(
                t["phase"] in {"sent", "failed", "cancelled", "observed", "closed_unknown"}
                for t in self.core.store.list("turns")
            )
        )
        admission = higher["outcomes"][0]["admission"]
        access = await self.current([admission])
        self.assertEqual(access["grants"][0]["state"], "allowed")
        after = await self.select(admission, "咖啡")
        self.assertEqual(after.status_code, 200, after.text)
        self.assertEqual(after.json()["selected_units"], [])
        self.check(
            "higher_remote_allowed_does_not_clear_local_suppression",
            approval_and_revise=forgotten,
            higher_admission=admission,
            current_access=access,
            selection=after.json(),
            consumed_confirmation_reuse_status=403,
            note="B preserved across A forget/restart; later physical edit has its separate global invalidation",
        )
