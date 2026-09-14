"""Ensure a real committed Memory group reaches the next Core/Gateway model request."""

import json

from ts050_source_support import SourceChain


class ActualRecallChain(SourceChain):
    async def test_complete_recalled_group_reaches_core_gateway_and_channel(self):
        await self.submit(self.physical("上午不喝咖啡，只有下午偶尔可以。", "recall-source"))
        commit = (await self.wait_commits(1))[0]
        _, written = await self.commit_draft(commit, ["上午不喝咖啡。", "只有下午偶尔可以喝咖啡。"])
        await self.submit(self.physical("你记得之前的咖啡偏好吗？", "recall-question"))
        await self.wait_commits(2)
        self.assertEqual(len(self.model_requests), 2)
        self.assertEqual(len(self.channel_requests), 2)
        prompt = json.loads(self.model_requests[1]["body"]["messages"][-1]["content"])
        self.assertEqual({u["record_id"] for u in prompt["evidence"]}, set(written["record_ids"]))
        self.assertEqual(len(prompt["dependency_groups"]), 1)
        self.assertEqual(
            set(prompt["dependency_groups"][0]["record_ids"]), set(written["record_ids"])
        )
        self.assertEqual(
            {u["statement"] for u in prompt["evidence"]},
            {"上午不喝咖啡。", "只有下午偶尔可以喝咖啡。"},
        )
        self.assertEqual([t["model_calls"] for t in self.core.store.list("turns")], [1, 1])
        self.assertEqual(
            self.channel_requests[1]["text"],
            self.model_requests[1]["response"]["choices"][0]["message"]["content"],
        )
        self.check(
            "actual_recalled_complete_group_in_core_model_request",
            trusted_commit=written,
            evidence=prompt["evidence"],
            dependency_groups=prompt["dependency_groups"],
            model_calls_per_turn=[1, 1],
            channel_text=self.channel_requests[1]["text"],
            generation="recorded external model; prompt assembly and evidence are real products",
        )
