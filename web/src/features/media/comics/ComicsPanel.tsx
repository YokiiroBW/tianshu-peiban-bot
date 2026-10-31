import { Drawer } from "../../../components/Drawer";
import { useCallback, useEffect, useState } from "react";
import { PanelsTopLeft, RefreshCw, Trash2 } from "lucide-react";
import { StatePanel } from "../../../components/StatePanel";
import { word, timestamp } from "../wording";
import { ComicSearch } from "./ComicSearch";
import { ComicSources } from "./ComicSources";
import { ComicDetail } from "./ComicDetail";
import { ComicSettings } from "./ComicSettings";
import { ComicImports } from "./ComicImports";
import { ComicCard } from "./ComicCard";
import type { Comic, Kind, Media, View } from "./types";
import { kinds } from "./types";
import { languageName } from "./language";
import "./comics.css";

const tabs = [
  ["search", "搜索漫画"],
  ["subscriptions", "订阅追更"],
  ["library", "漫画书库"],
  ["imports", "导入图片与 CBZ"],
  ["sources", "下载源"],
  ["settings", "连接与整理"],
] as const;
type Tab = (typeof tabs)[number][0];

export function ComicsPanel({ media }: { media: Media }) {
  const [view, setView] = useState<View>();
  const [tab, setTab] = useState<Tab>("search");
  const [kind, setKind] = useState<Kind>("general");
  const [selected, setSelected] = useState<Comic>();
  const [library, setLibrary] = useState<Comic[]>([]);
  const [libraryOffset, setLibraryOffset] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const { request, command, setNotice } = media;
  const refresh = useCallback(async () => {
    const result = await request<View>("comic-view", "comics/view", {});
    if (result) setView(result);
  }, [request]);
  useEffect(() => {
    if (!media.loading) void refresh();
  }, [media.loading, refresh]);
  const mutate = async (
    operation: string,
    body: object,
    notice = "已保存。",
  ) => {
    const result = await command("comics/" + operation, body);
    if (!result) return false;
    setNotice(notice);
    await refresh();
    return true;
  };
  const readLibrary = useCallback(
    async (offset = 0) => {
      const result = await request<{
        comics: Comic[];
        next_offset: number | null;
      }>("comic-library", "comics/library/view", {
        library_kind: kind,
        query,
        offset,
      });
      if (result) {
        setLibrary((old) =>
          offset ? [...old, ...result.comics] : result.comics,
        );
        setLibraryOffset(result.next_offset);
      }
    },
    [request, kind, query],
  );
  useEffect(() => {
    if (tab === "library") void readLibrary();
  }, [tab, readLibrary]);
  useEffect(() => {
    if (tab !== "subscriptions") return;
    const timer = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, 10000);
    return () => window.clearInterval(timer);
  }, [tab, refresh]);
  if (!view)
    return (
      <StatePanel
        kind={media.error ? "error" : "loading"}
        title={media.error || "正在读取漫画订阅"}
      >
        <button className="button" onClick={() => void refresh()}>
          重新读取
        </button>
      </StatePanel>
    );
  return (
    <div className="media-page comics-page">
      <div className="comic-heading">
        <div>
          <h2>
            <PanelsTopLeft aria-hidden="true" />
            漫画订阅
          </h2>
          <p className="muted">
            搜索来源、自动追更，将原图整理为 CBZ，在 Komga 接着读。
          </p>
        </div>
        <div className="comic-actions">
          <a className="button" href="#/media/jobs">
            下载中心
          </a>
          <button className="button" onClick={() => void refresh()}>
            <RefreshCw aria-hidden="true" />
            读取状态
          </button>
        </div>
      </div>
      {media.error && (
        <p className="media-message media-warning" role="alert">
          {media.error}
        </p>
      )}
      {media.notice && (
        <p className="media-message" role="status">
          {media.notice}
        </p>
      )}
      <nav className="comic-tabs" aria-label="漫画订阅功能">
        {tabs.map(([key, label]) => (
          <button
            key={key}
            aria-current={tab === key ? "page" : undefined}
            onClick={() => {
              setSelected(undefined);
              setTab(key);
            }}
          >
            {label}
          </button>
        ))}
      </nav>
      {selected && (
        <Drawer
          title={selected.metadata.title}
          variant="card"
          open={true}
          onClose={() => setSelected(undefined)}
        >
          {media.error && (
            <p className="media-message media-warning" role="alert">
              {media.error}
            </p>
          )}
          {media.notice && (
            <p className="media-message" role="status">
              {media.notice}
            </p>
          )}
          <ComicDetail
            key={selected.comic_id}
            comic={selected}
            media={media}
            mutate={mutate}
            onBack={() => setSelected(undefined)}
          />
        </Drawer>
      )}
      <div>
        {["search", "subscriptions", "library", "sources"].includes(tab) && (
          <div className="comic-controls">
            <label>
              收藏分类
              <select
                value={kind}
                onChange={(e) => setKind(e.target.value as Kind)}
              >
                {kinds.map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <span className="muted">
              {kind === "private"
                ? "单独浏览和归档私有收藏。"
                : `${view.counts.downloaded_chapters} 章已完成归档与阅读检查`}
            </span>
          </div>
        )}
        {tab === "search" &&
          (view.configured ? (
            <ComicSearch
              key={kind}
              media={media}
              kind={kind}
              onOpen={setSelected}
            />
          ) : (
            <div className="media-card">
              <h3>先连接 NAS 上的 Suwayomi</h3>
              <p>连接已有实例后，即可使用已安装的下载源搜索漫画。</p>
              <button className="button" onClick={() => setTab("settings")}>
                连接服务
              </button>
            </div>
          ))}
        {tab === "subscriptions" && (
          <div className="comic-stack">
            {view.subscriptions
              .filter((s) => s.comic.library_kind === kind)
              .map((s) => (
                <article className="media-card" key={s.comic_id}>
                  <div className="comic-heading">
                    <h3>
                      <button
                        className="comic-text-button"
                        onClick={() => setSelected(s.comic)}
                      >
                        {s.comic.metadata.title}
                      </button>
                    </h3>
                    <span>{s.state === "active" ? "追更中" : "已暂停"}</span>
                  </div>
                  <p className="muted">
                    {s.comic.source_name} · {s.comic.downloaded} /{" "}
                    {s.comic.chapter_count} 章 · 上次检查{" "}
                    {timestamp(s.last_scan_at ?? null)}
                  </p>
                  <p>{word(s.code)}</p>
                  {s.version_policy && (
                    <p className="muted">
                      固定版本：{languageName(s.version_policy.language)} ·{" "}
                      {s.version_policy.scanlator || "原来源版本"}
                    </p>
                  )}
                  <p className="muted">
                    {s.state === "active"
                      ? `下次检查 ${timestamp(s.next_scan_at || null)}`
                      : "已暂停，恢复后继续检查"}
                    {s.last_scan_at && s.code === "ready"
                      ? ` · 本次发现 ${s.last_added ?? 0} 章`
                      : ""}
                  </p>
                  <div className="comic-actions">
                    <button
                      className="button"
                      disabled={!!media.busy || s.state !== "active"}
                      onClick={() =>
                        void mutate(
                          "subscriptions/scan",
                          { comic_id: s.comic_id },
                          "已检查目录，新章节已加入队列。",
                        )
                      }
                    >
                      立即检查
                    </button>
                    <button
                      className="button"
                      disabled={!!media.busy}
                      onClick={() =>
                        void mutate("subscriptions/control", {
                          comic_id: s.comic_id,
                          action: s.state === "active" ? "pause" : "resume",
                          expected_revision: s.revision,
                        })
                      }
                    >
                      {s.state === "active" ? "暂停追更" : "继续追更"}
                    </button>
                    <button
                      className="button"
                      disabled={!!media.busy}
                      onClick={() =>
                        void mutate(
                          "subscriptions/control",
                          {
                            comic_id: s.comic_id,
                            action: "remove",
                            expected_revision: s.revision,
                          },
                          "订阅已删除，已入队任务、漫画归档和阅读进度保留。",
                        )
                      }
                    >
                      <Trash2 aria-hidden="true" />
                      删除订阅
                    </button>
                    <label>
                      检查周期
                      <select
                        value={s.interval_seconds}
                        onChange={(e) =>
                          void mutate("subscriptions/control", {
                            comic_id: s.comic_id,
                            action: "interval",
                            expected_revision: s.revision,
                            interval_seconds: Number(e.target.value),
                          })
                        }
                      >
                        <option value={3600}>每小时</option>
                        <option value={21600}>每 6 小时</option>
                        <option value={86400}>每天</option>
                        <option value={s.interval_seconds}>
                          当前：{s.interval_seconds / 3600} 小时
                        </option>
                      </select>
                    </label>
                  </div>
                </article>
              ))}
            {!view.subscriptions.some((s) => s.comic.library_kind === kind) && (
              <div className="comic-empty">
                <h3>还没有订阅</h3>
                <p>在漫画详情中选择订阅范围，之后会自动检查新章节。</p>
              </div>
            )}
          </div>
        )}
        {tab === "library" && (
          <>
            <label>
              查找书库
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="作品或作者"
              />
            </label>
            <div className="comic-grid">
              {library.map((c) => (
                <ComicCard
                  key={c.comic_id}
                  comic={c}
                  onOpen={() => setSelected(c)}
                />
              ))}
            </div>
            {!library.length && (
              <p className="comic-empty">
                这里还没有归档漫画。可以搜索下载，或导入 NAS 上的图片目录。
              </p>
            )}
            {libraryOffset !== null && (
              <button
                className="button"
                onClick={() => void readLibrary(libraryOffset)}
              >
                加载更多
              </button>
            )}
          </>
        )}
        {tab === "imports" && <ComicImports media={media} mutate={mutate} />}
        {tab === "sources" && (
          <ComicSources
            media={media}
            kind={kind}
            url={view.settings.connections.suwayomi?.url}
          />
        )}
        {tab === "settings" && (
          <ComicSettings
            key={view.settings.revision}
            settings={view.settings}
            media={media}
            mutate={mutate}
          />
        )}
      </div>
    </div>
  );
}
