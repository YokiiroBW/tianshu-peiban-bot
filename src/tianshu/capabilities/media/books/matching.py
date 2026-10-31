"""Conservative work identity and ordered chapter correspondence across book sources."""

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass

from .documents import plain_text, title_key


def author_key(value):
    value = re.sub(r"^(?:作者\s*[:：]?\s*)", "", value or "").strip()
    return title_key(value)


def same_work(left, right):
    if (
        left["metadata"].get("isbn")
        and right["metadata"].get("isbn")
        and left["metadata"]["isbn"] != right["metadata"]["isbn"]
    ):
        return False
    left, right = (
        left.get("source_identity") or left["metadata"],
        right.get("source_identity") or right["metadata"],
    )
    author = author_key(left.get("author"))
    return bool(
        author
        and author not in {"未知", "未知作者", "佚名", "作者未提供", "unknown"}
        and author == author_key(right.get("author"))
        and title_key(left.get("title"))
        and title_key(left.get("title")) == title_key(right.get("title"))
        and not (left.get("isbn") and right.get("isbn") and left["isbn"] != right["isbn"])
    )


def number_value(value):
    if value.isdecimal():
        return int(value)
    digits = dict(zip("零〇一二三四五六七八九两", (0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 2)))
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    if not any(char in units for char in value):
        return int("".join(str(digits[char]) for char in value))
    total, section, digit = 0, 0, 0
    for char in value:
        if char in digits:
            digit = digits[char]
        elif units[char] == 10000:
            total += (section + digit) * 10000
            section = digit = 0
        else:
            section += (digit or 1) * units[char]
            digit = 0
    return total + section + digit


def chapter_label(title):
    text = unicodedata.normalize("NFKC", plain_text(title)).strip()
    match = re.match(r"^第\s*([0-9零〇一二三四五六七八九十百千万两]+)\s*[章回节]\s*(.*)$", text)
    if not match:
        return None
    number, name = number_value(match[1]), match[2].strip()
    name = re.sub(
        r"[【\[](?:[^】\]]{0,50}(?:感谢|求票|月票|推荐票|加更)[^】\]]{0,50})[】\]]", "", name
    ).strip()
    part = re.search(r"(?:[【(]\s*([上中下])\s*[】)]|\s+([上中下]))$", name)
    segment = (part[1] or part[2]) if part else None
    if part:
        name = name[: part.start()].strip()
    key = title_key(name)
    return (number, key, segment, name) if number > 0 and key else None


@dataclass(frozen=True)
class ChapterUnit:
    number: int
    key: str
    title: str
    parts: tuple

    @property
    def signature(self):
        return self.number, self.key

    @property
    def position(self):
        return self.parts[0]["order"]


class ChapterIndex:
    def __init__(self, directory):
        groups = defaultdict(list)
        self.unmatched = []
        for chapter in directory:
            label = chapter_label(chapter["title"])
            if label:
                groups[label[:2]].append((label, chapter))
            else:
                self.unmatched.append(chapter)
        self.units, self.ambiguous = {}, set()
        for signature, rows in groups.items():
            labels = [label[2] for label, _ in rows]
            orders = [chapter["order"] for _, chapter in rows]
            complete = labels == [None] or (
                labels in (["上", "下"], ["上", "中", "下"])
                and orders == list(range(orders[0], orders[0] + len(orders)))
            )
            if not complete:
                self.ambiguous.add(signature)
                continue
            number, key = signature
            self.units[signature] = ChapterUnit(
                number, key, f"第{number}章 {rows[0][0][3]}", tuple(chapter for _, chapter in rows)
            )
        self.by_number = defaultdict(list)
        for unit in self.units.values():
            self.by_number[unit.number].append(unit)

    def anchors(self, other):
        shared = sorted(set(self.units) & set(other.units))
        if not shared:
            return []
        return [
            self.units[shared[index]] for index in sorted({0, len(shared) // 2, len(shared) - 1})
        ]

    def compatible(self, other):
        shared = sorted(set(self.units) & set(other.units))
        minimum = min(3, len(self.units))
        if len(shared) < minimum or not minimum:
            return False
        positions = [other.units[key].position for key in shared]
        # Volume resets and re-ordered directories are not repaired by sorting them.
        return positions == sorted(positions) and len(set(positions)) == len(positions)

    def extension(self, last_number, last_position=-1):
        result = []
        number = last_number + 1
        while len(self.by_number.get(number, [])) == 1:
            unit = self.by_number[number][0]
            if unit.position <= last_position or any(key[0] == number for key in self.ambiguous):
                break
            result.append(unit)
            last_position = unit.parts[-1]["order"]
            number += 1
        return result


def content_matches(left, right):
    left, right = title_key(left), title_key(right)
    if not left or not right:
        return False
    if left == right:
        return True
    if (
        min(len(left), len(right)) < 120
        or min(len(left), len(right)) / max(len(left), len(right)) < 0.65
    ):
        return False
    # Punctuation/whitespace differences and small site notices do not hide a
    # different work. Compare distributed text, not just a shared opening line.
    a = {left[index : index + 8] for index in range(0, len(left) - 7, 3)}
    b = {right[index : index + 8] for index in range(len(right) - 7)}
    return len(a & b) / max(1, len(a)) >= 0.78
