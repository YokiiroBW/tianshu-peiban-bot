import { BookOpen, Download } from "lucide-react";
import type { Book } from "./types";
import { bookFileUrl, bookReadability } from "./presentation";
import { BookSourceStatus } from "./BookSourceStatus";

export function BookCover({ book }: { book: Book }) {
  return (
    <div
      className={`book-cover book-cover-${parseInt(book.book_id.slice(0, 2), 16) % 4}`}
      aria-hidden="true"
    >
      <strong>{book.metadata.title}</strong>
      <small>{book.metadata.author || "作者未提供"}</small>
      {(book.metadata.cover_url || book.has_cover) && (
        <img
          key={book.metadata.cover_url}
          src={bookFileUrl(book, "cover")}
          alt=""
          loading="lazy"
          onError={(event) => {
            event.currentTarget.hidden = true;
          }}
        />
      )}
    </div>
  );
}

export function BookCard({
  book,
  alternatives = [],
  subscribed = false,
  onPreview,
  onSubscribe,
  library = false,
}: {
  book: Book;
  alternatives?: Book[];
  subscribed?: boolean;
  onPreview: (book: Book) => void;
  onSubscribe?: (book: Book) => void;
  library?: boolean;
}) {
  return (
    <article className="book-card">
      <BookCover book={book} />
      <div className="book-card-body">
        <div className="book-card-heading">
          <h3>{book.metadata.title}</h3>
          {book.artifact && (
            <span
              className={`book-tag ${book.artifact.complete ? "is-ready" : "is-warning"}`}
            >
              {book.artifact.complete ? "已有 EPUB" : "部分章节 EPUB"}
            </span>
          )}
        </div>
        <p className="muted book-card-author">
          {book.metadata.author || "作者未提供"}
          {book.metadata.tags?.length
            ? ` · ${book.metadata.tags.slice(0, 3).join(" / ")}`
            : ""}
        </p>
        <p className="book-card-description">
          {book.metadata.description || "进入详情查看目录与正文。"}
        </p>
        <div className="book-card-facts">
          <span>
            {book.chapter_count ? `${book.chapter_count} 章` : "目录待读取"}
          </span>
          <span>
            {alternatives.length > 1
              ? `${alternatives.length} 个来源`
              : book.source_name}
          </span>
          {book.latest_chapter && (
            <span title={book.latest_chapter}>
              更新至 {book.latest_chapter}
            </span>
          )}
        </div>
        <p className="book-fineprint muted">{bookReadability(book)}</p>
        <BookSourceStatus book={book} />
        <div className="book-card-actions">
          <button className="button" onClick={() => onPreview(book)}>
            <BookOpen aria-hidden="true" />
            预览与资料
          </button>
          {library && book.artifact ? (
            <a
              className="button primary"
              href={bookFileUrl(book, "epub")}
              download
            >
              <Download aria-hidden="true" />
              下载 EPUB
            </a>
          ) : (
            onSubscribe && (
              <button
                className="button primary"
                onClick={() => onSubscribe(book)}
              >
                {subscribed ? "查看订阅" : "订阅下载"}
              </button>
            )
          )}
        </div>
      </div>
    </article>
  );
}
