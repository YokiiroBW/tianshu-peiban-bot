"""Lossless image-directory/CBZ input; only private staging files are written."""

import hashlib
import os
import re
import shutil
import unicodedata
import uuid
import warnings
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from PIL import Image, UnidentifiedImageError

from ...contracts import Fault, require
from ..destination import path_component, safe_path
from ..video_storage import sync_directory, sync_file

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}
FORMAT_EXTENSIONS = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "GIF": ".gif",
    "BMP": ".bmp",
    "AVIF": ".avif",
}
MAX_PAGE_BYTES = 64 * 1024**2
MAX_CHAPTER_BYTES = 4 * 1024**3
MAX_PAGES = 10000


def natural_key(name):
    return tuple(
        (0, int(v)) if v.isdigit() else (1, v.casefold())
        for v in re.split(r"(\d+)", unicodedata.normalize("NFC", name))
    )


def safe_name(value, maximum=140):
    # AssetLibrary's portable intent also excludes URL escapes and DEL.
    value = " ".join(str(value).split()).replace("%", "_").replace("\x7f", "_")
    return path_component(value, fallback="未命名", maximum=maximum)


def linked(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def input_path(root, relative):
    require(isinstance(relative, str) and len(relative) <= 3000, "invalid_input", 400)
    root = Path(root)
    path = root if relative in ("", ".") else safe_path(root, relative)
    require(path.exists() and not linked(root), "comic_input_unavailable", 409)
    require(not any(linked(p) for p in (path, *path.parents)), "unsafe_library_path", 409)
    require(path.resolve().is_relative_to(root.resolve()), "unsafe_library_path", 409)
    return path


def image_record(path):
    path = Path(path)
    stat = path.stat()
    require(0 < stat.st_size <= MAX_PAGE_BYTES and not linked(path), "comic_page_invalid", 409)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                require(image.format in FORMAT_EXTENSIONS, "comic_image_unsupported", 409)
                require(0 < image.width * image.height <= 100_000_000, "comic_page_too_large", 409)
                extension = FORMAT_EXTENSIONS[image.format]
                dimensions = [image.width, image.height]
                image.load()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise Fault("comic_page_invalid", 409) from None
    with path.open("rb") as handle:
        sha = hashlib.file_digest(handle, "sha256").hexdigest()
    after = path.stat()
    require(
        (stat.st_size, stat.st_mtime_ns, stat.st_ino)
        == (after.st_size, after.st_mtime_ns, after.st_ino),
        "comic_input_changed",
        409,
    )
    return {
        "sha256": sha,
        "size_bytes": stat.st_size,
        "extension": extension,
        "dimensions": dimensions,
    }


def folder_pages(folder):
    paths = sorted(
        (p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS),
        key=lambda p: (natural_key(p.name), p.name),
    )
    require(
        0 < len(paths) <= MAX_PAGES,
        "comic_directory_empty" if not paths else "comic_page_limit",
        409,
    )
    keys = [natural_key(p.stem) for p in paths]
    require(len(set(keys)) == len(keys), "comic_page_order_ambiguous", 409)
    require(not any(linked(p) for p in paths), "unsafe_library_path", 409)
    return paths


def copy_page(source, target):
    """Snapshot a source before decoding; an external edit cannot publish mixed bytes."""
    source, target = Path(source), Path(target)
    require(not linked(source), "unsafe_library_path", 409)
    before = source.stat()
    require(0 < before.st_size <= MAX_PAGE_BYTES, "comic_page_invalid", 409)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + "." + uuid.uuid4().hex + ".partial")
    try:
        with source.open("rb") as incoming, partial.open("xb") as outgoing:
            size = 0
            while chunk := incoming.read(1024 * 1024):
                size += len(chunk)
                require(size <= MAX_PAGE_BYTES, "comic_page_too_large", 409)
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        after = source.stat()
        require(
            (before.st_size, before.st_mtime_ns, before.st_ino)
            == (after.st_size, after.st_mtime_ns, after.st_ino),
            "comic_input_changed",
            409,
        )
        record = image_record(partial)
        partial.replace(target)
        return record
    finally:
        partial.unlink(missing_ok=True)


def extract_images(archive_path, destination):
    """Read image ZIPs without executing paths or trusting declared compression sizes."""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            require(len(members) <= MAX_PAGES + 100, "comic_page_limit", 409)
            images = []
            for item in members:
                name = item.filename
                parts = PurePosixPath(name)
                require(
                    not parts.is_absolute() and ".." not in parts.parts and "\\" not in name,
                    "comic_archive_invalid",
                    409,
                )
                if item.is_dir() or parts.parts[0] == "__MACOSX":
                    continue
                if parts.suffix.lower() in IMAGE_EXTENSIONS:
                    require(
                        not item.flag_bits & 1 and 0 < item.file_size <= MAX_PAGE_BYTES,
                        "comic_archive_invalid",
                        409,
                    )
                    images.append(item)
            images.sort(key=lambda i: (natural_key(i.filename), i.filename))
            require(0 < len(images) <= MAX_PAGES, "comic_directory_empty", 409)
            require(
                len({natural_key(str(PurePosixPath(i.filename).with_suffix(""))) for i in images})
                == len(images),
                "comic_page_order_ambiguous",
                409,
            )
            require(
                sum(i.file_size for i in images) <= MAX_CHAPTER_BYTES,
                "comic_chapter_too_large",
                409,
            )
            Path(destination).mkdir(parents=True, exist_ok=True)
            result = []
            for index, item in enumerate(images):
                path = Path(destination) / f"{index:06d}.page"
                with archive.open(item) as incoming, path.open("wb") as outgoing:
                    count = 0
                    while chunk := incoming.read(1024 * 1024):
                        count += len(chunk)
                        require(count <= MAX_PAGE_BYTES, "comic_page_too_large", 409)
                        outgoing.write(chunk)
                result.append((path, image_record(path)))
            return result
    except (zipfile.BadZipFile, RuntimeError, EOFError):
        raise Fault("comic_archive_invalid", 409) from None


def comic_info(metadata, chapter, page_count):
    root = ET.Element("ComicInfo")
    fields = {
        "Series": metadata["title"],
        "Title": chapter["title"],
        "Number": chapter.get("number", ""),
        "Writer": metadata.get("author", ""),
        "Penciller": metadata.get("artist", ""),
        "LanguageISO": metadata.get("language", ""),
        "Summary": metadata.get("description", ""),
        "PageCount": str(page_count),
    }
    if metadata.get("direction") == "rtl":
        fields["Manga"] = "YesAndRightToLeft"
    for key, value in fields.items():
        if value is not None and str(value):
            ET.SubElement(root, key).text = "".join(
                c
                for c in str(value)
                if ord(c) in {9, 10, 13}
                or 32 <= ord(c) <= 0xD7FF
                or 0xE000 <= ord(c) <= 0xFFFD
                or 0x10000 <= ord(c) <= 0x10FFFF
            )
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def package_cbz(pages, output, metadata, chapter):
    require(0 < len(pages) <= MAX_PAGES, "comic_directory_empty", 409)
    require(
        sum(r["size_bytes"] for _, r in pages) <= MAX_CHAPTER_BYTES, "comic_chapter_too_large", 409
    )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + "." + uuid.uuid4().hex + ".partial")
    manifest = []
    try:
        with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_STORED) as archive:
            for index, (path, expected) in enumerate(pages, 1):
                record = image_record(path)
                require(record == expected, "comic_input_changed", 409)
                name = f"{index:06d}" + record["extension"]
                entry = zipfile.ZipInfo(name)
                entry.file_size = record["size_bytes"]
                entry.external_attr = 0o100644 << 16
                with Path(path).open("rb") as incoming, archive.open(entry, "w") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
                manifest.append({"name": name, **record})
            archive.writestr(
                zipfile.ZipInfo("ComicInfo.xml"), comic_info(metadata, chapter, len(pages))
            )
        sync_file(temporary)
        with zipfile.ZipFile(temporary) as archive:
            require(archive.testzip() is None, "comic_archive_invalid", 409)
            for page in manifest:
                with archive.open(page["name"]) as incoming:
                    require(
                        hashlib.file_digest(incoming, "sha256").hexdigest() == page["sha256"],
                        "comic_input_changed",
                        409,
                    )
        temporary.replace(output)
        sync_directory(output.parent)
        with output.open("rb") as handle:
            sha = hashlib.file_digest(handle, "sha256").hexdigest()
        return {
            "sha256": sha,
            "size_bytes": output.stat().st_size,
            "page_count": len(pages),
            "pages": manifest,
        }
    finally:
        temporary.unlink(missing_ok=True)
