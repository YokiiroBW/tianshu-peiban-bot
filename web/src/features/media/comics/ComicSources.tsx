import { DetailCard } from "../../../components/DetailCard";
import { useCallback, useEffect, useState } from "react";
import type { Kind, Media, Source } from "./types";
import { timestamp, word } from "../wording";
import {
  needsAttention,
  observations,
  SourceHealth,
  sourceAdvice,
} from "./SourceHealth";

type Plugin = {
  package: string;
  name: string;
  store_id: string;
  version: string;
  installed_version?: string;
  installed: boolean;
  languages: string[];
  state: string;
  code: string;
  duplicates?: number;
  restored?: boolean;
};
type ExtensionView = {
  policy: {
    enabled: boolean;
    revision: number;
    state: string;
    last_check_at?: number;
    next_check_at?: number;
    current_package?: string;
    code?: string;
  };
  stores: {
    id: string;
    name: string;
    url: string;
    state?: string;
    count?: number;
    code?: string;
  }[];
  counts: Record<string, number>;
  catalog_total: number;
  installed_count: number;
  plugins: Plugin[];
  total: number;
  next_offset: number | null;
};

const labels: Record<string, string> = {
  idle: "尚未启用",
  queued: "等待安装",
  running: "正在维护",
  ready: "检查完成",
  installed: "已安装",
  partial: "已完成，有项目需要查看",
  failed: "执行失败",
  incompatible: "引擎版本不兼容",
  placeholder: "仓库迁移提示，无可安装插件",
  origin_conflict: "来源冲突，保留原插件",
  source_conflict: "源 ID 重复，已跳过",
  unlisted: "仓库未收录，保留现有插件",
  busy: "等待源空闲",
  applying: "正在安装",
  verifying: "正在核对安装结果",
  restoring: "正在恢复旧版本",
  comic_extension_source_busy: "该源正在使用，稍后更新",
  comic_extension_download_failed: "插件文件下载失败，下轮重试",
  comic_extension_install_failed: "插件安装失败，下轮重试",
  comic_extension_update_restored: "更新未通过，旧版本已恢复",
  comic_extension_backup_unavailable: "无法读取旧插件备份，已保留旧版本",
  comic_extension_store_changed: "仓库签名发生变化，保留现有插件",
  comic_extension_verified: "安装与源加载已核对",
  comic_extension_current: "当前版本已安装",
};
const label = (value: string) => labels[value] || word(value);

export function ComicSources({
  media,
  kind,
  url,
}: {
  media: Media;
  kind: Kind;
  url?: string;
}) {
  const [view, setView] = useState<ExtensionView>();
  const [sources, setSources] = useState<Source[]>([]);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [sourcePage, setSourcePage] = useState(0);
  const [busy, setBusy] = useState(false);
  const [language, setLanguage] = useState("zh");
  const [verification, setVerification] = useState("all");
  const { request, command, setNotice } = media;
  const refresh = useCallback(async () => {
    const result = await request<ExtensionView>(
      "comic-extensions",
      "comics/extensions/view",
      { library_kind: kind, query, offset: page },
    );
    if (result) setView(result);
  }, [request, kind, query, page]);
  const refreshSources = useCallback(
    async (remote = false) => {
      const result = await request<{ sources: Source[] }>(
        "comic-managed-sources",
        remote ? "comics/sources/refresh" : "comics/sources/list",
        {},
      );
      if (result) setSources(result.sources);
    },
    [request],
  );
  useEffect(() => {
    void refresh();
  }, [refresh]);
  useEffect(() => {
    void refreshSources();
  }, [refreshSources]);
  useEffect(() => {
    setPage(0);
    setSourcePage(0);
  }, [kind, query]);
  useEffect(() => setSourcePage(0), [language, verification]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!document.hidden) {
        void refresh();
        void refreshSources();
      }
    }, 5000);
    return () => window.clearInterval(timer);
  }, [refresh, refreshSources]);
  const control = async (action: string) => {
    setBusy(true);
    try {
      const result = await command("comics/extensions/control", {
        action,
        expected_revision: view?.policy.revision ?? 0,
      });
      if (result) {
        setNotice(
          action === "pause"
            ? "已暂停自动维护，现有源和订阅继续保留。"
            : "已启用全部语言的兼容插件安装与每日自动更新，后台会继续执行。",
        );
        await refresh();
      }
    } finally {
      setBusy(false);
    }
  };
  const filtered = sources.filter(
    (s) =>
      (s.content_warning === "SAFE") === (kind === "general") &&
      (language !== "zh" || s.language.toLowerCase().startsWith("zh")) &&
      (verification !== "attention" || needsAttention(s)) &&
      (verification !== "reader" || observations(s).reader?.code === "ready") &&
      (verification !== "unchecked" ||
        Object.keys(observations(s)).every(
          (stage) => stage === "connectivity",
        )) &&
      (s.name + " " + s.language).toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <div className="comic-stack">
      <label>
        筛选插件与下载源
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="名称或语言"
        />
      </label>
      <section className="media-card">
        <h3>下载源（{filtered.length}）</h3>
        <div className="comic-controls">
          <label>
            下载源语言
            <select
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
            >
              <option value="all">全部语言</option>
              <option value="zh">中文（简体与繁体）</option>
            </select>
          </label>
          <label>
            验证状态
            <select
              value={verification}
              onChange={(e) => setVerification(e.target.value)}
            >
              <option value="all">全部状态</option>
              <option value="attention">有失败或待处理项</option>
              <option value="reader">已有样本通过阅读检查</option>
              <option value="unchecked">内容功能尚未验证</option>
            </select>
          </label>
        </div>
        <p className="muted">
          记录最近一次实际操作及时间。空搜索结果、入口可达和样本预览都不代表完整下载成功；归档与阅读按样本目标分别核对。
        </p>
        <div className="comic-source-list">
          {filtered.slice(sourcePage, sourcePage + 100).map((s) => (
            <article className="media-card" key={s.source_key}>
              <label className="comic-check">
                <input
                  type="checkbox"
                  checked={s.enabled}
                  disabled={busy}
                  onChange={async (e) => {
                    setBusy(true);
                    try {
                      const r = await command("comics/sources/control", {
                        source_key: s.source_key,
                        enabled: e.target.checked,
                        expected_revision: s.revision,
                      });
                      if (r) await refreshSources();
                    } finally {
                      setBusy(false);
                    }
                  }}
                />
                {s.name}
              </label>
              <p>
                {s.language} ·{" "}
                {s.available === false ? "插件当前未提供该源" : "已加载"}
                {s.content_warning === "MIXED" ? " · 内容混合" : ""}
              </p>
              <SourceHealth source={s} />
              {sourceAdvice(s) && (
                <p className="media-warning">{sourceAdvice(s)}</p>
              )}
              {needsAttention(s) && url && (
                <a
                  className="button"
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                >
                  前往 Suwayomi 处理来源
                </a>
              )}
            </article>
          ))}
        </div>
        <div className="comic-actions">
          <button
            className="button"
            disabled={sourcePage === 0}
            onClick={() => setSourcePage(Math.max(0, sourcePage - 100))}
          >
            上一页下载源
          </button>
          <button
            className="button"
            disabled={sourcePage + 100 >= filtered.length}
            onClick={() => setSourcePage(sourcePage + 100)}
          >
            下一页下载源
          </button>
        </div>
      </section>
      <p className="muted">
        {view?.policy.enabled
          ? "每日自动维护已启用"
          : "自动维护尚未启用或已暂停"}
        {view ? ` · ${view.installed_count} 个插件已安装` : ""}
      </p>
      <DetailCard
        title="插件仓库与每日维护"
        className="media-card comic-extension-details"
      >
        <section className="media-card">
          <div className="comic-heading">
            <h3>扩展仓库与自动更新</h3>
            <a className="button" href={url} target="_blank" rel="noreferrer">
              打开 Suwayomi 登录源账号
            </a>
          </div>
          <p>
            自动安装全部语言的兼容插件，每天检查新增插件与更新。重复源保留一个版本；普通漫画与私藏分别管理。
          </p>
          <div className="comic-actions">
            <button
              className="button primary"
              disabled={busy || !!media.busy}
              onClick={() =>
                void control(view?.policy.enabled ? "run" : "enable")
              }
            >
              {view?.policy.enabled
                ? "立即检查并安装更新"
                : "安装兼容插件并启用每日更新"}
            </button>
            {view?.policy.enabled && (
              <button
                className="button"
                disabled={busy || !!media.busy}
                onClick={() => void control("pause")}
              >
                暂停自动维护
              </button>
            )}
            <button
              className="button"
              onClick={() => void refreshSources(true)}
            >
              刷新下载源
            </button>
          </div>
          {view && (
            <>
              <p role="status">
                {view.policy.enabled ? "每日自动维护已启用" : "自动维护已暂停"}{" "}
                · {label(view.policy.state)} · {view.installed_count} /{" "}
                {view.catalog_total} 个插件已安装
                {view.policy.current_package
                  ? ` · 正在处理 ${view.policy.current_package}`
                  : ""}
              </p>
              <p className="muted">
                上次检查：{timestamp(view.policy.last_check_at ?? null)} ·
                下次检查：{timestamp(view.policy.next_check_at || null)}
              </p>
              <div className="comic-source-list">
                {view.stores.map((store) => (
                  <article className="media-card" key={store.id}>
                    <h4>{store.name}</h4>
                    <p>
                      {store.state ? label(store.state) : "等待连接"}
                      {store.count !== undefined
                        ? ` · ${store.count} 个目录项目`
                        : ""}
                    </p>
                    {store.code && <p>{label(store.code)}</p>}
                    <a href={store.url} target="_blank" rel="noreferrer">
                      查看仓库索引
                    </a>
                  </article>
                ))}
              </div>
            </>
          )}
        </section>
        <section className="media-card">
          <h3>
            {kind === "general" ? "普通漫画" : "本子与私藏"}插件{" "}
            {view ? `（${view.total}）` : ""}
          </h3>
          <p className="muted">
            安装完成表示插件已加载。站点的搜索、下载、登录情况在上方单独显示。
          </p>
          <div className="comic-source-list">
            {view?.plugins.map((p) => (
              <article className="media-card" key={p.package}>
                <h4>{p.name}</h4>
                <p>
                  {p.store_id} · {p.languages.join(" / ")}
                </p>
                <p>
                  {label(p.state)} · {p.installed_version || "未安装"} →{" "}
                  {p.version}
                </p>
                {p.code && <p className="muted">{label(p.code)}</p>}
                {!!p.duplicates && <p>已合并 {p.duplicates} 个重复仓库项目</p>}
              </article>
            ))}
          </div>
          <div className="comic-actions">
            <button
              className="button"
              disabled={page === 0}
              onClick={() => setPage(Math.max(0, page - 100))}
            >
              上一页插件
            </button>
            <button
              className="button"
              disabled={view?.next_offset == null}
              onClick={() => setPage(view?.next_offset ?? page)}
            >
              下一页插件
            </button>
          </div>
        </section>
      </DetailCard>
    </div>
  );
}
