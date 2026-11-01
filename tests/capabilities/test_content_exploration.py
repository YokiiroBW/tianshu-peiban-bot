"""Receipt-backed exploration and forum attribution, without external accounts."""

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

from tianshu.capabilities.content.continuation import (
    continuation_arguments, continuation_step, exploration_checkpoint,
)
from tianshu.capabilities.content.discovery import BilibiliDiscovery
from tianshu.capabilities.content.parsing import discourse, html_content
from tianshu.capabilities.content.service import Content
from tianshu.capabilities.content_discovery.candidates import candidate
from tianshu.capabilities.contracts import Fault


class ForumAttribution(TestCase):
    def test_quoted_author_is_separate_from_current_speaker(self):
        content = html_content(b'''<title>Topic</title><main>
          <article data-post-id="12" data-post-number="2"><a class="username">Bob</a>
          <div class="cooked"><aside class="quote" data-username="Alice" data-post="1">
          <div class="title">Alice wrote:</div><blockquote>I like rain.</blockquote></aside>
          I disagree.</div><time datetime="2026-11-02T09:00:00Z"></time></article>
          <a rel="next" href="?page=2">Next</a></main>''', "https://forum.example/t/1")
        post = content["posts"][0]
        self.assertEqual(post["author"], "Bob")
        self.assertNotIn("I like rain", post["text"])
        self.assertEqual(post["quotes"][0]["author"], "Alice")
        self.assertEqual(post["quotes"][0]["text"], "I like rain.")
        self.assertEqual(post["created_at"], "2026-11-02T09:00:00Z")
        self.assertIn("quotation, not current speaker", content["text"])
        self.assertEqual(content["next_url"], "https://forum.example/t/1?page=2")

    def test_discourse_posts_endpoint_preserves_remainder_until_last_page(self):
        def posts(ids):
            return [{"id": value, "post_number": value, "username": "author" + str(value),
                     "cooked": "<p>body " + str(value) + "</p>", "version": 2} for value in ids]

        first = discourse({"id": 42, "title": "Original topic", "post_stream": {
            "stream": list(range(1, 46)), "posts": posts(range(1, 21))}}, "https://forum.example/t/42.json")
        self.assertEqual(parse_qs(urlsplit(first["next_url"]).query)["post_ids[]"],
                         [str(value) for value in range(21, 41)])
        second = discourse({"post_stream": {"posts": posts(range(21, 41))}},
                           first["next_url"], first["forum_cursor"])
        self.assertEqual(second["title"], "Original topic")
        self.assertEqual(parse_qs(urlsplit(second["next_url"]).query)["post_ids[]"],
                         [str(value) for value in range(41, 46)])
        last = discourse({"post_stream": {"posts": posts(range(41, 46))}},
                         second["next_url"], second["forum_cursor"])
        self.assertIsNone(last["next_url"])
        self.assertEqual(last["canonical_url"], "https://forum.example/t/42.json")
        self.assertEqual(last["posts"][-1]["revision"], 2)


class ExplorationContinuation(TestCase):
    def test_discovery_selects_actual_read_without_marking_candidates_consumed(self):
        following = {"capability_id": "content.read", "arguments": {"url": "https://example.org/body",
            "content_key": "url:body", "mode": "research"}}
        result = {"candidates": [{"content_key": "url:body"}], "next_actions": [
            {**following, "content_key": "url:body"}], "continuation": following,
            "read_basis": "discovery_metadata", "source_refs": [{"source_id": "search", "revision": 1}]}
        self.assertEqual(continuation_step("content.discover", {}, result), following)
        self.assertIsNone(continuation_arguments("content.discover", {}, result))
        checkpoint = exploration_checkpoint("content.discover", {"mode": "research"}, result)
        self.assertEqual(checkpoint["seen_keys"], ["url:body"])
        self.assertEqual(checkpoint["consumed_sources"], [])
        read = exploration_checkpoint("content.read", following["arguments"], {
            "read_basis": "actual_source_body", "content_key": "url:body",
            "source_refs": [{"source_id": "body", "revision": 3}]}, checkpoint)
        self.assertEqual(read["next_actions"], [])
        self.assertEqual(read["consumed_sources"], [{"source_id": "body", "revision": 3}])

    def test_video_continuation_reuses_original_and_exact_checkpoint(self):
        result = {"consumption": {"source": {"source_id": "video", "revision": 3},
            "media": {"media_id": "artifact:original"}, "analysis_completed": True,
            "next_position": {"unit": "seconds", "start": 29.75, "end": 180}},
            "reading_state": {"consumption_id": "actual-attempt"}}
        args = continuation_arguments("video.observe", {"url": "https://bilibili.com/video/BVx",
            "cid": 12, "account_id": "account", "quality": {}, "start": 0, "end": 180}, result)
        self.assertNotIn("url", args)
        self.assertNotIn("account_id", args)
        self.assertEqual((args["start"], args["end"]), (29.75, 180))
        self.assertEqual(args["reading_state"], {"consumption_id": "actual-attempt"})
        result["consumption"]["analysis_completed"] = False
        self.assertIsNone(continuation_arguments("video.observe", {}, result))


class PublicDiscovery(IsolatedAsyncioTestCase):
    async def test_real_bilibili_page_cursor_does_not_restart_search(self):
        requests = []

        class Connector:
            async def request(self, path, *, params):
                requests.append((path, params))
                return {"numPages": 2, "result": [{"bvid": "BV" + str(value), "title": "Music " + str(value),
                    "author": "Artist"} for value in range(20)]}

            async def close(self):
                pass

        browser = BilibiliDiscovery(connector_factory=Connector)
        rows, status = await browser.page({}, {"query": "Music", "limit": 8}, "bilibili_search")
        self.assertEqual(len(rows), 8)
        more, second = await browser.page({}, {"query": "Music", "limit": 8,
            "cursors": {"bilibili_search": status["next_cursor"]}}, "bilibili_search")
        self.assertEqual(more[0]["content_key"], "bilibili:BV8")
        self.assertEqual(requests[-1][1]["page"], 1)
        self.assertIsNotNone(second["next_cursor"])

    async def test_interest_and_one_failed_source_still_offer_reading(self):
        context = SimpleNamespace(actor_id="actor", scope_ids=("private",), audience=("private",))
        service = Content(SimpleNamespace(list=AsyncMock(return_value=[])), None,
                          SimpleNamespace(check=AsyncMock()))
        service.discovery.discover = AsyncMock(return_value={"candidates": [], "sources": [
            {"source": "web", "state": "unavailable", "code": "source_unavailable",
             "has_more": None, "next_cursor": None}], "omitted_count": 0})
        item = candidate("bilibili", "bilibili:BVsample", kind="video", title="Music",
                         url="https://www.bilibili.com/video/BVsample", acquired=0)
        service.bilibili.discover = AsyncMock(return_value=([item], [{"source": "bilibili_search",
            "state": "ok", "code": None, "has_more": False, "next_cursor": None}]))
        result = await service.discover(context, {"mode": "explore", "interests": ["Music"]})
        self.assertEqual(service.bilibili.discover.await_args.args[1]["query"], "Music")
        self.assertEqual(result["continuation"]["capability_id"], "video.observe")
        self.assertNotIn("end", result["continuation"]["arguments"])
        self.assertFalse(result["experience_eligible"])
        self.assertEqual(result["read_basis"], "discovery_metadata")

    async def test_research_requires_a_question_but_idle_exploration_does_not(self):
        service = Content(SimpleNamespace(list=AsyncMock(return_value=[])), None,
                          SimpleNamespace(check=AsyncMock()))
        with self.assertRaises(Fault) as missing:
            await service.discover(SimpleNamespace(actor_id="actor", scope_ids=(), audience=()), {"mode": "research"})
        self.assertEqual(missing.exception.code, "research_question_required")
