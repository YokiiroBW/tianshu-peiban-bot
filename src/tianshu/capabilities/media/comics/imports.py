"""Read-only NAS browsing and chapter discovery for image folders and CBZ inputs."""

from pathlib import Path

from ...contracts import digest, require
from .packages import (
    IMAGE_EXTENSIONS,
    MAX_PAGE_BYTES,
    copy_page,
    folder_pages,
    image_record,
    input_path,
    linked,
    natural_key,
)


class DirectoryInputs:
    def __init__(self, directory):
        self.directory = directory

    def root(self):
        directory = self.directory
        require(directory, "comic_import_not_configured", 409)
        return Path(directory)

    def browse(self, relative="", offset=0):
        require(type(offset) is int and 0 <= offset <= 100000, "invalid_input", 400)
        root = self.root()
        folder = input_path(root, relative)
        require(folder.is_dir(), "comic_directory_required", 409)
        entries = []
        image_count = 0
        for entry in folder.iterdir():
            if linked(entry) or entry.name.startswith((".", "@", "#")):
                continue
            if entry.is_file() and entry.suffix.lower() in IMAGE_EXTENSIONS:
                image_count += 1
            if entry.is_dir() or entry.suffix.lower() in {".cbz", ".zip"}:
                entries.append(
                    {
                        "name": entry.name,
                        "relative_path": entry.relative_to(root).as_posix(),
                        "kind": "directory" if entry.is_dir() else "cbz",
                        "size_bytes": entry.stat().st_size if entry.is_file() else None,
                    }
                )
            require(len(entries) <= 100000, "comic_directory_too_large", 409)
        entries.sort(key=lambda e: (e["kind"] != "directory", natural_key(e["name"]), e["name"]))
        return {
            "relative_path": folder.relative_to(root).as_posix(),
            "entries": entries[offset : offset + 100],
            "total": len(entries),
            "next_offset": offset + 100 if offset + 100 < len(entries) else None,
            "image_count": image_count,
        }

    def inspect(self, relative):
        root = self.root()
        selected = input_path(root, relative)
        require(selected != root, "comic_choose_work_directory", 409)
        chapters = []

        def chapter(path):
            relative_path = path.relative_to(root).as_posix()
            if path.is_file():
                require(path.suffix.lower() in {".cbz", ".zip"}, "comic_input_unsupported", 409)
                stat = path.stat()
                details = [{"name": path.name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}]
                kind, count = "archive", None
            else:
                pages = folder_pages(path)
                details = [
                    {"name": p.name, "size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}
                    for p in pages
                ]
                kind, count = "images", len(pages)
            return {
                "title": path.stem if path.is_file() else path.name,
                "relative_path": relative_path,
                "input_kind": kind,
                "input_digest": digest(details),
                "input_files": details,
                "page_count": count,
            }

        if selected.is_file():
            chapters = [chapter(selected)]
        else:
            files = list(selected.iterdir())
            require(not any(linked(p) for p in files), "unsafe_library_path", 409)
            has_images = any(p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS for p in files)
            children = [
                p
                for p in files
                if p.is_dir()
                and not p.name.startswith((".", "@", "#"))
                or p.is_file()
                and p.suffix.lower() in {".cbz", ".zip"}
            ]
            if has_images:
                require(not children, "comic_directory_layout_ambiguous", 409)
                chapters = [chapter(selected)]
            else:
                for child in sorted(children, key=lambda p: (natural_key(p.name), p.name)):
                    chapters.append(chapter(child))
                    require(len(chapters) <= 2000, "comic_chapter_limit", 409)
        require(chapters, "comic_directory_empty", 409)
        return {
            "relative_path": selected.relative_to(root).as_posix(),
            "title": selected.stem if selected.is_file() else selected.name,
            "chapters": chapters,
            "input_digest": digest([c["input_digest"] for c in chapters]),
        }

    def recheck(self, chapter):
        root = self.root()
        selected = input_path(root, chapter["relative_path"])
        if chapter["input_kind"] == "archive":
            stat = selected.stat()
            details = [{"name": selected.name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}]
        else:
            details = [
                {"name": p.name, "size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}
                for p in folder_pages(selected)
            ]
        require(digest(details) == chapter["input_digest"], "comic_input_changed", 409)
        return selected


class Imports:
    def __init__(self, comics):
        self.comics = comics

    def access(self, comic=None):
        config = comic or self.comics.settings.read()
        if config.get("import_socket"):
            from .source_client import RemoteInputs

            return RemoteInputs(config["import_socket"], config["import_directory"])
        return DirectoryInputs(config["import_directory"])

    def root(self):
        return self.access().root()

    def browse(self, relative="", offset=0):
        return self.access().browse(relative, offset)

    def inspect(self, relative):
        return self.access().inspect(relative)

    def recheck(self, comic, chapter):
        return self.access(comic).recheck(chapter)

    def archive_path(self, comic, chapter, directory):
        reader = self.access(comic)
        if isinstance(reader, DirectoryInputs):
            return reader.recheck(chapter)
        destination = directory / "original.cbz"
        reader.copy_file(chapter["relative_path"], chapter["input_files"][0], destination)
        return destination

    def copy_image(self, comic, chapter, index, destination):
        reader = self.access(comic)
        record = chapter["input_files"][index]
        relative = chapter["relative_path"] + "/" + record["name"]
        if isinstance(reader, DirectoryInputs):
            return copy_page(input_path(reader.root(), relative), destination)
        reader.copy_file(relative, record, destination, maximum=MAX_PAGE_BYTES)
        return image_record(destination)
