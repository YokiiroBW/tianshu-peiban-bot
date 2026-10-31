"""Self-contained plugin views served as opaque, script-enabled iframe documents."""

import base64
import json
from html import escape
from html.parser import HTMLParser
import mimetypes
from pathlib import Path
import re

from ..contracts import require
from .packages import relative_path

POLICY = "sandbox allow-scripts allow-forms; default-src 'none'; script-src 'unsafe-inline' data:; style-src 'unsafe-inline' data:; img-src data: blob:; font-src data:; connect-src 'none'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
import tianshu_plugin
BRIDGE = Path(tianshu_plugin.__file__).parent / "view_bridge.js"


class AssetsHTML(HTMLParser):
    def __init__(self, asset):
        super().__init__(convert_charrefs=False)
        self.asset, self.output = asset, []

    def handle_starttag(self, tag, attrs):
        attributes = []
        for name, value in attrs:
            if value is not None and name in {"src", "href"}:
                value = self.asset(value)
            attributes.append(
                name if value is None else name + '="' + escape(value, quote=True) + '"'
            )
        self.output.append("<" + tag + (" " if attributes else "") + " ".join(attributes) + ">")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        self.output.append("</" + tag + ">")

    def handle_data(self, value):
        self.output.append(value)

    def handle_entityref(self, value):
        self.output.append("&" + value + ";")

    def handle_charref(self, value):
        self.output.append("&#" + value + ";")

    def handle_comment(self, value):
        self.output.append("<!--" + value + "-->")


def document(package, view, nonce):
    root = Path(package["root"]).resolve()
    path = root / str(relative_path(view["entry"]))
    require(path.suffix.lower() in {".html", ".htm"} and path.is_file(), "not_found", 404)
    html = path.read_text(encoding="utf-8")
    budget = len(html.encode())

    def asset(value):
        nonlocal budget
        if value.startswith(("data:", "#")):
            return value
        require(
            not re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", value) and not value.startswith("//"),
            "invalid_input",
            400,
        )
        target = (path.parent / str(relative_path(value))).resolve()
        require(target.is_relative_to(root) and target.is_file(), "invalid_input", 400)
        data = target.read_bytes()
        budget += len(data)
        require(budget <= 384 * 1024, "budget_exceeded", 413)
        media = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return f"data:{media};base64,{base64.b64encode(data).decode()}"

    require(budget <= 384 * 1024, "budget_exceeded", 413)
    parser = AssetsHTML(asset)
    parser.feed(html)
    parser.close()
    html = "".join(parser.output)
    bootstrap = BRIDGE.read_text(encoding="utf-8").replace(
        "__TIANSHU_NONCE__", json.dumps(nonce).replace("<", "\\u003c")
    )
    return "<!doctype html><script>" + bootstrap + "</script>" + html
