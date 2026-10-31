import { Pause, Play, RefreshCw, BookOpen, Trash2 } from "lucide-react";
import type { Book, BookSubscription } from "./types";
import { timestamp } from "../wording";
import { bookCode } from "./presentation";
import { BookSourceStatus } from "./BookSourceStatus";

export function BookSubscriptions({
  subscriptions,
  busy,
  onControl,
  onScan,
  onPreview,
}: {
  subscriptions: BookSubscription[];
  busy: boolean;
  onControl: (
    subscription: BookSubscription,
    action: string,
    interval?: number,
  ) => void;
  onScan: (subscription: BookSubscription) => void;
  onPreview: (book: Book) => void;
}) {
  if (!subscriptions.length)
    return (
      <div className="book-empty">
        <BookOpen aria-hidden="true" />
        <h3>还没有订阅书籍</h3>
        <p className="muted">
          在搜索结果或书籍详情中选择“订阅并下载”，之后会自动检查新章节。
        </p>
      </div>
    );
  return (
    <div className="media-stack">
      {subscriptions.map((subscription) => {
        const book = subscription.book;
        const percent = book.chapter_count
          ? Math.min(100, (book.downloaded / book.chapter_count) * 100)
          : 0;
        return (
          <article
            className="media-card book-subscription"
            key={subscription.book_id}
          >
            <div className="book-section-heading">
              <div>
                <h3>{book.metadata.title}</h3>
                <p className="muted">
                  {book.metadata.author || "作者未提供"} · {book.source_name}
                </p>
              </div>
              <span
                className={`book-tag ${subscription.state === "active" ? "is-ready" : ""}`}
              >
                {subscription.state === "active" ? "追更中" : "已暂停"}
              </span>
            </div>
            <div
              className="book-progress"
              aria-label={`已获取 ${book.downloaded} 章`}
            >
              <i style={{ width: `${percent}%` }} />
            </div>
            <div className="book-card-facts">
              <span>
                已取得 {book.downloaded} / {book.chapter_count || "待查"} 章
              </span>
              <span>
                最近检查{" "}
                {subscription.last_scan_at
                  ? timestamp(subscription.last_scan_at)
                  : "尚未检查"}
              </span>
              {subscription.code !== "ready" && (
                <span className="media-warning">
                  {bookCode(subscription.code)}
                </span>
              )}
            </div>
            <div className="book-controls">
              <label
                className="sr-only"
                htmlFor={`book-frequency-${book.book_id}`}
              >
                追更周期
              </label>
              <select
                id={`book-frequency-${book.book_id}`}
                value={subscription.interval_seconds}
                disabled={busy}
                onChange={(event) =>
                  onControl(
                    subscription,
                    "interval",
                    Number(event.target.value),
                  )
                }
              >
                <option value={3600}>每小时</option>
                <option value={21600}>每 6 小时</option>
                <option value={86400}>每天</option>
              </select>
              <button
                className="button"
                disabled={busy}
                onClick={() => onScan(subscription)}
              >
                <RefreshCw aria-hidden="true" />
                立即检查
              </button>
              <button
                className="button"
                disabled={busy}
                onClick={() =>
                  onControl(
                    subscription,
                    subscription.state === "active" ? "pause" : "resume",
                  )
                }
              >
                {subscription.state === "active" ? (
                  <Pause aria-hidden="true" />
                ) : (
                  <Play aria-hidden="true" />
                )}
                {subscription.state === "active" ? "暂停追更" : "恢复追更"}
              </button>
              <button className="button" onClick={() => onPreview(book)}>
                查看正文
              </button>
              <button
                className="button"
                disabled={busy}
                onClick={() => onControl(subscription, "remove")}
              >
                <Trash2 aria-hidden="true" />
                删除订阅
              </button>
            </div>
            <BookSourceStatus book={book} />
            <p className="muted book-fineprint">
              自动优选可读、更新领先的同书来源；已有章节继续复用。删除订阅保留文件、阅读进度和已入队任务。
            </p>
          </article>
        );
      })}
    </div>
  );
}
