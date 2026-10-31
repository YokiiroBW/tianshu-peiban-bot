"""Recognize access notices and record evidence from actual chapter reads."""

import re

from ...contracts import Fault, require
from .documents import plain_text


def check_content(value):
    text = plain_text(value)
    require(text.strip(), "book_content_empty", 502)
    # Short access notices are not novel chapters. Do not reject ordinary prose
    # merely because it mentions logging in, VIP membership or subscriptions.
    if len(text) > 800:
        return
    if re.fullmatch(
        r"(?:暂无(?:正文|内容)|章节不存在|内容加载失败|正在加载(?:正文)?)[。！!\s]*", text
    ):
        raise Fault("book_content_empty", 502)
    patterns = (
        (
            "book_source_rate_limited",
            r"(?:请求|访问)(?:过于|太过|过度)?频繁|too\s+many\s+requests",
        ),
        (
            "book_source_auth_required",
            r"(?:请|需要|必须)(?:先)?(?:登录|登陆)[^。！？\n]{0,24}(?:阅读|查看|继续)"
            r"|(?:登录|登陆)后(?:才能|可|即可)(?:阅读|查看|见)"
            r"|(?:正文|章节|内容)[^。！？\n]{0,12}(?:登录|登陆)后可见"
            r"|(?:please\s+)?(?:log\s*in|sign\s*in)\s+to\s+(?:read|continue|view)",
        ),
        (
            "book_content_purchase_required",
            r"(?:请|需要|必须)(?:先)?(?:购买|订阅|付费|解锁)[^。！？\n]{0,20}(?:阅读|查看|本章|章节)"
            r"|(?:本章|章节|正文)(?:为|是|属于)?(?:VIP|付费|收费)[^。！？\n]{0,24}(?:解锁|订阅|购买)"
            r"|(?:购买|订阅|付费|解锁)(?:本章|章节)?后(?:才可|才能|可|即可)(?:阅读|查看)",
        ),
        (
            "book_content_verification_required",
            r"(?:请|需要)(?:先)?(?:完成|通过|输入)[^。！？\n]{0,12}(?:验证码|人机验证|安全验证)"
            r"|verify\s+(?:that\s+)?you\s+are\s+human",
        ),
    )
    for code, pattern in patterns:
        if re.search(pattern, text, re.I):
            raise Fault(code, 409)


class Readability:
    def __init__(self, books):
        self.books, self.repo = books, books.repo

    def record(self, identity, state, *, chapter=None, code=None, count=0):
        with self.repo.transaction() as db:
            book = self.books.catalog.book(identity, db)
            previous = book.get("readability") or {}
            if state == "previewed" and (
                previous.get("state") in {"sampled", "complete"}
                or previous.get("state") == "failed"
                and previous.get("chapter_id") != chapter["chapter_id"]
            ):
                return previous
            result = {
                "state": state,
                "code": code,
                "checked_at": self.repo.clock(),
                "sample_count": count,
                "chapter_id": chapter["chapter_id"] if chapter else None,
                "chapter_title": chapter["title"] if chapter else None,
            }
            self.repo.put("book", identity, {**book, "readability": result}, db=db)
            return result

    async def verify(self, book, directory, pinned):
        first = next(
            (
                index
                for index, chapter in enumerate(directory[:10])
                if not re.match(r"^(感谢|请假|上架|通知|公告|求票|致读者)", chapter["title"])
            ),
            0,
        )
        selected = {first, len(directory) // 2, len(directory) - 1}
        previous = book.get("readability") or {}
        if previous.get("state") == "failed":
            selected.update(
                index
                for index, chapter in enumerate(directory)
                if chapter["chapter_id"] == previous.get("chapter_id")
            )
        for index in sorted(selected):
            await self.books.preview(
                book["book_id"], directory[index]["chapter_id"], pinned=pinned, observe=False
            )
        await self.books.media.local(self.record, book["book_id"], "sampled", count=len(selected))
