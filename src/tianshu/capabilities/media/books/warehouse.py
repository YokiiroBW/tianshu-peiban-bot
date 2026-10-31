"""Read yckceo's public catalogue as native, inert book-source entries."""

import asyncio
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from lxml import etree, html

from ...contracts import Fault, require
from .network import origin

KINDS = {"source": "shuyuan", "collection": "shuyuans"}
ENTRY_PATH = re.compile(r"/yuedu/(shuyuan|shuyuans)/(?:content/id/(\d+)\.html|json/id/(\d+)\.json)")


def text(node, limit=1000):
    return " ".join(node.text_content().split())[:limit] if node is not None else ""


class Warehouse:
    def __init__(self, books):
        self.books = books
        self.base_url = "https://www.yckceo.com"

    def entry(self, kind, identity):
        require(isinstance(kind, str) and kind in KINDS, "invalid_input", 400)
        require(
            isinstance(identity, str) and re.fullmatch(r"[1-9]\d{0,9}", identity),
            "invalid_input",
            400,
        )
        path = f"/yuedu/{KINDS[kind]}"
        return {
            "kind": kind,
            "entry_id": identity,
            "key": f"{kind}:{identity}",
            "page_url": f"{self.base_url}{path}/content/id/{identity}.html",
            "url": f"{self.base_url}{path}/json/id/{identity}.json",
        }

    def normalize(self, value):
        """Accept durable JSON, detail links and Reader's import-online scheme."""
        require(isinstance(value, str) and 0 < len(value.strip()) <= 8192, "book_invalid_url", 400)
        value = value.strip()
        try:
            parsed = urlsplit(value)
        except ValueError:
            raise Fault("book_invalid_url", 400) from None
        if parsed.scheme == "yuedu":
            require(
                parsed.netloc == "booksource" and parsed.path == "/importonline",
                "book_invalid_url",
                400,
            )
            values = parse_qs(parsed.query).get("src", [])
            require(len(values) == 1, "book_invalid_url", 400)
            value = values[0]
        location = origin(value)
        parsed = urlsplit(value)
        known = location == origin(self.base_url) or (
            location[1] in {"www.yckceo.com", "yckceo.com"} and location[2] in {80, 443}
        )
        if not known:
            return value, None
        if location[1] not in {"www.yckceo.com", "yckceo.com"} and not parsed.path.startswith(
            "/yuedu/"
        ):
            return value, None
        if parsed.path == "/yuedu/shuyuan/jsons":
            ids = parse_qs(parsed.query).get("id", [])
            require(
                len(ids) == 1 and re.fullmatch(r"[1-9]\d{0,9}(?:-[1-9]\d{0,9})*", ids[0]),
                "book_invalid_url",
                400,
            )
            return self.base_url + parsed.path + "?" + urlencode({"id": ids[0]}), None
        match = ENTRY_PATH.fullmatch(parsed.path)
        require(match, "book_warehouse_choose_entry", 400)
        kind = "source" if match[1] == "shuyuan" else "collection"
        entry = self.entry(kind, match[2] or match[3])
        return entry["url"], entry

    async def document(self, url):
        response = await asyncio.to_thread(self.books.network.fetch, url, timeout=25)
        require(origin(response.url) == origin(self.base_url), "book_warehouse_changed", 502)
        try:
            document = html.fromstring(response.text)
        except (etree.ParserError, ValueError):
            raise Fault("book_warehouse_changed", 502) from None
        for node in document.xpath("//script|//style|//pre|//noscript"):
            node.drop_tree()
        return document

    async def browse(self, body):
        kind = body.get("kind", "source")
        query = body.get("query", "")
        page = body.get("page", 1)
        order = body.get("order", "time")
        require(
            isinstance(kind, str) and kind in KINDS and order in ("time", "down"),
            "invalid_input",
            400,
        )
        require(isinstance(query, str) and len(query) <= 200, "invalid_input", 400)
        require(type(page) is int and 1 <= page <= 10000, "invalid_input", 400)
        path = f"/yuedu/{KINDS[kind]}/index.html"
        parameters = {"page": page, "keys": query.strip(), "order1": order, "order2": "1"}
        if kind == "source":
            parameters["ver"] = "3"
        url = self.base_url + path + "?" + urlencode(parameters)
        document = await self.document(url)
        require(document.cssselect("#navsub"), "book_warehouse_changed", 502)
        entries = []
        seen = set()
        for row in document.cssselect(".ylist"):
            links = row.cssselect("h2 a[href]")
            if not links:
                continue
            try:
                _, entry = self.normalize(urljoin(url, links[0].get("href")))
            except Fault:
                continue
            if not entry or entry["kind"] != kind or entry["key"] in seen:
                continue
            seen.add(entry["key"])
            title = text(links[0], 512)
            entry["title"] = re.split(r"\s+https?://", title, maxsplit=1)[0]
            stamps = row.cssselect("h2 p")
            entry["updated"] = text(stamps[0], 100) if stamps else ""
            entry["badges"] = [text(node, 160) for node in row.cssselect("span") if text(node)]
            entries.append(entry)
        has_next = False
        for link in document.cssselect(".layui-laypage a[href]"):
            target = urlsplit(urljoin(url, link.get("href")))
            pages = parse_qs(target.query).get("page", [])
            if target.path == path and pages and pages[0].isdigit() and int(pages[0]) > page:
                has_next = True
        return {"entries": entries, "page": page, "has_next": has_next, "page_url": url}

    async def detail(self, body):
        if "url" in body:
            _, entry = self.normalize(body["url"])
            require(entry, "book_warehouse_choose_entry", 400)
        else:
            entry = self.entry(body.get("kind"), body.get("entry_id"))
        document = await self.document(entry["page_url"])
        titles, links = document.cssselect("h1"), document.cssselect("input#jsonurl")
        require(titles and links, "book_warehouse_changed", 502)
        _, linked = self.normalize(links[0].get("value", ""))
        require(linked and linked["key"] == entry["key"], "book_warehouse_changed", 502)
        for child in titles[0].cssselect("p"):
            child.drop_tree()
        descriptions = document.cssselect(".contents")
        facts = document.cssselect("blockquote p")
        return {
            **entry,
            "title": text(titles[0], 512),
            "description": "\n".join(text(node, 6000) for node in descriptions)[:6000],
            "badges": [text(node, 300) for node in facts if text(node)],
        }

    def selection(self, values):
        require(isinstance(values, list) and 0 < len(values) <= 100, "invalid_input", 400)
        entries = {}
        for value in values:
            require(isinstance(value, dict), "invalid_input", 400)
            entry = self.entry(value.get("kind"), value.get("entry_id"))
            title = value.get("title", "")
            require(isinstance(title, str) and len(title) <= 512, "invalid_input", 400)
            entries[entry["key"]] = {**entry, "title": " ".join(title.split())}
        return list(entries.values())
