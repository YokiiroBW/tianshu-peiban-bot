import { useRef, useState } from "react";
import { Upload, RefreshCw, BookOpen } from "lucide-react";
import type { Book } from "./types";
import { BookCard } from "./BookCard";
import { bookBytes } from "./presentation";

export function BookLibrary({
  books,
  total,
  busy,
  onPreview,
  onImport,
  onRefresh,
  onMore,
  onRebuild,
}: {
  books: Book[];
  total: number;
  busy: boolean;
  onPreview: (book: Book) => void;
  onImport: (file: File) => Promise<void>;
  onRefresh: (query: string) => void;
  onMore: () => void;
  onRebuild: (book: Book) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  return (
    <>
      <div className="book-section-heading">
        <div>
          <h3>书都留在这里</h3>
          <p className="muted">导入自己的文件，或从订阅持续获取章节。</p>
        </div>
        <button
          className="button primary"
          disabled={busy}
          onClick={() => input.current?.click()}
        >
          <Upload aria-hidden="true" />
          导入本地书籍
        </button>
        <input
          ref={input}
          type="file"
          accept=".txt,.md,.markdown,.html,.htm,.epub"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void onImport(file);
            event.target.value = "";
          }}
        />
      </div>
      <form
        className="book-library-search"
        onSubmit={(event) => {
          event.preventDefault();
          onRefresh(query);
        }}
      >
        <label className="sr-only" htmlFor="book-library-query">
          搜索本地书库
        </label>
        <input
          id="book-library-query"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="在本地书库查找书名或作者"
        />
        <button className="button">
          <RefreshCw aria-hidden="true" />
          读取书库
        </button>
      </form>
      <div className="book-card-list">
        {books.map((book) => (
          <div key={book.book_id}>
            <BookCard book={book} library onPreview={onPreview} />
            <div className="book-artifact-row">
              <span className="muted">
                {book.artifact
                  ? `${bookBytes(book.artifact.bytes)} · 第 ${book.artifact.version} 版 · ${book.artifact.chapters} 章${book.artifact.complete ? "" : "，仍需补章"}`
                  : "原文已导入，等待整理成 EPUB"}
              </span>
              <button
                className="button book-small-button"
                disabled={busy}
                onClick={() => onRebuild(book)}
              >
                重新整理 EPUB
              </button>
              {book.artifact && (
                <code title={book.artifact.relative_path}>
                  {book.artifact.relative_path}
                </code>
              )}
            </div>
          </div>
        ))}
      </div>
      {!books.length && (
        <div className="book-empty">
          <BookOpen aria-hidden="true" />
          <h3>这里还没有匹配的书籍</h3>
          <p className="muted">
            支持导入
            TXT、Markdown、HTML、EPUB。下载和转换进度会出现在统一下载中心。
          </p>
        </div>
      )}
      {books.length < total && (
        <button className="button" onClick={onMore}>
          加载更多（{books.length}/{total}）
        </button>
      )}
    </>
  );
}
