"""Metadata candidates from the local catalogue, Open Library and configured OPDS."""

import asyncio
import json
from urllib.parse import quote, urljoin
from xml.etree import ElementTree as ET

from ...contracts import Fault
from .documents import plain_text, title_key
from .network import Network, origin

ATOM = "http://www.w3.org/2005/Atom"


def author_text(value):
    values = value if isinstance(value, list) else [value] if value else []
    return ", ".join(
        item.get("name", "") if isinstance(item, dict) else str(item) for item in values
    )


class Metadata:
    def __init__(self, books):
        self.books, self.media, self.repo = books, books.media, books.repo

    async def search(self, book, query=None):
        query = query or book["metadata"].get("isbn") or book["metadata"]["title"]
        candidates, providers = [], []
        rows = await self.media.local(self.repo.list, "book")
        for row in rows:
            if (
                row["book_id"] != book["book_id"]
                and row.get("artifact")
                and title_key(query)
                in title_key(row["metadata"]["title"] + row["metadata"].get("author", ""))
            ):
                candidates.append(
                    {
                        "candidate_id": "local:" + row["book_id"],
                        "provider": "本地书库",
                        "metadata": row["metadata"],
                    }
                )
        settings = await self.media.local(self.books.catalog.settings)
        operations = []
        if settings.get("open_library"):
            operations.append(("Open Library", self.open_library(query)))
        if settings.get("opds_url"):
            operations.append(
                (
                    "OPDS 书库",
                    self.opds(settings["opds_url"], query, self.books.opds_headers(settings)),
                )
            )
        for name, result in zip(
            [name for name, _ in operations],
            await asyncio.gather(
                *(operation for _, operation in operations), return_exceptions=True
            ),
        ):
            if isinstance(result, BaseException):
                providers.append(
                    {
                        "name": name,
                        "code": result.code
                        if isinstance(result, Fault)
                        else "book_metadata_unavailable",
                    }
                )
            else:
                candidates.extend(result)
                providers.append({"name": name, "code": "ready", "count": len(result)})
        return {"candidates": candidates[:50], "providers": providers}

    async def open_library(self, query):
        url = (
            "https://openlibrary.org/search.json?q="
            + quote(query)
            + "&limit=10&fields=key,title,author_name,cover_i,isbn,language,subject"
        )
        response = await asyncio.to_thread(self.books.network.fetch, url)
        payload = json.loads(response.text)
        candidates = []
        for item in payload.get("docs", []):
            fields = {
                "title": item.get("title", ""),
                "author": ", ".join(item.get("author_name", [])),
                "tags": item.get("subject", [])[:10],
            }
            if item.get("cover_i"):
                fields["cover_url"] = (
                    f"https://covers.openlibrary.org/b/id/{int(item['cover_i'])}-L.jpg"
                )
            if query.replace("-", "") in [
                str(value).replace("-", "") for value in item.get("isbn", [])
            ]:
                fields["isbn"] = query
            candidates.append(
                {
                    "candidate_id": "openlibrary:" + str(item.get("key", "")),
                    "provider": "Open Library",
                    "metadata": fields,
                }
            )
        return candidates

    async def opds(self, base_url, query, headers):
        network = Network(allowed_private_origins=[base_url])

        async def fetch(url):
            return await asyncio.to_thread(
                network.fetch, url, headers=headers if origin(url) == origin(base_url) else {}
            )

        response = await fetch(base_url)
        if response.text.lstrip().startswith("{"):
            payload = json.loads(response.text)
            return [
                {
                    "candidate_id": "opds:" + str(index),
                    "provider": "OPDS 书库",
                    "metadata": {
                        "title": entry.get("metadata", {}).get("title", ""),
                        "author": author_text(entry.get("metadata", {}).get("author")),
                        "description": plain_text(entry.get("metadata", {}).get("description", "")),
                    },
                }
                for index, entry in enumerate(payload.get("publications", []))
                if title_key(query) in title_key(entry.get("metadata", {}).get("title", ""))
            ][:20]
        root = ET.fromstring(response.content)
        search_link = next(
            (link for link in root.findall(f"{{{ATOM}}}link") if link.get("rel") == "search"), None
        )
        if search_link is not None:
            target = urljoin(response.url, search_link.get("href", ""))
            if "opensearchdescription" in search_link.get("type", ""):
                search_document = await fetch(target)
                search_root = ET.fromstring(search_document.content)
                templates = [
                    node.get("template")
                    for node in search_root.iter()
                    if node.tag.endswith("}Url") and node.get("template")
                ]
                target = templates[0] if templates else target
            if "{searchTerms}" in target or "{query}" in target or "{?query}" in target:
                target = (
                    target.replace("{searchTerms}", quote(query))
                    .replace("{query}", quote(query))
                    .replace("{?query}", "?query=" + quote(query))
                    .replace("{count?}", "20")
                    .replace("{startIndex?}", "0")
                    .replace("{startPage?}", "1")
                )
                response = await fetch(urljoin(base_url, target))
                root = ET.fromstring(response.content)
        candidates = []
        for entry in root.findall(f"{{{ATOM}}}entry"):
            title = entry.findtext(f"{{{ATOM}}}title", "")
            authors = [
                node.findtext(f"{{{ATOM}}}name", "") for node in entry.findall(f"{{{ATOM}}}author")
            ]
            fields = {
                "title": title,
                "author": ", ".join(authors),
                "description": plain_text(entry.findtext(f"{{{ATOM}}}summary", "")),
            }
            for link in entry.findall(f"{{{ATOM}}}link"):
                if link.get("rel", "").endswith(("/image", "/image/thumbnail")):
                    fields["cover_url"] = urljoin(response.url, link.get("href", ""))
            for node in entry:
                local = node.tag.rsplit("}", 1)[-1]
                if local in {"language", "publisher"}:
                    fields[local] = node.text or ""
                if local == "identifier" and (node.text or "").startswith("urn:isbn:"):
                    fields["isbn"] = node.text.removeprefix("urn:isbn:")
            if title_key(query) in title_key(title + " ".join(authors) + fields.get("isbn", "")):
                candidates.append(
                    {
                        "candidate_id": "opds:"
                        + entry.findtext(f"{{{ATOM}}}id", str(len(candidates))),
                        "provider": "OPDS 书库",
                        "metadata": fields,
                    }
                )
        return candidates[:20]

    async def enrich(self, book):
        settings = await self.media.local(self.books.catalog.settings)
        if not settings.get("auto_metadata"):
            return book
        metadata = book["metadata"]
        if metadata.get("author") and metadata.get("description") and metadata.get("cover_url"):
            return book
        result = await self.search(book)
        exact = [
            candidate
            for candidate in result["candidates"]
            if title_key(candidate["metadata"].get("title")) == title_key(metadata["title"])
            and metadata.get("author")
            and title_key(candidate["metadata"].get("author")) == title_key(metadata["author"])
        ]
        if len(exact) != 1:
            return book
        candidate = exact[0]

        def apply():
            with self.repo.transaction() as db:
                current = self.books.catalog.book(book["book_id"], db)
                updated = dict(current["metadata"])
                field_sources = dict(current.get("field_sources", {}))
                for key in ("description", "cover_url", "tags", "language"):
                    if (
                        not updated.get(key)
                        and key not in current.get("manual_fields", [])
                        and candidate["metadata"].get(key)
                    ):
                        updated[key] = candidate["metadata"][key]
                        field_sources[key] = candidate["provider"]
                if updated != current["metadata"]:
                    return self.repo.put(
                        "book",
                        current["book_id"],
                        {
                            **current,
                            "metadata": updated,
                            "metadata_revision": current.get("metadata_revision", 1) + 1,
                            "field_sources": field_sources,
                        },
                        db=db,
                    )
                return current

        return await self.media.local(apply)
