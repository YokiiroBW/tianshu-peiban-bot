"""Regex worker: the only process that ever compiles or runs a user-supplied expression.

This file is started by :mod:`tianshu.capabilities.media.rules.regex_process` as
``sys.executable -I -u <absolute path to this file>``. It is deliberately the smallest possible
program:

* it imports the standard library only (``json``, ``os``, ``re``, ``sys``) — no platform
  configuration, no server package, no network client, no filesystem access, no clock;
* it speaks one JSON object per line on stdin/stdout, in **UTF-8 on both directions**, never in the
  host's locale encoding: the parent writes UTF-8 bytes and the child decodes them as UTF-8, so a CJK
  or emoji pattern means the same thing on every host. ``PYTHONUTF8``/``PYTHONIOENCODING`` are not
  relied on: the child is started with ``-I``, which ignores them;
* it never writes source text anywhere else;
* its only secrets are the ones handed to it on that pipe, and it holds no compiled pattern after a
  ``forget`` command.

The hard budgets (3 s handshake, 50 ms per call, 5 s per request) belong to the parent, which kills
this process when one of them expires: ``re`` cannot be interrupted from inside, so the parent's
``terminate``/``kill`` *is* the timeout mechanism.

Protocol (one response line per request line, ``seq`` echoed verbatim):

* ``{"seq": N, "op": "compile", "pattern": P, "flags": F}`` →
  ``{"seq": N, "ok": true, "handle": N}`` or ``{"seq": N, "ok": false, "code": "invalid_regex"}``
* ``{"seq": N, "op": "search", "handle": H, "text": T}`` →
  ``{"seq": N, "ok": true, "matched": bool}`` or ``{"seq": N, "ok": false, "code": "unknown_handle"}``
* ``{"seq": N, "op": "forget", "handle": H}`` → ``{"seq": N, "ok": true}``

A malformed line is refused with ``{"seq": null, "ok": false, "code": "invalid_request"}`` and never
echoes its content. Closing stdin ends the loop and the process exits 0.
"""

from __future__ import annotations

import json
import os
import re
import sys

PATTERN_MAX = 512
FLAGS_MAX = 0x7FFFFFFF


def configure_stdio() -> None:
    """Pin both directions to UTF-8, independently of the host locale.

    The parent encodes a request as UTF-8 and the protocol says the child answers in UTF-8, so the
    locale must not decide what a pattern means. ``errors="strict"`` is deliberate: a byte sequence
    that is not UTF-8 is a broken peer, not text to be repaired, and ``surrogateescape`` would put
    unencodable lone surrogates into a compiled pattern.
    """

    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="strict")


def _send(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _refuse(seq: object, code: str) -> None:
    _send({"seq": seq, "ok": False, "code": code})


def _compile(command: dict[str, object], seq: object, cache: dict[int, re.Pattern[str]]) -> None:
    pattern = command.get("pattern")
    flags = command.get("flags")
    handle = command.get("seq")
    if not isinstance(pattern, str) or len(pattern) > PATTERN_MAX:
        _refuse(seq, "invalid_request")
        return
    if type(flags) is not int or not 0 <= flags <= FLAGS_MAX:
        _refuse(seq, "invalid_request")
        return
    try:
        compiled = re.compile(pattern, flags)
    except re.error:
        # The expression itself is never echoed: the caller only needs to know that it is refused.
        _refuse(seq, "invalid_regex")
        return
    if type(handle) is not int:
        _refuse(seq, "invalid_request")
        return
    cache[handle] = compiled
    _send({"seq": seq, "ok": True, "handle": handle})


def _search(command: dict[str, object], seq: object, cache: dict[int, re.Pattern[str]]) -> None:
    handle = command.get("handle")
    text = command.get("text")
    if type(handle) is not int or not isinstance(text, str):
        _refuse(seq, "invalid_request")
        return
    compiled = cache.get(handle)
    if compiled is None:
        _refuse(seq, "unknown_handle")
        return
    _send({"seq": seq, "ok": True, "matched": compiled.search(text) is not None})


def _forget(command: dict[str, object], seq: object, cache: dict[int, re.Pattern[str]]) -> None:
    handle = command.get("handle")
    if type(handle) is not int:
        _refuse(seq, "invalid_request")
        return
    cache.pop(handle, None)
    _send({"seq": seq, "ok": True})


def _duplicate_free(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build a request object, refusing a key that appears twice.

    ``json.loads`` keeps the last value of a repeated key, so ``{"seq": 1, "seq": 2, ...}`` would be
    silently reinterpreted. Two different facts under one key mean the peer is not speaking the frozen
    protocol, and the line is refused rather than half-accepted.
    """

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def handle_line(line: str, cache: dict[int, re.Pattern[str]]) -> None:
    try:
        command = json.loads(line, object_pairs_hook=_duplicate_free)
    except (ValueError, RecursionError):
        # ``RecursionError`` is not a ``ValueError``: deeply nested input that is far below any size
        # limit — 5000 nested arrays fit in a few kilobytes — makes the parser blow its own stack. That
        # is a line this worker cannot read, exactly like malformed JSON, so it is refused by name
        # rather than crashing the child on an unhandled exception the parent can only observe as EOF.
        _refuse(None, "invalid_request")
        return
    if not isinstance(command, dict):
        _refuse(None, "invalid_request")
        return
    seq = command.get("seq")
    if type(seq) is not int or seq <= 0:
        # A sequence number that is not a real positive integer cannot be echoed faithfully, and a
        # response the parent cannot match would be a protocol violation.
        _refuse(None, "invalid_request")
        return
    operation = command.get("op")
    if operation == "compile":
        _compile(command, seq, cache)
    elif operation == "search":
        _search(command, seq, cache)
    elif operation == "forget":
        _forget(command, seq, cache)
    else:
        _refuse(seq, "invalid_request")


def main() -> int:
    configure_stdio()
    cache: dict[int, re.Pattern[str]] = {}
    _send({"ready": True, "protocol": 1, "pid": os.getpid()})
    while True:
        try:
            line = sys.stdin.readline()
        except UnicodeDecodeError:
            # The line was not valid UTF-8 and nothing of it can be trusted; refuse without echoing.
            _send({"seq": None, "ok": False, "code": "invalid_request"})
            continue
        if not line:
            return 0
        handle_line(line, cache)


if __name__ == "__main__":  # pragma: no cover - exercised as a real subprocess
    sys.exit(main())
