import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, Globe, RefreshCw, Search } from "lucide-react";
import { Drawer } from "../../../components/Drawer";
import type { MediaController } from "../useMediaController";
import type { BookFeed, WarehouseEntry, WarehousePage } from "./types";
import { bookFeedCode } from "./presentation";

type Query = {
  kind: "source" | "collection";
  query: string;
  page: number;
  order: string;
};
const initial: Query = { kind: "source", query: "", page: 1, order: "time" };

export function BookWarehouse({
  media,
  feeds,
  onClose,
  onSubscribe,
}: {
  media: MediaController;
  feeds: BookFeed[];
  onClose: () => void;
  onSubscribe: (
    entries: WarehouseEntry[],
    interval: number,
  ) => Promise<BookFeed[] | undefined>;
}) {
  const current = useRef(media);
  current.current = media;
  const [query, setQuery] = useState(initial);
  const [keywords, setKeywords] = useState("");
  const [page, setPage] = useState<WarehousePage | null>(null);
  const [detail, setDetail] = useState<WarehouseEntry | null>(null);
  const [selected, setSelected] = useState<Record<string, WarehouseEntry>>({});
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [addingFailed, setAddingFailed] = useState(false);
  const [notice, setNotice] = useState("");
  const [interval, setInterval] = useState(86400);
  const version = useRef(0);
  const registered = new Map(
    feeds
      .filter((feed) => feed.warehouse_key)
      .map((feed) => [feed.warehouse_key, feed]),
  );

  const browse = useCallback(async (next: Query) => {
    const mine = ++version.current;
    current.current.cancel("books-warehouse-detail");
    setQuery(next);
    setDetail(null);
    setLoading(true);
    setFailed(false);
    const result = await current.current.request<WarehousePage>(
      "books-warehouse-list",
      "books/warehouse/browse",
      next,
    );
    if (mine !== version.current) return;
    setPage(result ?? null);
    setFailed(!result);
    setLoading(false);
  }, []);

  useEffect(() => {
    void browse(initial);
    return () => {
      version.current += 1;
      current.current.cancel("books-warehouse-list");
      current.current.cancel("books-warehouse-detail");
    };
  }, [browse]);

  async function open(entry: WarehouseEntry) {
    const mine = ++version.current;
    current.current.cancel("books-warehouse-list");
    setDetail(entry);
    setFailed(false);
    setLoading(true);
    const result = await current.current.request<WarehouseEntry>(
      "books-warehouse-detail",
      "books/warehouse/detail",
      { kind: entry.kind, entry_id: entry.entry_id },
    );
    if (mine !== version.current) return;
    if (result) setDetail(result);
    setFailed(!result);
    setLoading(false);
  }

  function select(entries: WarehouseEntry[], checked: boolean) {
    const next = { ...selected };
    for (const entry of entries) {
      if (!checked) delete next[entry.key];
      else if (!registered.has(entry.key)) next[entry.key] = entry;
    }
    if (Object.keys(next).length > 100) {
      setNotice("每次最多添加 100 个条目，请先添加已选条目。");
      return;
    }
    setSelected(next);
    setNotice("");
  }

  async function subscribe(entries: WarehouseEntry[]) {
    setAddingFailed(false);
    const result = await onSubscribe(entries, interval);
    if (!result) {
      setAddingFailed(true);
      return;
    }
    setSelected((before) => {
      const after = { ...before };
      for (const entry of entries) delete after[entry.key];
      return after;
    });
    setNotice(
      `已保存 ${result.length} 个规则订阅，后台正在获取书源。关闭仓库后可继续查看更新结果。`,
    );
  }

  const chosen = Object.values(selected).filter(
    (entry) => !registered.has(entry.key),
  );
  const candidates =
    page?.entries.filter((entry) => !registered.has(entry.key)) ?? [];
  return (
    <Drawer variant="card" title="内置源仓库" open onClose={onClose}>
      <div className="book-warehouse media-stack">
        <div className="warehouse-address">
          <Globe aria-hidden="true" />
          <span>
            {detail?.page_url ?? page?.page_url ?? "https://www.yckceo.com"}
          </span>
        </div>
        <p className="muted">
          直接浏览源仓库，选择单个书源或合集，添加后自动同步规则。
        </p>
        {detail ? (
          <>
            <button
              className="button warehouse-back"
              onClick={() => {
                version.current += 1;
                current.current.cancel("books-warehouse-detail");
                setDetail(null);
                setLoading(false);
                setFailed(false);
              }}
            >
              <ArrowLeft aria-hidden="true" />
              返回仓库列表
            </button>
            <article className="media-card warehouse-detail">
              <span className="book-tag">
                {detail.kind === "source" ? "单个书源" : "书源合集"}
              </span>
              <h3>{detail.title}</h3>
              <p className="muted">{detail.badges.join(" · ")}</p>
              <p className="warehouse-description">{detail.description}</p>
              <p className="muted">
                订阅会跟踪此条目的更新。作者另发的新条目需重新选择。
              </p>
              {registered.has(detail.key) ? (
                <p role="status">
                  已订阅 ·{" "}
                  {registered.get(detail.key)?.state === "paused"
                    ? "自动更新已暂停"
                    : bookFeedCode(registered.get(detail.key)!.code)}
                </p>
              ) : (
                <button
                  className="button primary"
                  disabled={!!media.busy || loading || failed}
                  onClick={() => void subscribe([detail])}
                >
                  添加此{detail.kind === "source" ? "书源" : "合集"}并自动更新
                </button>
              )}
            </article>
          </>
        ) : (
          <>
            <div className="book-controls" aria-label="仓库分类">
              {(
                [
                  ["source", "单个书源"],
                  ["collection", "书源合集"],
                ] as const
              ).map(([kind, label]) => (
                <button
                  key={kind}
                  className={`button ${query.kind === kind ? "primary" : ""}`}
                  aria-pressed={query.kind === kind}
                  onClick={() => void browse({ ...query, kind, page: 1 })}
                >
                  {label}
                </button>
              ))}
            </div>
            <form
              className="warehouse-search"
              onSubmit={(event) => {
                event.preventDefault();
                void browse({ ...query, query: keywords, page: 1 });
              }}
            >
              <label className="sr-only" htmlFor="warehouse-query">
                仓库搜索
              </label>
              <input
                id="warehouse-query"
                value={keywords}
                maxLength={200}
                onChange={(event) => setKeywords(event.target.value)}
                placeholder="搜索仓库中的书源或合集"
              />
              <select
                aria-label="仓库排序"
                value={query.order}
                onChange={(event) =>
                  void browse({ ...query, order: event.target.value, page: 1 })
                }
              >
                <option value="time">最近更新</option>
                <option value="down">下载最多</option>
              </select>
              <button className="button" disabled={loading}>
                <Search aria-hidden="true" />
                搜索仓库
              </button>
            </form>
          </>
        )}
        {loading && (
          <p role="status" className="muted">
            正在读取源仓库…
          </p>
        )}
        {failed && (
          <div className="media-card" role="alert">
            <p>{media.error || "暂时无法读取源仓库，请稍后重试。"}</p>
            <button
              className="button"
              onClick={() => void (detail ? open(detail) : browse(query))}
            >
              <RefreshCw aria-hidden="true" />
              重试读取
            </button>
          </div>
        )}
        {!detail && !loading && !failed && page && (
          <>
            <div className="warehouse-results-heading">
              <label>
                <input
                  type="checkbox"
                  checked={
                    candidates.length > 0 &&
                    candidates.every((entry) => !!selected[entry.key])
                  }
                  disabled={!candidates.length}
                  onChange={(event) => select(candidates, event.target.checked)}
                />
                选择本页未订阅条目
              </label>
              <span className="muted">
                第 {page.page} 页 · 本页 {page.entries.length} 项
              </span>
            </div>
            <div className="warehouse-grid">
              {page.entries.map((entry) => {
                const feed = registered.get(entry.key);
                return (
                  <article
                    className="media-card warehouse-entry"
                    key={entry.key}
                  >
                    <input
                      type="checkbox"
                      aria-label={`选择 ${entry.title}`}
                      checked={!!feed || !!selected[entry.key]}
                      disabled={!!feed}
                      onChange={(event) =>
                        select([entry], event.target.checked)
                      }
                    />
                    <div>
                      <button
                        className="warehouse-title"
                        onClick={() => void open(entry)}
                      >
                        {entry.title}
                      </button>
                      <p className="muted">{entry.updated}</p>
                      <div className="warehouse-badges">
                        {entry.badges.map((badge, index) => (
                          <span key={index}>{badge}</span>
                        ))}
                      </div>
                      {feed && (
                        <p className="warehouse-subscribed">
                          已订阅 ·{" "}
                          {feed.state === "paused"
                            ? "已暂停"
                            : bookFeedCode(feed.code)}
                        </p>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
            {!page.entries.length && (
              <div className="book-empty">
                <h3>没有找到匹配条目</h3>
                <p className="muted">换个关键词，或切换到书源合集。</p>
              </div>
            )}
            <nav className="book-controls" aria-label="仓库分页">
              <button
                className="button"
                disabled={page.page <= 1}
                onClick={() => void browse({ ...query, page: page.page - 1 })}
              >
                上一页
              </button>
              <span>第 {page.page} 页</span>
              <button
                className="button"
                disabled={!page.has_next}
                onClick={() => void browse({ ...query, page: page.page + 1 })}
              >
                下一页
              </button>
            </nav>
          </>
        )}
        <div className="warehouse-footer">
          <label>
            自动更新频率
            <select
              aria-label="自动更新频率"
              value={interval}
              onChange={(event) => setInterval(Number(event.target.value))}
            >
              <option value={3600}>每小时</option>
              <option value={21600}>每 6 小时</option>
              <option value={86400}>每天</option>
              <option value={604800}>每周</option>
            </select>
          </label>
          {!detail && (
            <>
              <span>已选 {chosen.length} 项</span>
              <button
                className="button"
                disabled={!chosen.length || !!media.busy}
                onClick={() => setSelected({})}
              >
                清空选择
              </button>
              <button
                className="button primary"
                disabled={!chosen.length || !!media.busy}
                onClick={() => void subscribe(chosen)}
              >
                添加所选并自动更新
              </button>
            </>
          )}
          {notice && <p role="status">{notice}</p>}
          {addingFailed && (
            <p role="alert">{media.error || "未能确认添加结果，请重试。"}</p>
          )}
          <p className="muted">
            跟踪已选条目的规则变化；保留本地启停设置。导入成功后仍需实际搜索或预览验证书源可用性。
          </p>
        </div>
      </div>
    </Drawer>
  );
}
