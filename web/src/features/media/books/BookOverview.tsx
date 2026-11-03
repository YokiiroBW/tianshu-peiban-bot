import { ArrowRight, BookOpen } from "lucide-react";
import type { MediaView } from "../types";
import { count } from "../overviewPresentation";

export function BookOverview({ view }: { view: MediaView }) {
  const books = view.overview?.books;
  return (
    <section
      className="curio-card media-card overview-subscriptions"
      aria-label="书籍订阅状态"
    >
      <div className="section-head media-section-head">
        <div className="section-title-group">
          <div className="section-seal-icon" aria-hidden="true">
            卷
          </div>
          <div>
            <h2 className="section-title">
              <BookOpen aria-hidden="true" className="sr-only" />
              书籍订阅
            </h2>
            <div className="section-sub">书源追更与本地 EPUB 典藏</div>
          </div>
        </div>
        <a className="ink-link overview-text-link" href="#/media/books">
          管理书籍
          <ArrowRight aria-hidden="true" />
        </a>
      </div>

      <dl className="sub-stat-grid curio-book-stat-grid media-facts">
        <div className="sub-stat-box">
          <dt className="s-label">追更中</dt>
          <dd className="s-val">{count(books?.active)}</dd>
        </div>
        <div className="sub-stat-box">
          <dt className="s-label">已暂停</dt>
          <dd className="s-val">{count(books?.paused)}</dd>
        </div>
        <div className="sub-stat-box">
          <dt className="s-label">需要处理</dt>
          <dd className="s-val">{count(books?.attention)}</dd>
        </div>
        <div className="sub-stat-box">
          <dt className="s-label">本地 EPUB</dt>
          <dd className="s-val">{count(books?.files)}</dd>
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
