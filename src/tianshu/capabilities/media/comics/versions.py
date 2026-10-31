"""Language evidence and chapter editions shared by search, downloads and tracking."""

import re
import unicodedata

from ...contracts import digest, require


def language_code(value):
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower().replace("_", "-")
    if re.fullmatch(r"zh(?:-(?:cn|sg|hans))?", text) or text in {"chinese", "中文", "汉语", "漢語"}:
        return "zh-Hans" if text != "zh" and text.startswith("zh-") else "zh"
    if text in {"zh-hant", "zh-hk", "zh-tw"}:
        return "zh-Hant"
    if any(
        v in text
        for v in (
            "简体",
            "簡體",
            "simplified chinese",
            "chinese (simplified)",
            "chinese simplified",
        )
    ):
        return "zh-Hans"
    if any(
        v in text
        for v in (
            "繁体",
            "繁體",
            "traditional chinese",
            "chinese (traditional)",
            "chinese traditional",
        )
    ):
        return "zh-Hant"
    for code, values in (
        ("en", ("english", "英文", "英语", "英語")),
        ("ja", ("japanese", "日本語", "日文", "日语", "日語")),
        ("ko", ("korean", "韩语", "韓語", "한국어")),
        ("fr", ("french", "français", "法文")),
        ("es", ("spanish", "español", "西班牙语")),
        ("pt", ("portuguese", "português", "葡萄牙语")),
        ("ru", ("russian", "русский", "俄文")),
        ("de", ("german", "deutsch", "德文")),
        ("it", ("italian", "italiano", "意大利语")),
        ("vi", ("vietnamese", "越南语")),
        ("id", ("indonesian", "印尼语")),
        ("th", ("thai", "泰文")),
        ("ar", ("arabic", "阿拉伯语")),
    ):
        if text == code or any(text == v or text.startswith(v + " (") for v in values):
            return code
    return "unknown"


def chinese(value):
    return language_code(value).startswith("zh")


def evidence(row, source_language="", *, chapter=False):
    for field in ("translatedLanguage", "language", "lang"):
        code = language_code(row.get(field))
        if code != "unknown":
            return {"language": code, "basis": "chapter_metadata" if chapter else "metadata"}
    description = str(row.get("description") or "")[:20000]
    for value in re.findall(
        r"(?im)^\s*(?:language|translated language|语言|語言|语种|語種)\s*[:：]\s*([^\n<]+)",
        description,
    ):
        code = language_code(value)
        if code != "unknown":
            return {"language": code, "basis": "metadata"}
    text = " ".join(
        str(row.get(k) or "") for k in (("name", "title", "scanlator") if chapter else ("title",))
    )
    markers = re.findall(r"[\[【]([^\]】]{1,80})[\]】]", text)
    markers.extend(re.findall(r"[(（]([^)）]{1,50})[)）]", text))
    markers.extend(str(v) for v in (row.get("genre") or []) if isinstance(v, str))
    for value in markers:
        code = language_code(value)
        if code != "unknown":
            return {"language": code, "basis": "chapter_tag" if chapter else "language_tag"}
    if re.search(r"汉化|漢化|中文翻[译譯]|中国[语語]訳", text):
        return {"language": "zh", "basis": "translation_tag"}
    code = language_code(source_language)
    return {"language": code, "basis": "source" if code != "unknown" else "unknown"}


def comic_evidence(source, item, previous=None):
    result = evidence(item, source.get("language", ""))
    if previous and "language" in previous.get("manual_fields", []):
        result = {
            "language": language_code(previous["metadata"].get("language")),
            "basis": "user_metadata",
        }
    return result


def chapter_evidence(comic, row):
    result = evidence(row, chapter=True)
    if result["language"] == "unknown":
        if "language" in comic.get("manual_fields", []):
            return {
                "language": language_code(comic["metadata"].get("language")),
                "basis": "user_metadata",
            }
        inherited = comic.get("language_evidence") or {
            "language": language_code(comic["metadata"].get("language")),
            "basis": "source",
        }
        # A title search filter only proves that some translated chapters may exist.
        if inherited.get("basis") not in {"query_filter", "unknown"}:
            return dict(inherited)
    return result


def version_id(language, scanlator):
    return digest({"language": language, "scanlator": scanlator})[:24]


def version_options(comic, chapters):
    result = {}
    for chapter in chapters:
        language = chapter.get("language_evidence") or chapter_evidence(comic, chapter)
        if chapter.get("source_missing") or not chinese(language["language"]):
            continue
        group = chapter.get("scanlator", "")
        key = version_id(language["language"], group)
        result.setdefault(
            key,
            {
                "id": key,
                "language": language["language"],
                "scanlator": group,
                "basis": language["basis"],
                "chapters": 0,
            },
        )["chapters"] += 1
    return sorted(result.values(), key=lambda row: (row["language"], row["scanlator"]))


def choose_version(comic, chapters, body, previous=None):
    requested = body.get("version_id")
    if requested is None and previous:
        return previous.get("version_policy")
    options = version_options(comic, chapters)
    group = body.get("scanlator", "")
    if requested is None and group:
        options = [v for v in options if v["scanlator"] == group]
    selected = (
        next((v for v in options if v["id"] == requested), None)
        if requested is not None
        else options[0]
        if len(options) == 1
        else None
    )
    require(selected is not None, "comic_chinese_version_required", 409)
    return {
        "version": 1,
        "source_id": comic["source_id"],
        "engine_id": comic["engine_id"],
        "language": selected["language"],
        "scanlator": selected["scanlator"],
    }


def eligible(comic, chapters, subscription):
    policy = subscription.get("version_policy")
    if not policy:
        return [
            c
            for c in chapters
            if not c.get("source_missing")
            and (
                not subscription.get("scanlator") or c.get("scanlator") == subscription["scanlator"]
            )
        ]
    require(
        policy.get("source_id") == comic["source_id"]
        and policy.get("engine_id") == comic["engine_id"],
        "comic_source_changed",
        409,
    )
    return [
        c
        for c in chapters
        if not c.get("source_missing")
        and c.get("scanlator", "") == policy["scanlator"]
        and (c.get("language_evidence") or chapter_evidence(comic, c))["language"]
        == policy["language"]
    ]


def chinese_filter_plans(filters):
    """Produce per-request filter changes without writing source preferences."""
    plans = []
    language_names = re.compile(r"language|语言|語言|语种|語種", re.I)
    original_names = re.compile(r"original|origin |原作|原始|出版|原文", re.I)
    for position, field in enumerate(filters[:100]):
        name = str(field.get("name") or "")
        if not language_names.search(name) or original_names.search(name):
            continue
        if field.get("__typename") == "SelectFilter":
            for index, value in enumerate(field.get("values", [])[:300]):
                if chinese(value):
                    plans.append([{"position": position, "selectState": index}])
        elif field.get("__typename") == "GroupFilter":
            children = field.get("filters", [])[:300]
            if not any(chinese(c.get("name")) for c in children):
                continue
            changes = []
            for index, child in enumerate(children):
                kind = child.get("__typename")
                if kind == "CheckBoxFilter":
                    change = {"position": index, "checkBoxState": chinese(child.get("name"))}
                elif kind == "TriStateFilter":
                    change = {
                        "position": index,
                        "triState": "INCLUDE" if chinese(child.get("name")) else "IGNORE",
                    }
                else:
                    continue
                changes.append({"position": position, "groupChange": change})
            if changes:
                plans.append(changes)
    return plans[:2]
