import { DetailCard } from "../../../components/DetailCard";
import { useEffect, useRef, useState } from "react";
import { Download, Globe, Plus, RefreshCw, Search } from "lucide-react";
import { Drawer } from "../../../components/Drawer";
import type { BookSource, BookFeed, BooksView } from "./types";
import { bookCode, bookFeedCode } from "./presentation";
import { timestamp } from "../wording";

export type SourceImportResult = {
  imported: number;
  invalid: number;
  results: { index: number; state: string; code?: string }[];
};

export function BookSources({
  view,
  busy,
  onWarehouse,
  onImport,
  onFeed,
  onFeedAction,
  onToggle,
  onExport,
  onLoad,
}: {
  view: BooksView;
  busy: boolean;
  onWarehouse: () => void;
  onImport: (sources: unknown) => Promise<SourceImportResult | undefined>;
  onFeed: (url: string) => Promise<boolean>;
  onFeedAction: (feed: BookFeed, action: string, interval?: number) => void;
  onToggle: (source: BookSource) => Promise<BookSource | undefined>;
  onExport: (source: BookSource) => void;
  onLoad: (
    offset: number,
    query: string,
  ) => Promise<{ sources: BookSource[]; total: number } | undefined>;
}) {
  const [open, setOpen] = useState(false);
  const [raw, setRaw] = useState("");
  const [feedback, setFeedback] = useState("");
  const [report, setReport] = useState<SourceImportResult | null>(null);
  const [url, setUrl] = useState("");
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState(view.sources);
  const [total, setTotal] = useState(view.source_count);
  const fileInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!query) {
      setRows((current) => [
        ...view.sources,
        ...current.filter(
          (source) =>
            !view.sources.some(
              (updated) => updated.source_id === source.source_id,
            ),
        ),
      ]);
      setTotal(view.source_count);
    }
  }, [view.sources, view.source_count, query]);
  async function load(offset: number) {
    const result = await onLoad(offset, query);
    if (result) {
      setRows((current) =>
        offset ? [...current, ...result.sources] : result.sources,
      );
      setTotal(result.total);
    }
  }
  async function importRules() {
    setFeedback("");
    setReport(null);
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      setFeedback("JSON 格式有误，请检查后再导入；已有书源保持不变。");
      return;
    }
    const result = await onImport(parsed);
    if (result) {
      setReport(result);
      setFeedback(
        `导入或更新 ${result.imported} 项，${result.invalid} 项格式不符合要求。`,
      );
    }
  }
  return (
    <>
      <div className="book-section-heading">
        <div>
          <h3>书源与规则集合</h3>
          <p className="muted">规则订阅更新书源；书籍订阅负责追更章节。</p>
        </div>
        <div className="book-controls">
          <button className="button primary" onClick={onWarehouse}>
            <Globe aria-hidden="true" />
            打开源仓库
          </button>
          <button className="button" onClick={() => setOpen(true)}>
            <Plus aria-hidden="true" />
            导入书源
          </button>
        </div>
      </div>
      <DetailCard title={"添加书源集合订阅"}>
        <form
          className="media-card book-feed-form"
          onSubmit={(event) => {
            event.preventDefault();
            void onFeed(url).then((saved) => {
              if (saved) setUrl("");
            });
          }}
        >
          <label>
            订阅规则集合
            <input
              type="text"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="粘贴书源 JSON 直链"
              required
            />
          </label>
          <button className="button" disabled={busy}>
            添加规则订阅
          </button>
          <p className="muted">
            可填写 JSON
            直链、源仓库详情页或阅读一键导入链接。也可打开内置源仓库直接选择。远端失联时继续保留本地规则。
          </p>
        </form>
      </DetailCard>
      {view.feeds.length > 0 && (
        <div className="media-stack book-feeds">
          {view.feeds.map((feed) => (
            <div className="media-card book-feed" key={feed.feed_id}>
              <div>
                <strong>{feed.title || feed.url}</strong>
                <p className="muted">
                  {feed.state === "paused" ? "已暂停" : bookFeedCode(feed.code)}{" "}
                  ·{" "}
                  {feed.last_scan_at
                    ? timestamp(feed.last_scan_at)
                    : "尚未更新"}
                  {feed.result ? ` · ${feed.result.imported} 个书源` : ""}
                </p>
                {feed.result && (
                  <p
                    className={feed.result.invalid ? "media-warning" : "muted"}
                  >
                    {feed.result.added !== undefined
                      ? `本次新增 ${feed.result.added} · 更新 ${feed.result.updated} · 未变 ${feed.result.unchanged}`
                      : ""}
                    {feed.result.invalid > 0 &&
                      ` · 跳过 ${feed.result.invalid} 项`}
                  </p>
                )}
                {feed.result?.invalid ? (
                  <DetailCard title="查看跳过原因">
                    {Object.entries(feed.result.reasons ?? {}).map(
                      ([code, count]) => (
                        <p key={code}>
                          {bookCode(code)}：{count} 项
                        </p>
                      ),
                    )}
                  </DetailCard>
                ) : null}
                {feed.state === "active" && (
                  <p className="muted">
                    下次检查：{timestamp(feed.next_scan_at)}
                  </p>
                )}
                <DetailCard title={"集合更新频率设置"}>
                  <label className="book-feed-interval">
                    更新频率
                    <select
                      aria-label={`${feed.title || feed.url} 更新频率`}
                      value={feed.interval_seconds}
                      disabled={busy}
                      onChange={(event) =>
                        onFeedAction(
                          feed,
                          "interval",
                          Number(event.target.value),
                        )
                      }
                    >
                      <option value={3600}>每小时</option>
                      <option value={21600}>每 6 小时</option>
                      <option value={86400}>每天</option>
                      <option value={604800}>每周</option>
                      {![3600, 21600, 86400, 604800].includes(
                        feed.interval_seconds,
                      ) && (
                        <option value={feed.interval_seconds}>
                          每 {feed.interval_seconds / 3600} 小时
                        </option>
                      )}
                    </select>
                  </label>
                </DetailCard>
              </div>
              <div className="book-controls">
                <button
                  className="button"
                  disabled={busy}
                  onClick={() => onFeedAction(feed, "refresh")}
                >
                  <RefreshCw aria-hidden="true" />
                  立即更新
                </button>
                <button
                  className="button"
                  disabled={busy}
                  onClick={() =>
                    onFeedAction(
                      feed,
                      feed.state === "active" ? "pause" : "resume",
                    )
                  }
                >
                  {feed.state === "active" ? "暂停" : "恢复"}
                </button>
                <button
                  className="button"
                  disabled={busy}
                  onClick={() => onFeedAction(feed, "remove")}
                >
                  移除订阅
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      <form
        className="book-library-search"
        onSubmit={(event) => {
          event.preventDefault();
          void load(0);
        }}
      >
        <label className="sr-only" htmlFor="book-source-query">
          查找书源
        </label>
        <input
          id="book-source-query"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="按名称或分组查找书源"
        />
        <button className="button">
          <Search aria-hidden="true" />
          查找
        </button>
        <span className="muted">{total} 个书源</span>
      </form>
      <div className="media-stack">
        {rows.map((source) => (
          <article className="media-card book-source" key={source.source_id}>
            <div>
              <h4>{source.name}</h4>
              <p className="muted">
                {source.group || "未分组"} · {source.url}
              </p>
              <div className="book-card-facts">
                <span className="book-tag">
                  {source.enabled ? "已启用" : "已停用"}
                </span>
                <span>
                  {source.code === "ready"
                    ? "最近请求成功，正文需按书验证"
                    : bookCode(source.code)}
                </span>
                {source.capabilities.javascript && <span>含脚本规则</span>}
                {source.capabilities.webview_http_fallback && (
                  <span>尝试普通网页读取</span>
                )}
                {source.capabilities.unsupported.length > 0 && (
                  <span className="media-warning">部分能力待支持</span>
                )}
              </div>
            </div>
            <div className="book-controls">
              <button
                className="button"
                disabled={busy}
                onClick={() => {
                  void onToggle(source).then((updated) => {
                    if (updated)
                      setRows((current) =>
                        current.map((row) =>
                          row.source_id === updated.source_id ? updated : row,
                        ),
                      );
                  });
                }}
              >
                {source.enabled ? "停用" : "启用"}
              </button>
              <button className="button" onClick={() => onExport(source)}>
                <Download aria-hidden="true" />
                导出规则
              </button>
            </div>
          </article>
        ))}
      </div>
      {!rows.length && (
        <div className="book-empty">
          <h3>{query ? "没有匹配的书源" : "还没有导入书源"}</h3>
          <p className="muted">
            打开源仓库即可浏览并添加，也可以上传 JSON 文件或订阅规则链接。
          </p>
        </div>
      )}
      {rows.length < total && (
        <button className="button" onClick={() => void load(rows.length)}>
          加载更多书源
        </button>
      )}
      <Drawer
        variant="card"
        title="导入阅读书源"
        open={open}
        onClose={() => setOpen(false)}
      >
        <div className="book-source-import media-stack">
          <p className="muted">
            支持阅读 3
            的单个书源或书源数组。原始规则会保留，各来源独立显示实际可用状态。
          </p>
          <button className="button" onClick={() => fileInput.current?.click()}>
            选择 JSON / TXT 文件
          </button>
          <input
            ref={fileInput}
            type="file"
            accept=".json,.txt,application/json,text/plain"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) {
                if (file.size > 32 * 1024 * 1024)
                  setFeedback("规则文件超过 32 MiB，请分批导入。");
                else void file.text().then(setRaw);
              }
              event.target.value = "";
            }}
          />
          <label>
            规则 JSON
            <textarea
              rows={14}
              value={raw}
              onChange={(event) => setRaw(event.target.value)}
              spellCheck={false}
              placeholder="粘贴从书源仓库获得的 JSON"
            />
          </label>
          {feedback && (
            <p
              role="status"
              className={report?.invalid ? "media-warning" : "muted"}
            >
              {feedback}
            </p>
          )}
          {report && report.invalid > 0 && (
            <DetailCard title="查看未导入条目">
              {report.results
                .filter((item) => item.state === "invalid")
                .slice(0, 50)
                .map((item) => (
                  <p key={item.index}>
                    第 {item.index + 1} 项：
                    {bookCode(item.code ?? "book_source_input_invalid")}
                  </p>
                ))}
            </DetailCard>
          )}
          <button
            className="button primary"
            disabled={busy || !raw.trim()}
            onClick={() => void importRules()}
          >
            导入规则
          </button>
        </div>
      </Drawer>
    </>
  );
}
