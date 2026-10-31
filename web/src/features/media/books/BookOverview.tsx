import { ArrowRight, BookOpen } from "lucide-react";
import type { MediaView } from "../types";
import { count } from "../overviewPresentation";

export function BookOverview({ view }: { view: MediaView }) {
  const books = view.overview?.books;
  return (
    <section
      className="media-card overview-subscriptions"
      aria-label="书籍订阅状态"
    >
      <div className="media-section-head">
        <h2>
          <BookOpen aria-hidden="true" />
          书籍订阅
        </h2>
        <a className="overview-text-link" href="#/media/books">
          管理书籍
          <ArrowRight aria-hidden="true" />
        </a>
      </div>
      <dl className="media-facts">
        <div>
          <dt>追更中</dt>
          <dd>{count(books?.active)}</dd>
        </div>
        <div>
          <dt>已暂停</dt>
          <dd>{count(books?.paused)}</dd>
        </div>
        <div>
          <dt>需要处理</dt>
          <dd>{count(books?.attention)}</dd>
        </div>
        <div>
          <dt>本地 EPUB</dt>
          <dd>{count(books?.files)}</dd>
        </div>
      </dl>
      <div className="overview-card-footer">
        <span className="muted">
          {books
            ? books.total
              ? "新增章节自动整理，原文与 EPUB 保存在本地。"
              : "导入书源或自己的文件，开始建立书库。"
            : "尚未取得书籍摘要。"}
        </span>
      </div>
    </section>
  );
}
