"""First actual W0 conversation from source_input through trusted Memory candidate commit."""

import asyncio

from ts050_source_support import SourceChain


class PrivateSourceChain(SourceChain):
    async def test_first_w0_generation_receipt_candidate_and_complete_memory(self):
        result = await self.submit(
            self.physical("我叫小明，上午不喝咖啡，下午偶尔可以。", "private:first")
        )
        self.assertIsNotNone(result["person_id"])
        admission = result["outcomes"][0]["admission"]
        self.assertEqual(
            admission["source"]["receipt_id"], result["outcomes"][0]["receipt"]["receipt_id"]
        )
        commits = await self.wait_commits(1)
        event, receipt = commits[0]["request"], commits[0]["response"]
        self.assertEqual(event["delivery_state"], "sent")
        self.assertEqual(receipt["state"], "accepted")
        self.assertFalse(receipt["confirmed_memory_written"])
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual(len(self.channel_requests), 1)
        self.assertEqual(self.core.store.list("turns")[0]["model_calls"], 1)
        draft, committed = await self.commit_draft(
            commits[0], ["上午不喝咖啡。", "只有下午偶尔可以喝咖啡。"]
        )
        self.assertEqual(committed["state"], "committed")
        self.assertEqual(len(committed["record_ids"]), 2)
        repeated = await asyncio.to_thread(self.workflow.commit_candidate, draft)
        self.assertEqual(repeated, committed)
        selection = await self.select(admission, "咖啡")
        self.assertEqual(selection.status_code, 200, selection.text)
        selected = selection.json()
        self.assertEqual(
            {u["record_id"] for u in selected["selected_units"]}, set(committed["record_ids"])
        )
        self.assertEqual(len(selected["dependency_groups"]), 1)
        zero = await self.select(admission, "咖啡", budget={"tokens": 0, "bytes": 0})
        self.assertEqual(zero.status_code, 200, zero.text)
        self.assertEqual(zero.json()["selected_units"], [])
        self.assertEqual(zero.json()["scope_version"], selected["scope_version"])
        self.check(
            "first_private_w0_actual_chain",
            fanout=result,
            event=event,
            consume=receipt,
            draft_source="deterministic external extraction substitute from synthetic input",
            trusted_commit=committed,
            idempotent_commit=repeated,
            selection=selected,
            zero=zero.json(),
        )
