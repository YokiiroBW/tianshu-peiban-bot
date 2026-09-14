"""Real owner/curator approval, Core profile prompt, pre-send version check and invalidation."""

import asyncio
import json
import time

from tianshu_companion.clients import utc
from tianshu_memory.domain import Fault
from ts050_approval_support import ApprovalChain


class ApprovedProfiles(ApprovalChain):
    async def test_owner_curator_profiles_prompt_revocation_and_private_epoch_isolation(self):
        private = await self.submit(
            self.physical("我下午偶尔喜欢咖啡。", "profile:private"), actors=("actor:a", "actor:b")
        )
        await self.wait_commits(2)
        pa, pb = [o["admission"] for o in private["outcomes"]]
        group = await self.submit(
            self.physical("本群周末讨论咖啡。", "profile:group", audience="group")
        )
        await self.wait_commits(3)
        ga = group["outcomes"][0]["admission"]
        owner_permission = {
            "role": "owner",
            "actor_id": "actor:a",
            "subject_kind": "person",
            "category": "interest",
            "sharing": "public_preference",
            "conversation_id": None,
        }
        curator_permission = {
            "role": "curator",
            "actor_id": "actor:a",
            "subject_kind": "group",
            "category": "topic",
            "sharing": "group_only",
            "conversation_id": ga["scope"]["conversation_id"],
        }
        self.register_local_user("owner", [pa, ga], [owner_permission])
        self.register_local_user("curator", [ga], [curator_permission])
        interest = self.profile_draft(pa)
        topic = self.profile_draft(
            ga,
            subject={"kind": "group", "conversation_id": ga["scope"]["conversation_id"]},
            category="topic",
            sharing="group_only",
            statement="本群周末讨论咖啡。",
        )
        for credential in ("wrong-local-user-credential-at-least-32", self.tokens["CORE_MEMORY"]):
            with self.assertRaises(Fault) as error:
                await self.user_action(
                    {
                        "operation": "approve_profile",
                        "origin": pa["accepted_origin"],
                        "draft": interest,
                        "expires_at": utc(),
                    },
                    "owner",
                    credential=credential,
                )
            self.assertEqual(error.exception.status, 401)
        with self.assertRaises(Fault) as no_curator:
            await self.user_action(
                {
                    "operation": "approve_profile",
                    "origin": ga["accepted_origin"],
                    "draft": topic,
                    "expires_at": utc(time.time() + 600),
                },
                "owner",
            )
        self.assertEqual(no_curator.exception.status, 403)
        _, interest_write, interest_publish = await self.publish(
            "owner", pa, interest, cli_approve=True
        )
        _, topic_write, topic_publish = await self.publish("curator", ga, topic)
        same = await self.user_action(interest_publish, "owner")
        self.assertEqual(same, interest_write)
        person = {"kind": "person", "person_id": pa["scope"]["person_id"]}
        group_target = topic["subject"]
        interests = await self.profile_query(ga, person, ["interest"])
        topics = await self.profile_query(ga, group_target, ["topic"])
        self.assertEqual(
            [u["record_id"] for u in interests["selected_units"]], interest_write["record_ids"]
        )
        self.assertEqual(
            [u["record_id"] for u in topics["selected_units"]], topic_write["record_ids"]
        )
        self.assertTrue(
            all(
                s["kind"] == "shareable_projection"
                for unit in interests["selected_units"] + topics["selected_units"]
                for s in unit["sources"]
            )
        )
        before_epoch = interests["scope_version"]
        b_before = await self.profiles(pb)
        unrelated = await self.submit(self.physical("另一个私密咖啡记录。", "profile:unrelated"))
        uc = (await self.wait_commits(4))[3]
        _, written = await self.commit_draft(uc, ["另一个私密咖啡记录。"])
        await self.approve_forget(
            "owner", unrelated["outcomes"][0]["admission"], written["record_ids"][0], cli=False
        )
        after_private = await self.profile_query(ga, person, ["interest"])
        b_after = await self.profiles(pb)
        self.assertEqual(after_private["scope_version"], before_epoch)
        self.assertEqual(b_before["scope_version"], b_after["scope_version"])
        await self.submit(
            self.physical(
                "我们本群聊咖啡，你知道我的咖啡兴趣吗？", "profile:question", audience="group"
            )
        )
        self.assert_sent_commits(
            [
                c
                for c in await self.wait_commits(5)
                if c["request"]["turn_sequence"] == 2
                and c["request"]["scope"]["audience"] == "group"
            ]
        )
        prompt = json.loads(self.model_requests[-1]["body"]["messages"][-1]["content"])
        units = [u for p in prompt["profiles"] for u in p["selected_units"]]
        self.assertEqual(
            {u["record_id"] for u in units},
            set(interest_write["record_ids"] + topic_write["record_ids"]),
        )
        successful = next(
            t
            for t in self.core.store.list("turns")
            if t["bundle"]["messages"][0]["message_key"]["message_id"] == "profile:question"
        )
        self.assertEqual(successful["phase"], "sent")
        self.assertTrue(successful["profile_checks"])
        self.assertTrue(
            all(c["version_domain"] == "profile-memory/v1" for c in successful["profile_checks"])
        )
        self.assertGreater(
            successful["profile_checks"][0]["scope_version"], successful["scope_version"]
        )
        gate = self.model_gates["actor:a"] = asyncio.Event()
        before_calls, before_sends = len(self.model_requests), len(self.channel_requests)
        await self.submit(
            self.physical("请再按咖啡兴趣与本群咖啡话题回答。", "profile:stale", audience="group")
        )
        await self.eventually(lambda: len(self.model_requests) == before_calls + 1)
        stale_prompt = json.loads(self.model_requests[-1]["body"]["messages"][-1]["content"])
        self.assertTrue(stale_prompt["profiles"])
        revoked_action = {**interest_publish, "operation": "revoke_profile"}
        revoked = await self.user_action(revoked_action, "owner", cli=True)
        self.assertEqual(revoked["state"], "revoked")
        gate.set()
        await self.wait_commits(6)
        stale = next(
            t
            for t in self.core.store.list("turns")
            if t["bundle"]["messages"][0]["message_key"]["message_id"] == "profile:stale"
        )
        self.assertEqual(
            (stale["phase"], stale["failure"], stale["model_calls"]), ("failed", "scope_changed", 1)
        )
        self.assertEqual(len(self.channel_requests), before_sends)
        self.assertEqual((await self.profile_query(ga, person, ["interest"]))["selected_units"], [])
        self.assertEqual(
            [
                u["record_id"]
                for u in (await self.profile_query(ga, group_target, ["topic"]))["selected_units"]
            ],
            topic_write["record_ids"],
        )
        with self.assertRaises(Fault):
            await self.user_action(interest_publish, "owner")
        await self.submit(
            self.physical("", "profile:group", audience="group", revision=2, kind="retract"),
            actors=(),
        )
        withdrawn = await self.profile_query(ga, group_target, ["topic"])
        self.assertEqual(withdrawn["selected_units"], [])
        with self.assertRaises(Fault):
            await self.user_action(topic_publish, "curator")
        public_after_revoke = await self.profiles(pa)
        b_before_edit = await self.profiles(pb)
        private_edit = await self.submit(
            self.physical(
                "撤销共享后，私下更改咖啡偏好。", "profile:private", revision=2, kind="edit"
            )
        )
        new_private = private_edit["outcomes"][0]["admission"]
        await self.select(new_private, "咖啡", budget={"tokens": 0, "bytes": 0})
        public_after_edit = await self.profiles(new_private)
        b_after_edit = await self.profiles(pb)
        self.assertEqual(public_after_edit["scope_version"], public_after_revoke["scope_version"])
        self.assertEqual(b_after_edit["scope_version"], b_before_edit["scope_version"])
        self.assertEqual(public_after_edit["selected_units"], [])
        await self.eventually(
            lambda: all(
                t["phase"] in {"sent", "failed", "cancelled", "observed", "closed_unknown"}
                for t in self.core.store.list("turns")
            )
        )
        self.check(
            "real_owner_curator_profile_pipeline",
            owner_publication=interest_write,
            curator_publication=topic_write,
            core_profile_prompt=prompt["profiles"],
            profile_checks=successful["profile_checks"],
            text_scope_version=successful["scope_version"],
            private_epoch_before=before_epoch,
            private_epoch_after=after_private["scope_version"],
            actor_b_epoch_before=b_before["scope_version"],
            actor_b_epoch_after=b_after["scope_version"],
            stale_turn={k: stale[k] for k in ("id", "phase", "failure", "model_calls")},
            no_stale_send=True,
            revoked=revoked,
            source_withdrawal=withdrawn,
            unauthorized_user_and_service_status=401,
            owner_without_curator_status=403,
            revoked_public_epoch_before_private_edit=public_after_revoke["scope_version"],
            revoked_public_epoch_after_private_edit=public_after_edit["scope_version"],
            actor_b_epoch_before_private_edit=b_before_edit["scope_version"],
            actor_b_epoch_after_private_edit=b_after_edit["scope_version"],
        )
