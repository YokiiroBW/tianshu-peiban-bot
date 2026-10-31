import { bookWords } from "./wording";
import type { Book } from "./types";

export function groupedBooks(books: Book[]) {
  const groups = new Map<string, Book[]>();
  for (const book of books) {
    const group = groups.get(book.group_key) ?? [];
    if (!group.some((item) => item.book_id === book.book_id)) group.push(book);
    groups.set(book.group_key, group);
  }
  return [...groups.values()];
}

export function preferredBook(books: Book[]) {
  return (
    books.find((book) => book.artifact?.complete) ??
    books.find((book) =>
      ["complete", "sampled", "previewed"].includes(
        book.readability?.state ?? "",
      ),
    ) ??
    books.find((book) => book.readability?.state !== "failed") ??
    books[0]
  );
}

export function bookBytes(bytes: number) {
  return bytes < 1024 * 1024
    ? `${Math.ceil(bytes / 1024)} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function bookCode(code: string) {
  return code === "ready" ? "可用" : (bookWords[code] ?? code);
}

export function bookReadability(book: Book) {
  if (book.source_type !== "remote") return "本地正文";
  const check = book.readability;
  if (check?.state === "failed")
    return `${check.chapter_title || "正文检查"}：${bookCode(check.code || "book_content_unavailable")}`;
  if (check?.state === "complete")
    return check.sample_count >= book.chapter_count
      ? "当前目录正文已完整保存"
      : "旧章节已保存，新章待检查";
  if (check?.state === "sampled")
    return `已抽查 ${check.sample_count} 章正文，下载时继续逐章检查`;
  if (check?.state === "previewed") return "已取得试读正文，整本待验证";
  return book.chapter_count ? "目录可读，正文待验证" : "仅搜索结果，正文待验证";
}

export function bookFeedCode(code: string) {
  return code === "ready" ? "规则已同步" : bookCode(code);
}

export function bookFileUrl(book: Book, kind: "epub" | "cover") {
  return `/api/v1/media/books/files/${encodeURIComponent(book.book_id)}/${kind}?v=${book.metadata_revision}`;
}
