"""Single bounded rule operation; no platform database or credential environment."""

import contextlib
import io
import json
from pathlib import Path
import sys

from ...contracts import Fault, require
from . import engine_http
from .network import Network
from .readability import check_content


def run(request):
    sys.path.insert(0, str(Path(__file__).parent / "_vendor"))
    from LegadoParser2.RuleCompile import compileBookSource
    from LegadoParser2.Search import search
    from LegadoParser2.BookInfo import getBookInfo
    from LegadoParser2.ChapterList import getChapterList
    from LegadoParser2.Chapter import getChapterContent

    source = dict(request["source"])
    engine_http.configure_rate(source.get("concurrentRate"), request.get("rate_state", {}))
    engine_http.network = Network(
        allowed_private_origins=request.get("allowed_private_origins", ()),
        before_request=engine_http.before_request,
    )
    source["_tianshu_book"] = request.get("book", {})
    for key in ("ruleSearch", "ruleBookInfo", "ruleToc", "ruleContent", "ruleExplore"):
        source.setdefault(key, {})
    compiled = compileBookSource(source)
    operation = request["operation"]
    if operation == "search":
        return search(compiled, request["query"], request.get("page", 1))[:200]
    variables = request.get("variables", {})
    if operation == "detail":
        return getBookInfo(compiled, request["url"], variables)
    if operation == "chapters":
        result = getChapterList(compiled, request["url"], variables)
        require(len(result) <= 20000, "book_directory_invalid", 502)
        return result
    if operation == "content":
        require(not source["ruleContent"].get("webJs"), "book_rule_capability_unsupported", 409)
        result = getChapterContent(compiled, request["url"], variables, request.get("next_url", ""))
        require(
            isinstance(result, dict) and result.get("content", "").strip(),
            "book_content_empty",
            502,
        )
        check_content(result["content"])
        return result
    raise Fault("invalid_input", 400)


def main():
    try:
        raw = sys.stdin.buffer.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise Fault("book_rule_too_large", 413)
        request = json.loads(raw)
        with contextlib.redirect_stdout(io.StringIO()):
            result = run(request)
        output = json.dumps(
            {"ok": True, "data": result, "rate_state": engine_http.rate_state()},
            ensure_ascii=False,
        )
        if len(output.encode("utf-8")) > 8 * 1024 * 1024:
            raise Fault("book_response_too_large", 413)
    except Fault as error:
        output = json.dumps(
            {"ok": False, "code": error.code, "rate_state": engine_http.rate_state()}
        )
    except Exception:
        output = json.dumps(
            {"ok": False, "code": "book_rule_failed", "rate_state": engine_http.rate_state()}
        )
    sys.stdout.buffer.write(output.encode("utf-8"))


if __name__ == "__main__":
    main()
