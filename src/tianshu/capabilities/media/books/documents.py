"""Book text normalization and real EPUB containers; no network or database access."""

import hashlib
import html
import io
import posixpath
import re
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

from ...contracts import Fault, require

DC = "http://purl.org/dc/elements/1.1/"
OPF = "http://www.idpf.org/2007/opf"
XHTML = "http://www.w3.org/1999/xhtml"
EPUB = "http://www.idpf.org/2007/ops"
CONTAINER = "urn:oasis:names:tc:opendocument:xmlns:container"
ET.register_namespace("dc", DC)
ET.register_namespace("opf", OPF)
CHAPTER = re.compile(
    r"^(?:第[\d零〇一二三四五六七八九十百千万两]+[卷章节回部篇].*|chapter\s+\d+.*|#{1,3}\s+.+)$",
    re.I,
)


def clean_text(value, maximum=1000000):
    value = str(value or "")
    require(len(value) <= maximum, "book_content_too_large", 413)
    return (
        "".join(
            character
            for character in value
            if character in "\n\r\t"
            or ord(character) >= 32
            and not 0xD800 <= ord(character) <= 0xDFFF
        )
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


def plain_text(value):
    value = clean_text(value, 32 * 1024 * 1024)
    if re.search(r"</?(?:p|div|br|span|body|html|script|h[1-6]|img)\b", value, re.I):
        from lxml import html as markup

        try:
            root = markup.fragment_fromstring(value, create_parent="div")
            for node in root.xpath(".//script|.//style|.//iframe|.//form|.//object"):
                node.drop_tree()
            for node in root.xpath(".//br|.//p|.//div|.//h1|.//h2|.//h3|.//li"):
                node.tail = "\n" + (node.tail or "")
            value = root.text_content()
        except (ValueError, TypeError):
            value = re.sub(r"<[^>]+>", "", value)
    return html.unescape(value).strip()


def paragraphs(content):
    return [line.strip() for line in plain_text(content).splitlines() if line.strip()]


def title_key(value):
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value or "").casefold())


def text_chapters(content, filename):
    lines = content.splitlines()
    chapters = []
    current = {"title": "正文", "content": []}
    for line in lines:
        title = line.strip()
        if len(title) <= 100 and CHAPTER.match(title):
            if current["content"] or chapters:
                chapters.append({**current, "content": "\n".join(current["content"]).strip()})
            current = {"title": title.lstrip("# "), "content": []}
        else:
            current["content"].append(line)
    if current["content"] or not chapters:
        chapters.append({**current, "content": "\n".join(current["content"]).strip()})
    chapters = [chapter for chapter in chapters if chapter["content"] or chapter["title"] != "正文"]
    require(chapters, "book_content_empty", 400)
    for index, chapter in enumerate(chapters):
        chapter["chapter_id"] = hashlib.sha256(f"{index}:{chapter['title']}".encode()).hexdigest()[
            :24
        ]
        chapter["order"] = index
    return {
        "title": PurePosixPath(filename).stem or "未命名书籍",
        "author": "",
        "language": "zh-CN",
    }, chapters


def member_path(base, href):
    value = posixpath.normpath(posixpath.join(posixpath.dirname(base), href.split("#", 1)[0]))
    require(
        not value.startswith(("../", "/")) and "\\" not in value and ":" not in value,
        "book_invalid_epub",
        400,
    )
    return value


def checked_archive(raw):
    require(len(raw) <= 32 * 1024 * 1024, "book_import_too_large", 413)
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        entries = archive.infolist()
        names = [item.filename for item in entries]
        require(len(set(names)) == len(names), "book_invalid_epub", 400)
        require(
            all(
                not name.startswith("/")
                and "\\" not in name
                and ":" not in name
                and ".." not in PurePosixPath(name).parts
                for name in names
            ),
            "book_invalid_epub",
            400,
        )
        require(
            len(entries) <= 20000 and sum(item.file_size for item in entries) <= 160 * 1024 * 1024,
            "book_import_too_large",
            413,
        )
        require(
            all(item.file_size <= 16 * 1024 * 1024 for item in entries),
            "book_import_too_large",
            413,
        )
        require("META-INF/encryption.xml" not in archive.namelist(), "book_encrypted_epub", 409)
        require(archive.read("mimetype") == b"application/epub+zip", "book_invalid_epub", 400)
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = container.find(f".//{{{CONTAINER}}}rootfile")
        require(rootfile is not None, "book_invalid_epub", 400)
        package_path = member_path("", rootfile.get("full-path", ""))
        package = ET.fromstring(archive.read(package_path))
        return archive, package_path, package
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError, ValueError):
        raise Fault("book_invalid_epub", 400) from None


def import_file(filename, raw):
    suffix = PurePosixPath(filename).suffix.lower()
    require(
        suffix in {".txt", ".md", ".markdown", ".html", ".htm", ".epub"},
        "book_import_format_unsupported",
        400,
    )
    require(0 < len(raw) <= 32 * 1024 * 1024, "book_import_too_large", 413)
    if suffix != ".epub":
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            content = raw.decode("utf-16")
        else:
            try:
                content = raw.decode("utf-8-sig")
            except UnicodeError:
                try:
                    content = raw.decode("gb18030")
                except UnicodeError:
                    raise Fault("book_encoding_unknown", 400) from None
        if suffix in {".html", ".htm"}:
            content = plain_text(content)
        return (*text_chapters(content, filename), None)
    archive, package_path, package = checked_archive(raw)
    try:
        metadata_node = package.find(f"{{{OPF}}}metadata")
        require(metadata_node is not None, "book_invalid_epub", 400)
        metadata = {}
        for key, tag in {
            "title": "title",
            "author": "creator",
            "description": "description",
            "language": "language",
            "publisher": "publisher",
            "published_at": "date",
            "isbn": "identifier",
        }.items():
            node = metadata_node.find(f"{{{DC}}}{tag}")
            metadata[key] = node.text or "" if node is not None else ""
        if not re.fullmatch(r"(?:97[89])?[\dXx-]{10,17}", metadata.get("isbn", "")):
            metadata["isbn"] = ""
        metadata["tags"] = [
            node.text for node in metadata_node.findall(f"{{{DC}}}subject") if node.text
        ]
        cover_id = next(
            (node.get("content") for node in metadata_node if node.get("name") == "cover"), None
        )
        for node in metadata_node:
            if node.get("name") in {"calibre:series", "calibre:series_index"}:
                metadata[node.get("name").split(":")[1]] = node.get("content", "")
        manifest = {
            node.get("id"): node for node in package.findall(f"{{{OPF}}}manifest/{{{OPF}}}item")
        }
        chapters = []
        cover = None
        for node in manifest.values():
            if (
                "cover-image" in node.get("properties", "").split()
                or cover_id
                and node.get("id") == cover_id
            ):
                cover = archive.read(member_path(package_path, node.get("href", "")))
                break
        for reference in package.findall(f"{{{OPF}}}spine/{{{OPF}}}itemref"):
            node = manifest.get(reference.get("idref"))
            if node is None or node.get("media-type") != "application/xhtml+xml":
                continue
            path = member_path(package_path, node.get("href", ""))
            content = archive.read(path).decode("utf-8-sig")
            from lxml import html as markup

            page = markup.fromstring(content.encode("utf-8"))
            headings = page.xpath("//*[local-name()='h1' or local-name()='h2']") or page.xpath(
                "//*[local-name()='title']"
            )
            title = (
                plain_text(headings[0].text_content()) if headings else f"第 {len(chapters) + 1} 节"
            )
            chapters.append(
                {
                    "chapter_id": hashlib.sha256(path.encode()).hexdigest()[:24],
                    "title": title,
                    "content": plain_text(content),
                    "order": len(chapters),
                    "original_path": path,
                }
            )
        require(chapters, "book_content_empty", 400)
        metadata["title"] = metadata.get("title") or PurePosixPath(filename).stem
        metadata["language"] = metadata.get("language") or "zh-CN"
        return metadata, chapters, cover
    except (KeyError, UnicodeError, ValueError):
        raise Fault("book_invalid_epub", 400) from None
    finally:
        archive.close()


def xml_bytes(node):
    return ET.tostring(node, encoding="utf-8", xml_declaration=True)


def apply_package_metadata(package, metadata, identifier, modified):
    node = package.find(f"{{{OPF}}}metadata")
    require(node is not None, "book_invalid_epub", 400)
    for key, tag in {
        "title": "title",
        "author": "creator",
        "description": "description",
        "language": "language",
        "publisher": "publisher",
        "published_at": "date",
    }.items():
        if key not in metadata:
            continue
        for old in list(node.findall(f"{{{DC}}}{tag}")):
            node.remove(old)
        value = metadata[key]
        if value or key in {"title", "language"}:
            ET.SubElement(node, f"{{{DC}}}{tag}").text = value or (
                "zh-CN" if key == "language" else "未命名书籍"
            )
    if not node.findall(f"{{{DC}}}identifier"):
        ET.SubElement(node, f"{{{DC}}}identifier", {"id": "book-id"}).text = identifier
        package.set("unique-identifier", "book-id")
    if "tags" in metadata or "category" in metadata:
        for old in list(node.findall(f"{{{DC}}}subject")):
            node.remove(old)
        subjects = [metadata["category"]] if metadata.get("category") else []
        for tag in dict.fromkeys(subjects + metadata.get("tags", [])):
            ET.SubElement(node, f"{{{DC}}}subject").text = tag
    if metadata.get("isbn"):
        for old in list(node.findall(f"{{{DC}}}identifier")):
            if (old.text or "").startswith("urn:isbn:") and old.get("id") != package.get(
                "unique-identifier"
            ):
                node.remove(old)
        ET.SubElement(node, f"{{{DC}}}identifier").text = "urn:isbn:" + metadata["isbn"]
    if "series" in metadata or "series_index" in metadata:
        for old in list(node.findall(f"{{{OPF}}}meta")):
            if old.get("name") in {"calibre:series", "calibre:series_index"}:
                node.remove(old)
        for key in ("series", "series_index"):
            if metadata.get(key):
                ET.SubElement(
                    node, f"{{{OPF}}}meta", {"name": "calibre:" + key, "content": metadata[key]}
                )
    for old in list(node.findall(f"{{{OPF}}}meta")):
        if old.get("property") == "dcterms:modified":
            node.remove(old)
    if package.get("version", "").startswith("3"):
        ET.SubElement(node, f"{{{OPF}}}meta", {"property": "dcterms:modified"}).text = modified


def build_epub(path, book, chapters, *, cover=None, original=None, modified_at=None):
    metadata = book["metadata"]
    modified = datetime.fromtimestamp(modified_at or 0, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if original is not None:
        archive, package_path, package = checked_archive(original)
        try:
            apply_package_metadata(
                package, metadata, "urn:tianshu:book:" + book["book_id"], modified
            )
            replacement = {}
            if cover:
                from PIL import Image

                manifest = package.find(f"{{{OPF}}}manifest")
                metadata_node = package.find(f"{{{OPF}}}metadata")
                cover_meta = next(
                    (node for node in metadata_node if node.get("name") == "cover"), None
                )
                cover_id = cover_meta.get("content") if cover_meta is not None else None
                cover_item = next(
                    (
                        node
                        for node in manifest
                        if "cover-image" in node.get("properties", "").split()
                        or cover_id
                        and node.get("id") == cover_id
                    ),
                    None,
                )
                formats = {"image/jpeg": "JPEG", "image/png": "PNG", "image/gif": "GIF"}
                if cover_item is not None and cover_item.get("media-type") in formats:
                    image_buffer = io.BytesIO()
                    with Image.open(io.BytesIO(cover)) as image:
                        image.convert("RGB").save(
                            image_buffer, formats[cover_item.get("media-type")]
                        )
                    replacement[member_path(package_path, cover_item.get("href"))] = (
                        image_buffer.getvalue()
                    )
                else:
                    for node in manifest:
                        properties = node.get("properties", "").split()
                        if "cover-image" in properties:
                            properties.remove("cover-image")
                            node.set("properties", " ".join(properties))
                    identity = "tianshu-cover-" + book["book_id"]
                    href = identity + ".jpg"
                    attributes = {"id": identity, "href": href, "media-type": "image/jpeg"}
                    if package.get("version", "").startswith("3"):
                        attributes["properties"] = "cover-image"
                    ET.SubElement(manifest, f"{{{OPF}}}item", attributes)
                    if cover_meta is None:
                        cover_meta = ET.SubElement(
                            metadata_node, f"{{{OPF}}}meta", {"name": "cover"}
                        )
                    cover_meta.set("content", identity)
                    replacement[member_path(package_path, href)] = cover
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as output:
                output.writestr(
                    "mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED
                )
                for item in archive.infolist():
                    if item.filename != "mimetype" and item.filename not in replacement:
                        output.writestr(
                            item.filename,
                            xml_bytes(package)
                            if item.filename == package_path
                            else archive.read(item),
                        )
                for filename, content in replacement.items():
                    output.writestr(filename, content)
        finally:
            archive.close()
        return
    package = ET.Element(f"{{{OPF}}}package", {"version": "3.0", "unique-identifier": "book-id"})
    ET.SubElement(package, f"{{{OPF}}}metadata")
    apply_package_metadata(package, metadata, "urn:tianshu:book:" + book["book_id"], modified)
    manifest = ET.SubElement(package, f"{{{OPF}}}manifest")
    spine = ET.SubElement(package, f"{{{OPF}}}spine")
    ET.SubElement(
        manifest,
        f"{{{OPF}}}item",
        {
            "id": "nav",
            "href": "nav.xhtml",
            "media-type": "application/xhtml+xml",
            "properties": "nav",
        },
    )
    ET.SubElement(
        manifest, f"{{{OPF}}}item", {"id": "style", "href": "style.css", "media-type": "text/css"}
    )
    if cover:
        ET.SubElement(
            manifest,
            f"{{{OPF}}}item",
            {
                "id": "cover",
                "href": "cover.jpg",
                "media-type": "image/jpeg",
                "properties": "cover-image",
            },
        )
    navigation = []
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        output.writestr(
            "META-INF/container.xml",
            f'<?xml version="1.0" encoding="utf-8"?><container xmlns="{CONTAINER}" version="1.0"><rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        output.writestr(
            "EPUB/style.css",
            "body{line-height:1.75}h1{text-align:center}p{text-indent:2em;margin:.7em 0}img{max-width:100%}",
        )
        if cover:
            output.writestr("EPUB/cover.jpg", cover)
        for chapter in chapters:
            identity = "c-" + chapter["chapter_id"]
            filename = "chapters/" + identity + ".xhtml"
            ET.SubElement(
                manifest,
                f"{{{OPF}}}item",
                {"id": identity, "href": filename, "media-type": "application/xhtml+xml"},
            )
            ET.SubElement(spine, f"{{{OPF}}}itemref", {"idref": identity})
            navigation.append(f'<li><a href="{filename}">{html.escape(chapter["title"])}</a></li>')
            seen = {}
            content = []
            for paragraph in paragraphs(chapter["content"]):
                key = hashlib.sha256(paragraph.encode()).hexdigest()[:16]
                seen[key] = seen.get(key, 0) + 1
                content.append(f'<p id="p-{key}-{seen[key]}">{html.escape(paragraph)}</p>')
            output.writestr(
                "EPUB/" + filename,
                f'<?xml version="1.0" encoding="utf-8"?><html xmlns="{XHTML}" xml:lang="{html.escape(metadata.get("language") or "zh-CN", quote=True)}"><head><title>{html.escape(chapter["title"])}</title><link rel="stylesheet" href="../style.css" type="text/css"/></head><body><h1>{html.escape(chapter["title"])}</h1>{"".join(content)}</body></html>',
            )
        output.writestr(
            "EPUB/nav.xhtml",
            f'<?xml version="1.0" encoding="utf-8"?><html xmlns="{XHTML}" xmlns:epub="{EPUB}"><head><title>目录</title></head><body><nav epub:type="toc" id="toc"><h1>目录</h1><ol>{"".join(navigation)}</ol></nav></body></html>',
        )
        output.writestr("EPUB/package.opf", xml_bytes(package))


def validate_epub(path):
    with zipfile.ZipFile(path) as archive:
        require(
            archive.infolist()[0].filename == "mimetype"
            and archive.infolist()[0].compress_type == zipfile.ZIP_STORED,
            "book_invalid_epub",
            400,
        )
        require(archive.read("mimetype") == b"application/epub+zip", "book_invalid_epub", 400)
        require(archive.testzip() is None, "book_invalid_epub", 400)
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        package_path = container.find(f".//{{{CONTAINER}}}rootfile").get("full-path")
        package = ET.fromstring(archive.read(package_path))
        manifest = {
            item.get("id"): item for item in package.findall(f"{{{OPF}}}manifest/{{{OPF}}}item")
        }
        for node in manifest.values():
            href = member_path(package_path, node.get("href", ""))
            require(href in archive.namelist(), "book_invalid_epub", 400)
            if node.get("media-type") == "application/xhtml+xml":
                ET.fromstring(archive.read(href))
        for reference in package.findall(f"{{{OPF}}}spine/{{{OPF}}}itemref"):
            require(reference.get("idref") in manifest, "book_invalid_epub", 400)
