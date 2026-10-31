"""Source diagnostics record observed stages without changing source preferences."""

import re


def source_error(messages):
    """Classify upstream evidence; never expose its URLs, secrets or stack traces."""
    text = "\n".join(str(message)[:12000] for message in messages[:8]).casefold()
    patterns = (
        ("comic_source_search_unsupported", r": no search|search (?:is )?not supported|不支持搜索"),
        ("comic_source_content_restricted", r"r18.*(?:开关|显示|開關|顯示)|content.*restricted"),
        ("comic_source_no_chapters", r"no chapters? (?:found|available)|未找到章节|找不到章節"),
        ("comic_source_format_changed", r"解析失[败敗]|解析(?:错误|錯誤)"),
        (
            "comic_source_address_required",
            r"修改网址|更换域名|设置.*(?:网址|域名)|change.*(?:base url|domain)",
        ),
        (
            "comic_source_login_required",
            r"请.*登[录入]|未登[录入]|需要登[录入]|not logged in|login required|log in first|sign in first|invalid credentials",
        ),
        (
            "comic_source_webview_required",
            r"waiting for webview|webview.*(?:not available|not supported|failed)|webview不可用",
        ),
        (
            "comic_source_verification_required",
            r"captcha|cloudflare|ddos-guard|verify you are human|人机验证|人機驗證|cf-chl-",
        ),
        (
            "comic_source_dns_failed",
            r"unknownhostexception|name or service not known|unable to resolve host|no such host|nodename nor servname",
        ),
        ("comic_source_connection_refused", r"connection refused|connectexception:.*refused"),
        ("comic_source_timeout", r"sockettimeoutexception|timed? out|timeout"),
        (
            "comic_source_tls_failed",
            r"sslhandshakeexception|certificate verify failed|certpathvalidatorexception|sslpeerunverifiedexception",
        ),
        (
            "comic_source_plugin_incompatible",
            r"nosuchmethoderror|noclassdeffounderror|verifyerror|android\..*stub!",
        ),
    )
    for code, pattern in patterns:
        if re.search(pattern, text):
            return code
    statuses = re.findall(
        r"(?:http(?: error| status)?|status(?: code)?|code)\s*[:=]?\s*([45]\d\d)\b", text
    )
    for status in statuses:
        number = int(status)
        if number == 401:
            return "comic_source_login_required"
        if number == 403:
            return "comic_source_access_denied"
        if number == 429:
            return "comic_source_rate_limited"
        if number in {404, 410}:
            return "comic_source_not_found"
        if number >= 500:
            return "comic_source_server_error"
    if re.search(r"服务器错误|伺服器錯誤|service unavailable|internal server error", text):
        return "comic_source_server_error"
    if re.search(
        r"jsondecodingexception|serializationexception|jsonsyntaxexception|expected (?:begin|start)|unexpected json|no value for|selector.*not found|indexoutofboundsexception|nullpointerexception",
        text,
    ):
        return "comic_source_format_changed"
    return "comic_source_unavailable"


STAGES = {
    "connectivity",
    "search",
    "directory",
    "preview",
    "download",
    "package",
    "archive",
    "reader",
}
FACTS = {
    "count",
    "returned",
    "filtered",
    "search_mode",
    "page_count",
    "index",
    "comic_id",
    "chapter_id",
    "job_id",
    "library_kind",
    "cached",
    "http_status",
    "scope",
    "query_count",
    "query_errors",
}


class SourceHealth:
    def __init__(self, comics):
        self.repo = comics.repo

    def record(self, source, stage, code, *, facts=None, observed_at=None):
        if source.get("source_type") == "local":
            return
        if stage not in STAGES:
            raise ValueError("Unknown comic source stage")
        key = source.get("source_key") or source["engine_id"] + ":" + source["source_id"]
        at = self.repo.clock() if observed_at is None else observed_at
        with self.repo.transaction() as db:
            row = self.repo._document(db, "comic_source", key)
            if not row:
                return
            health = dict(row.get("health") or {})
            previous = health.get(stage) or {}
            if previous.get("checked_at", 0) > at:
                return
            record = {k: v for k, v in (facts or {}).items() if k in FACTS}
            record.update(code=code, checked_at=at)
            if code == "ready":
                record["last_success_at"] = at
            elif previous.get("last_success_at"):
                record["last_success_at"] = previous["last_success_at"]
            if code != "ready":
                record.update(last_failure_at=at, last_failure_code=code)
            elif previous.get("last_failure_at"):
                record.update(
                    last_failure_at=previous["last_failure_at"],
                    last_failure_code=previous["last_failure_code"],
                )
            health[stage] = record
            changes = {"health": health}
            if stage == "search":
                changes.update(code=code, checked_at=at, search_checked_at=at)
            elif stage == "download":
                changes.update(download_code=code, download_checked_at=at)
            self.repo.fact("comic_source", key, changes, row["revision"], db=db)
