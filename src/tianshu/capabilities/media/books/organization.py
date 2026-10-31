"""Book category selection and the shared on-disk library layout."""

from ..destination import path_component

CATEGORIES = (
    ("玄幻奇幻", ("玄幻", "奇幻", "魔幻", "fantasy")),
    ("武侠仙侠", ("武侠", "仙侠", "修真", "修仙")),
    ("科幻", ("科幻", "science fiction")),
    ("悬疑推理", ("悬疑", "推理", "侦探", "mystery", "detective")),
    ("言情", ("言情", "爱情", "romance")),
    ("都市现实", ("都市", "现实", "职场")),
    ("历史军事", ("历史小说", "军事", "战争", "historical fiction")),
    ("历史传记", ("历史", "传记", "史藏", "正史", "history", "biography")),
    ("哲学宗教", ("哲学", "宗教", "佛", "道藏", "儒家", "philosophy", "religion")),
    ("国学古籍", ("国学", "古籍", "经藏", "子藏", "四书", "五经", "蒙学")),
    ("文学诗词", ("文学", "诗", "词集", "散文", "名著", "fiction", "literature", "poetry")),
    ("政治社会", ("政治", "社会", "politics", "sociology")),
    ("经济管理", ("经济", "管理", "商业", "economics", "business")),
    ("科学技术", ("科学", "技术", "计算机", "science", "technology")),
    ("学习教育", ("教育", "教材", "学习", "education")),
)


def book_category(metadata, source_name=""):
    explicit = metadata.get("category", "").strip()
    if explicit:
        return path_component(explicit, "未分类", 80)
    for tag in metadata.get("tags", []):
        value = str(tag).casefold()
        for category, words in CATEGORIES:
            if any(word in value for word in words):
                return category
    if any(word in source_name for word in ("国学", "典藏", "古籍", "汉籍")):
        return "国学古籍"
    return "未分类"


def book_relative_path(book):
    metadata = book["metadata"]
    previous = book.get("artifact")
    if previous:
        parts = previous["relative_path"].split("/")
        # Keep the established author/title folder when metadata is corrected.
        # Only a category/root change relocates an already published book.
        tail = parts[1:] if previous.get("category") else parts
        return "/".join([book_category(metadata, book.get("source_name", "")), *tail])
    return "/".join(
        (
            book_category(metadata, book.get("source_name", "")),
            path_component(metadata.get("author"), "未知作者", 80),
            path_component(metadata["title"], "未命名书籍", 100) + " [" + book["book_id"][:8] + "]",
            "book.epub",
        )
    )
