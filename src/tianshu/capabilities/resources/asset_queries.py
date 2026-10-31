"""Asset-page read rules: what a browser may ask for, and what may be shown back.

This module is the single owner of the asset page's non-transport rules. It knows nothing about
HTTP, sessions, CSRF, aiohttp or the console: it takes one already-authenticated page request as a
plain mapping, normalizes it into the peer's own published read body, validates the peer's answer,
and projects that answer into the browser's redacted shape. The route adapter
(`services.platform.web_assets`) calls it; nothing calls back the other way.

Rules that live only here:

* the operation whitelist is exactly the five published reads, and no other name is forwarded;
* every identifier, query, path, page size and sorting option is bounded and validated;
* a continuation belongs to the connection, library, scope, query and sorting that produced it,
  so a cursor can never be replayed onto a different listing;
* a relative path is only ever relative: no root, drive letter, traversal or control character;
* the peer's answer must agree with what was asked for (same library, same entry, a hit that
  belongs to the library it names), otherwise it is an invalid upstream answer, not a page;
* a peer code this product does not own becomes one honest `upstream_error`.

Everything here is pure: no clock, no network, no store, no global state. That is what makes the
rules directly testable without a server, and it is why the adapter can stay about transport.
"""

import base64
import re

from ..contracts import Fault, canonical, digest, loads, require

# The five published reads this page may reach; nothing else exists in this vocabulary.
OPERATIONS = ("libraries.list", "libraries.get", "entries.browse", "entries.get", "assets.search")
PAGE_SCOPES = ("libraries", "browse", "search")
CATEGORIES = (
    "photos",
    "images",
    "videos",
    "music",
    "projects",
    "documents",
    "characters",
    "general",
)
ACCESS_LEVELS = ("read_only", "read_write", "organize", "library_administrator")
AVAILABILITY = ("online", "offline")
ENTRY_KINDS = ("file", "directory", "reparse_file", "reparse_directory")
DIRECTORY_KINDS = ("directory", "reparse_directory")
HIT_REASONS = ("name", "path")
SORT_BY = ("name", "modified", "size")
DIRECTIONS = ("asc", "desc")
KIND_FILTERS = ("all", "files", "directories")
SEARCH_SCOPES = ("all", "library", "directory")
CURSOR_VERSION = 1
CURSOR_LIMIT = 4096
IDENTIFIER_LIMIT = 256
QUERY_LIMIT = 200
NAME_FILTER_LIMIT = 200
PATH_LIMIT = 4096
NAME_LIMIT = 512
LIBRARY_NAME_LIMIT = 200
DEFAULT_PAGE = 50
MAX_PAGE = 100
# Peer codes this page may repeat. Anything else becomes one honest `upstream_error` instead of
# echoing a vocabulary this product does not own.
KNOWN_CODES = {
    "not_found",
    "forbidden",
    "unauthorized",
    "invalid_input",
    "budget_exceeded",
    "deadline_exceeded",
    "dependency_unavailable",
    "invalid_upstream",
    "unsupported_operation",
}
# A page cannot invent a media port. The published AssetLink read protocol has none, so the only
# honest statement is that this deployment has no preview and no download through this page.
PREVIEW = {
    "available": False,
    "code": "asset_media_port_absent",
    "reason": "现行资产只读协议没有预览或下载端口，这里只显示索引元数据。",
}


def text(value, limit, code="invalid_input", status=400):
    require(isinstance(value, str) and 0 < len(value) <= limit, code, status)
    require(all(ord(char) >= 32 and char != "\x7f" for char in value), code, status)
    return value


def relative(value):
    """A relative path stays relative: no root, drive letter, traversal or control character."""
    require(isinstance(value, str) and len(value) <= PATH_LIMIT, "invalid_input", 400)
    require(
        not value.startswith(("/", "\\"))
        and re.match(r"^[A-Za-z]:", value) is None
        and not any(part in (".", "..") for part in re.split(r"[\\/]+", value) if part)
        and all(ord(char) >= 32 and char != "\x7f" for char in value),
        "invalid_input",
        400,
    )
    return value


def size(body):
    value = body.get("page_size", DEFAULT_PAGE)
    require(type(value) is int and 1 <= value <= MAX_PAGE, "invalid_input", 400)
    return value


def failure(code, status):
    """One honest word out of a peer failure; an unknown peer code is not echoed."""
    return (code, status) if code in KNOWN_CODES else ("upstream_error", 502)


class Scope:
    """The identity a continuation belongs to: one page of one connection, for one identity."""

    def __init__(self, principal, connection_id, page):
        self.principal = principal
        self.connection_id = connection_id
        self.page = page

    def key(self, options):
        return digest(
            {
                "principal": self.principal,
                "connection": self.connection_id,
                "page": self.page,
                # The reading identity is part of that identity too: another identity's
                # continuation is a different query, not a resumable one.
                "options": options,
            }
        )

    def encode(self, upstream, options):
        document = {
            "v": CURSOR_VERSION,
            "upstream": upstream,
            "filter": self.key(options),
        }
        return base64.urlsafe_b64encode(canonical(document).encode()).decode("ascii")

    def decode(self, value, options):
        """Decode one continuation, refusing a replay against a different scope or query."""
        if value is None:
            return None
        require(isinstance(value, str) and 0 < len(value) <= CURSOR_LIMIT, "invalid_input", 400)
        try:
            raw = base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
        except (ValueError, UnicodeError):
            raise Fault("invalid_input", 400) from None
        document = loads(raw)
        require(
            isinstance(document, dict)
            and set(document) == {"v", "upstream", "filter"}
            and document["v"] == CURSOR_VERSION
            and isinstance(document["upstream"], str)
            and 0 < len(document["upstream"]) <= CURSOR_LIMIT,
            "invalid_input",
            400,
        )
        require(document["filter"] == self.key(options), "cursor_conflict", 409)
        return document["upstream"]


def libraries_request(body, scope):
    """`libraries.list`, optionally filtered by the peer's own category vocabulary.

    Returns the peer's own read body together with the identity of the continuation that body may
    carry: exactly the options that selected it, and nothing the adapter has to reconstruct.
    """
    require(set(body) <= {"page_size", "cursor", "category"}, "invalid_input", 400)
    category = body.get("category")
    require(category is None or category in CATEGORIES, "invalid_input", 400)
    requested = size(body)
    options = canonical({"page": "libraries", "category": category})
    cursor = scope.decode(body.get("cursor"), options)
    request = {"page_size": requested}
    if cursor is not None:
        request["cursor"] = cursor
    if category is not None:
        request["category"] = category
    return request, options


def browse_request(body, scope):
    """`entries.browse`: one library directory, with the peer's reviewed paging options."""
    require(
        set(body)
        <= {
            "library_id",
            "parent_relative_path",
            "page_size",
            "cursor",
            "sort_by",
            "sort_direction",
            "kind",
            "name_filter",
            "anchor_entry_id",
        },
        "invalid_input",
        400,
    )
    require("library_id" in body, "invalid_input", 400)
    library_id = text(body["library_id"], IDENTIFIER_LIMIT)
    parent = relative(body.get("parent_relative_path", ""))
    sort_by = body.get("sort_by", "name")
    direction = body.get("sort_direction", "asc")
    kind = body.get("kind", "all")
    require(
        sort_by in SORT_BY and direction in DIRECTIONS and kind in KIND_FILTERS,
        "invalid_input",
        400,
    )
    name_filter = body.get("name_filter", "")
    require(
        isinstance(name_filter, str) and len(name_filter) <= NAME_FILTER_LIMIT,
        "invalid_input",
        400,
    )
    anchor = body.get("anchor_entry_id")
    require(
        anchor is None or (isinstance(anchor, str) and 0 < len(anchor) <= IDENTIFIER_LIMIT),
        "invalid_input",
        400,
    )
    # Every option that selects or orders the page belongs to the continuation's identity, so a
    # cursor can never be replayed onto a differently filtered, sorted or scoped listing. The page
    # size is deliberately not part of that identity: a shorter page is the same listing read in
    # smaller steps, and `slice_page` refuses any answer that would be truncated instead.
    options = canonical(
        {
            "page": "browse",
            "library": library_id,
            "parent": parent,
            "sort_by": sort_by,
            "sort_direction": direction,
            "kind": kind,
            "name_filter": name_filter,
        }
    )
    cursor = scope.decode(body.get("cursor"), options)
    require(not (cursor is not None and anchor is not None), "invalid_input", 400)
    # The reviewed defaults are the peer's own defaults: an omitted option is omitted on the wire
    # instead of being restated with a value this page guessed.
    request = {"library_id": library_id, "parent_relative_path": parent, "page_size": size(body)}
    for field, value in (
        ("sort_by", sort_by),
        ("sort_direction", direction),
        ("kind", kind),
        ("name_filter", name_filter),
    ):
        if body.get(field) is not None:
            request[field] = value
    if cursor is not None:
        request["cursor"] = cursor
    if anchor is not None:
        request["anchor_entry_id"] = anchor
    return request, options


def search_request(body, scope):
    """`assets.search`: one explicit query, with the peer's own scope vocabulary."""
    require(
        set(body)
        <= {"query", "page_size", "cursor", "scope", "library_id", "parent_relative_path"},
        "invalid_input",
        400,
    )
    query = body.get("query")
    require(isinstance(query, str) and 0 < len(query.strip()) <= QUERY_LIMIT, "invalid_input", 400)
    # The query goes out as the peer's own literal containment term: no wildcard translation, no
    # escaping rewrite, no pattern this page invented, and no control character at all.
    require(not any(char < " " or char == "\x7f" for char in query), "invalid_input", 400)
    page_scope = body.get("scope", "all")
    require(page_scope in SEARCH_SCOPES, "invalid_input", 400)
    library_id = body.get("library_id")
    parent = body.get("parent_relative_path")
    if page_scope == "all":
        # An unscoped search never carries a library or a directory; the peer's own scope
        # vocabulary decides, and a contradictory request is refused here.
        require(library_id is None and parent is None, "invalid_input", 400)
        target = None
    else:
        require("library_id" in body, "invalid_input", 400)
        target = text(library_id, IDENTIFIER_LIMIT)
    if page_scope == "directory":
        require("parent_relative_path" in body, "invalid_input", 400)
        parent = relative(parent)
    else:
        require(parent is None, "invalid_input", 400)
        parent = None
    options = canonical(
        {
            "page": "search",
            "query": query,
            "scope": page_scope,
            "library": target,
            "parent": parent,
        }
    )
    request = {
        "query": query,
        "page_size": size(body),
        "scope": page_scope,
    }
    if target is not None:
        request["library_id"] = target
    if page_scope == "directory":
        request["parent_relative_path"] = parent
    cursor = scope.decode(body.get("cursor"), options)
    if cursor is not None:
        request["cursor"] = cursor
    return request, options


def entry_request(body):
    """`entries.get`: exactly one library and one entry identifier, nothing else."""
    require(set(body) == {"library_id", "entry_id"}, "invalid_input", 400)
    return {
        "library_id": text(body["library_id"], IDENTIFIER_LIMIT),
        "entry_id": text(body["entry_id"], IDENTIFIER_LIMIT),
    }


def library(value):
    """One peer library, with the peer's own index state and no claim about the originals."""
    require(isinstance(value, dict), "invalid_upstream", 502)
    availability = value.get("availability")
    require(availability in AVAILABILITY, "invalid_upstream", 502)
    access = value.get("access_level")
    require(access in ACCESS_LEVELS, "invalid_upstream", 502)
    # An older peer may omit `category`; the published default for that case is `general`, and an
    # unknown explicit value is a real protocol error rather than something to normalize.
    category = value.get("category", "general")
    require(category in CATEGORIES, "invalid_upstream", 502)
    return {
        "library_id": text(value.get("library_id"), IDENTIFIER_LIMIT, "invalid_upstream", 502),
        "display_name": text(
            value.get("display_name"), LIBRARY_NAME_LIMIT, "invalid_upstream", 502
        ),
        "availability": availability,
        "access_level": access,
        "category": category,
        # The peer's own word for the index, never a claim about the physical originals.
        "index_available": availability == "online",
        "original_available": "not_verified",
        "preview": dict(PREVIEW),
    }


def entry(value):
    """One peer entry; sizes are canonical decimal strings in this protocol, not numbers."""
    require(isinstance(value, dict), "invalid_upstream", 502)
    kind = value.get("kind")
    require(kind in ENTRY_KINDS, "invalid_upstream", 502)
    length = value.get("content_length")
    require(
        length is None
        or (isinstance(length, str) and re.fullmatch(r"(0|[1-9][0-9]{0,19})", length) is not None),
        "invalid_upstream",
        502,
    )
    modified = value.get("last_write_time_utc")
    require(isinstance(modified, str) and 0 < len(modified) <= 64, "invalid_upstream", 502)
    return {
        "entry_id": text(value.get("entry_id"), IDENTIFIER_LIMIT, "invalid_upstream", 502),
        "library_id": text(value.get("library_id"), IDENTIFIER_LIMIT, "invalid_upstream", 502),
        "relative_path": text(value.get("relative_path"), PATH_LIMIT, "invalid_upstream", 502),
        "name": text(value.get("name"), NAME_LIMIT, "invalid_upstream", 502),
        "kind": kind,
        "directory": kind in DIRECTORY_KINDS,
        "content_length": length,
        "last_write_time_utc": modified,
        # An index row is metadata about a path; whether the original bytes are reachable is not
        # something this protocol answers, so the page never guesses it.
        "original_available": "not_verified",
        "preview": dict(PREVIEW),
    }


def hit(value):
    """One search hit; a hit that names another library than its entry is not a hit."""
    require(isinstance(value, dict), "invalid_upstream", 502)
    reason = value.get("hit_reason")
    require(reason in HIT_REASONS, "invalid_upstream", 502)
    found = library(value.get("library"))
    item = entry(value.get("entry"))
    require(item["library_id"] == found["library_id"], "invalid_upstream", 502)
    return {"library": found, "entry": item, "hit_reason": reason}


def slice_page(result, requested, scope, options, decode):
    """One bounded page: complete or refused, never a truncated listing shown as complete.

    The continuation this page hands back is bound to the very options that produced it, so it can
    only ever continue that same listing for that same identity.
    """
    require(isinstance(result, dict), "invalid_upstream", 502)
    items = result.get("items")
    require(isinstance(items, list) and len(items) <= requested, "invalid_upstream", 502)
    cursor = result.get("next_cursor")
    require(
        cursor is None or (isinstance(cursor, str) and 0 < len(cursor) <= CURSOR_LIMIT),
        "invalid_upstream",
        502,
    )
    # A continuation only means "there is more than this page": the page itself is whole, and a
    # listing that silently dropped rows is refused rather than shown as a complete directory.
    require(cursor is None or len(items) == requested, "invalid_upstream", 502)
    decoded = [decode(item) for item in items]
    return {
        "items": decoded,
        "page": {
            "size": requested,
            "returned": len(decoded),
            "has_more": cursor is not None,
            "next_cursor": scope.encode(cursor, options) if cursor is not None else None,
        },
    }


def echo(value, fallback):
    """The peer's own echo of the parent path, or the one this page asked for."""
    if value is None:
        return fallback
    require(
        isinstance(value, str)
        and len(value) <= PATH_LIMIT
        and all(ord(char) >= 32 and char != "\x7f" for char in value),
        "invalid_upstream",
        502,
    )
    return value
