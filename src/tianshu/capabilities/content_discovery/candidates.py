"""Explicit metadata identities; a search snippet never proves a body was read."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import html
import math
import re
from urllib.parse import urlsplit, urlunsplit

from jsonschema import Draft202012Validator, ValidationError

from ..contracts import Fault, digest, require
from .schemas import OUTPUT

VALIDATOR = Draft202012Validator(OUTPUT["properties"]["candidates"]["items"])


def public_url(value):
    require(isinstance(value, str) and len(value) <= 4096, "invalid_input", 400)
    try:
        parts = urlsplit(value)
        require(
            parts.scheme.lower() in {"http", "https"}
            and parts.hostname
            and not parts.username
            and not parts.password
            and not any(ord(char) < 32 for char in value),
            "invalid_input",
            400,
        )
        host = parts.hostname.lower()
        host = "[" + host + "]" if ":" in host else host.encode("idna").decode()
        port = parts.port
        if port and (parts.scheme.lower(), port) not in {("http", 80), ("https", 443)}:
            host += ":" + str(port)
        # Query ordering, tracking-like parameters and fragments can change what
        # a site displays. Keep them rather than guessing semantic equivalence.
        return urlunsplit(
            (parts.scheme.lower(), host, parts.path or "/", parts.query, parts.fragment)
        )
    except (ValueError, UnicodeError):
        require(False, "invalid_input", 400)


def text(value, maximum):
    value = value if isinstance(value, str) else ""
    plain = html.unescape(re.sub(r"<[^>]*>", "", value)).strip()
    return plain[:maximum], len(plain) > maximum


def timestamp(value):
    if type(value) in (int, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str) and len(value) <= 128:
        try:
            datetime.fromisoformat(value)
            return value
        except ValueError:
            try:
                parsedate_to_datetime(value)
                return value
            except (ValueError, TypeError, OverflowError):
                pass
    return None


def candidate(
    source,
    key,
    *,
    kind,
    title,
    authors=(),
    summary="",
    url=None,
    owner_id=None,
    source_key=None,
    version=None,
    observed=None,
    published=None,
    acquired=0,
    summary_kind="catalog_description",
):
    title, title_cut = text(title, 512)
    summary, summary_cut = text(summary, 2000)
    names = [text(name, 160)[0] for name in authors if isinstance(name, str) and name.strip()]
    names = list(dict.fromkeys(names))[:8]
    metadata = {
        "title": title or None,
        "authors": names,
        "summary": summary,
        "summary_kind": summary_kind,
        "text_truncated": title_cut or summary_cut,
        "published_at": timestamp(published),
    }
    locator = {
        "owner": "knowledge" if source == "web" else "media",
        "kind": kind,
        "id": owner_id,
        "source_key": source_key,
        "url": url,
    }
    metadata_digest = digest({"metadata": metadata, "locator": locator, "owner_version": version})
    result = {
        "content_key": key,
        "candidate_key": "candidate:" + digest([key, version, metadata_digest]),
        "source": source,
        "kind": kind,
        **metadata,
        "owner_version": str(version) if version is not None else None,
        "metadata_digest": metadata_digest,
        "discovered_at": datetime.fromtimestamp(acquired, timezone.utc).isoformat(),
        "source_observed_at": timestamp(observed),
        "read_locator": locator,
        "readability": "unverified" if source == "web" else "adapter_required",
        "seen_state": "unknown",
    }
    try:
        VALIDATOR.validate(result)
    except ValidationError:
        raise Fault("invalid_input", 400) from None
    return result


def select(groups, query, excluded, maximum):
    result, seen = [], set()
    excluded = set(excluded)
    omitted = 0
    for rows in groups:
        for row in rows:
            if row["content_key"] in excluded or row["candidate_key"] in excluded:
                continue
            # Only a stable owner identity or exactly normalized URL is evidence
            # of duplication. Equal titles are not enough to merge reprints.
            if row["source"] != "web" and query:
                haystack = " ".join(
                    [row["title"] or "", *row["authors"], row["summary"]]
                ).casefold()
                if query.casefold() not in haystack:
                    continue
            if row["content_key"] in seen:
                continue
            seen.add(row["content_key"])
            if len(result) < maximum:
                result.append(row)
            else:
                omitted += 1
    return result, omitted
