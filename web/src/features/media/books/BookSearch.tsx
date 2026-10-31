import { DetailCard } from "../../../components/DetailCard";
import { useState } from "react";
import { Search, Plus, Square, BookOpen } from "lucide-react";
import type { Book, BooksView, BookSearchResult } from "./types";
import { BookCard } from "./BookCard";
import { bookCode, groupedBooks, preferredBook } from "./presentation";

export function BookSearch({
  view,
  result,
  onSearch,
  onCancel,
  onPreview,
  onSubscribe,
  onTab,
}: {
  view: BooksView;
  result: BookSearchResult | null;
  onSearch: (query: string, group: string, offset?: number) => void;
  onCancel: () => void;
  onPreview: (book: Book) => void;
  onSubscribe: (book: Book) => void;
  onTab: (tab: string) => void;
}) {
  const [query, setQuery] = useState(result?.query ?? "");
  const [group, setGroup] = useState("");
  const groups = groupedBooks(result?.books ?? []);
  const failed =
    result?.sources.filter((source) => source.code !== "ready") ?? [];
  return (
    <>
      <form
        className="book-search-form"
        onSubmit={(event) => {
          event.preventDefault();
          onSearch(query, group);
        }}
      >
        <label className="sr-only" htmlFor="book-search-query">
          书名、作者或 ISBN
        </label>
        <Search aria-hidden="true" />
        <input
          id="book-search-query"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索书名、作者或 ISBN"
          required
          maxLength={200}
        />
        <label className="sr-only" htmlFor="book-search-group">
          书源分组
        </label>
        <select
          id="book-search-group"
          value={group}
          onChange={(event) => setGroup(event.target.value)}
        >
          <option value="">全部启用书源</option>
          {view.groups.map((value) => (
            <option key={value}>{value}</option>
          ))}
        </select>
        <button
          className="button primary"
          disabled={!query.trim()}
          type="submit"
        >
          聚合搜索
        </button>
      </form>
      <div className="book-search-layout">
        <section>
          <div className="book-section-heading">
            <h3>{result ? "搜索结果" : "找到下一本想读的书"}</h3>
            {result && (
              <span className="muted">
                {groups.length} 本 · {result.sources.length}/{result.total}{" "}
                个来源返回
              </span>
            )}
            {result?.state === "running" && (
              <button className="button book-small-button" onClick={onCancel}>
                <Square aria-hidden="true" />
                停止搜索
              </button>
            )}
          </div>
          {failed.length > 0 && (
            <DetailCard
              title={<>{failed.length} 个来源暂未返回结果，其他来源仍可使用</>}
              className="book-source-failures"
            >
              {failed.map((source) => (
                <p key={source.source_id}>
                  {source.name}：{bookCode(source.code)}
                </p>
              ))}
            </DetailCard>
          )}
          <div className="book-card-list">
            {groups.map((candidates) => (
              <BookCard
                key={candidates[0].group_key}
                book={preferredBook(candidates)}
                alternatives={candidates}
                subscribed={view.subscriptions.some(
                  (sub) => sub.book_id === candidates[0].book_id,
                )}
                onPreview={onPreview}
                onSubscribe={onSubscribe}
              />
            ))}
          </div>
          {!groups.length && (
            <div className="book-empty">
              <BookOpen aria-hidden="true" />
              <h3>
                {result?.state === "running"
                  ? "正在查找各个来源…"
                  : result
                    ? "没有找到匹配的书籍"
                    : "先搜索，再读几页"}
              </h3>
              <p className="muted">
                {view.source_count
                  ? "输入书名或作者，也可以调整书源分组；本地已整理书籍会一起参与搜索。"
                  : "导入阅读 3 书源，或者先在本地书库导入自己的 EPUB 和 TXT。"}
              </p>
              {!view.source_count && (
                <button className="button" onClick={() => onTab("sources")}>
                  <Plus aria-hidden="true" />
                  添加书源
                </button>
              )}
            </div>
          )}
          {result?.next_offset != null && result.state !== "running" && (
            <button
              className="button"
              onClick={() => onSearch(query, group, result.next_offset!)}
            >
              继续搜索其余来源
            </button>
          )}
        </section>
        <aside className="media-card book-search-aside">
          <h3>你的阅读收藏</h3>
          <dl>
            <div>
              <dt>追更中的书</dt>
              <dd>
                {
                  view.subscriptions.filter((sub) => sub.state === "active")
                    .length
                }
              </dd>
            </div>
            <div>
              <dt>本地 EPUB</dt>
              <dd>{view.library_count}</dd>
            </div>
            <div>
              <dt>已导入书源</dt>
              <dd>{view.source_count}</dd>
            </div>
          </dl>
          <button className="button" onClick={() => onTab("subscriptions")}>
            查看我的订阅
          </button>
          <button className="button" onClick={() => onTab("library")}>
            打开本地书库
          </button>
          <p className="muted">
            {view.settings.directory
              ? "首次下载已有章节，后续只获取新增或缺失内容。"
              : "搜索和试读可以先用；下载前在整理设置中填写书籍目录。"}
          </p>
        </aside>
      </div>
    </>
  );
}
