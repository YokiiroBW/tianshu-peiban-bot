import { DetailCard } from "../../../components/DetailCard";
import { useEffect, useState } from "react";
import type { Comic, Kind, Media, SearchResult, Source } from "./types";
import { ComicCard } from "./ComicCard";
import { word } from "../wording";
import { chineseResult } from "./language";

export function ComicSearch({
  media,
  kind,
  onOpen,
}: {
  media: Media;
  kind: Kind;
  onOpen: (comic: Comic) => void;
}) {
  const [query, setQuery] = useState("");
  const [aliases, setAliases] = useState("");
  const [aliasSubject, setAliasSubject] = useState("");
  const [includeUnknown, setIncludeUnknown] = useState(false);
  const [source, setSource] = useState("");
  const [sources, setSources] = useState<Source[]>([]);
  const [result, setResult] = useState<SearchResult>();
  const [starting, setStarting] = useState(false);
  const { request } = media;
  const aliasNames = aliases
    .split("\n")
    .map((v) => v.trim())
    .filter(Boolean);
  useEffect(() => {
    void request<{ sources: Source[] }>(
      "comic-search-sources",
      "comics/sources/list",
      {},
    ).then((r) => {
      if (r) setSources(r.sources);
    });
  }, [request]);
  useEffect(() => {
    if (result?.state !== "completed") return;
    void request<{ sources: Source[] }>(
      "comic-search-sources",
      "comics/sources/list",
      {},
    ).then((value) => {
      if (value) setSources(value.sources);
    });
  }, [request, result?.state]);
  useEffect(() => {
    if (result?.state !== "running") return;
    const timer = window.setTimeout(() => {
      void request<SearchResult>("comic-search-poll", "comics/search/results", {
        search_id: result.search_id,
      }).then((r) => {
        if (r) setResult(r);
      });
    }, 900);
    return () => window.clearTimeout(timer);
  }, [result, request]);
  const search = async (page = 1, subject = aliasSubject) => {
    setStarting(true);
    if (result?.state === "running")
      await request("comic-search-cancel", "comics/search/cancel", {
        search_id: result.search_id,
      });
    const r = await request<SearchResult>(
      "comic-search-start",
      "comics/search/start",
      {
        query,
        language: "zh",
        aliases: aliasNames,
        alias_subject: subject,
        library_kind: kind,
        source_ids: source ? [source] : [],
        page,
      },
    );
    if (r) setResult(r);
    setStarting(false);
  };
  const available = sources.filter(
    (s) =>
      s.enabled &&
      s.available !== false &&
      (/^zh(?:[-_]|$)/i.test(s.language) || s.language === "all") &&
      (s.content_warning === "SAFE") === (kind === "general"),
  );
  const visibleComics =
    result?.comics.filter((c) => includeUnknown || chineseResult(c)) ?? [];
  const unknownCount =
    result?.comics.filter((c) => !chineseResult(c)).length ?? 0;
  return (
    <div className="comic-stack">
      <form
        className="media-card comic-search"
        onSubmit={(e) => {
          e.preventDefault();
          void search();
        }}
      >
        <label>
          作品或作者
          <input
            required
            maxLength={200}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setAliasSubject("");
            }}
            placeholder="中文、英文、日文作品名或作者"
          />
        </label>
        <label>
          下载源
          <select value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">聚合中文与多语言源</option>
            {available.map((s) => (
              <option key={s.source_id} value={s.source_id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <button
          className="button primary"
          disabled={starting || !query.trim() || aliasNames.length > 2}
        >
          搜索
        </button>
        <p className="muted">
          搜索范围：中文（简体与繁体）。自动查找中日英别名，覆盖中文源及多语言源中的中文版本。
        </p>
        <DetailCard title="添加其他译名或原名">
          <label>
            作品别名（可选，每行一个，最多 2 个）
            <textarea
              value={aliases}
              maxLength={402}
              rows={2}
              onChange={(e) => setAliases(e.target.value)}
              placeholder={"例如：航海王\nOne Piece"}
            />
          </label>
          {aliasNames.length > 2 && (
            <p role="alert">每次最多添加 2 个其他译名，请分次搜索。</p>
          )}
        </DetailCard>
        <p className="muted">
          中文判断参考来源、作品和章节资料；语言待确认的结果单独查看，正文可先预览。
        </p>
      </form>
      {result?.alias_resolution && (
        <div className="media-card" aria-label="自动别名识别">
          {result.alias_resolution.state === "resolving" ? (
            <p>正在查找作品的中文译名与原名…</p>
          ) : result.alias_resolution.selected ? (
            <>
              <p>已识别：{result.alias_resolution.selected.title}</p>
              <p>同时检索：{result.alias_resolution.names.join(" / ")}</p>
              <a
                href={result.alias_resolution.selected.url}
                target="_blank"
                rel="noreferrer"
              >
                资料来源：{result.alias_resolution.selected.provider}
              </a>
            </>
          ) : (
            <p>
              {result.alias_resolution.state === "unavailable"
                ? "别名资料暂不可用，继续按原名和手填别名搜索。"
                : result.alias_resolution.state === "ambiguous"
                  ? "发现同名作品，请选择对应作品以使用其译名。"
                  : "尚未确认对应译名，继续按原名和手填别名搜索。"}
            </p>
          )}
          {!result.alias_resolution.selected &&
            !!result.alias_resolution.candidates.length && (
              <DetailCard title="查看可能对应的作品">
                {result.alias_resolution.candidates.map((candidate) => (
                  <p key={candidate.id}>
                    {candidate.title} ·{" "}
                    {candidate.authors.join(" / ") || "作者未提供"}{" "}
                    <button
                      className="button"
                      disabled={starting}
                      onClick={() => {
                        setAliasSubject(candidate.id);
                        void search(1, candidate.id);
                      }}
                    >
                      使用此作品的译名
                    </button>
                  </p>
                ))}
              </DetailCard>
            )}
        </div>
      )}
      {result && (
        <div className="comic-heading">
          <p role="status">
            {result.state === "running"
              ? "正在搜索"
              : result.state === "cancelled"
                ? "搜索已停止"
                : result.state === "failed"
                  ? "搜索未完整结束"
                  : "搜索完成"}{" "}
            · {result.sources.length} / {result.total} 个源 ·{" "}
            {visibleComics.length} 个来源版本
          </p>
          {result.state === "running" && (
            <button
              className="button"
              onClick={() => {
                void request<SearchResult>(
                  "comic-search-cancel",
                  "comics/search/cancel",
                  { search_id: result.search_id },
                ).then((r) => {
                  if (r) setResult(r);
                });
              }}
            >
              停止搜索
            </button>
          )}
        </div>
      )}
      {result && (
        <DetailCard title="各源结果与失败原因">
          <p>本次写法：{result.queries?.join(" / ") || result.query}</p>
          <ul>
            {result.sources.map((s) => (
              <li key={s.source_id}>
                {s.name}：{word(s.code)} · {s.count} 个结果
                {s.search_mode === "catalogue" ? " · 按源目录筛选" : ""}
                {s.language_filter === "applied"
                  ? " · 已请求中文过滤"
                  : s.language_filter === "not_provided" ||
                      s.language_filter === "unavailable"
                    ? " · 逐作品核对语言"
                    : ""}
                {s.filtered ? `，排除 ${s.filtered} 个不相关结果` : ""}
                {!!s.query_errors?.length && (
                  <span> · {s.query_errors.map(word).join("；")}</span>
                )}
              </li>
            ))}
          </ul>
        </DetailCard>
      )}
      {unknownCount > 0 && (
        <label>
          <input
            type="checkbox"
            checked={includeUnknown}
            onChange={(e) => setIncludeUnknown(e.target.checked)}
          />
          显示语言待确认的 {unknownCount} 个版本
        </label>
      )}
      <div className="comic-grid">
        {visibleComics.map((c) => (
          <ComicCard key={c.comic_id} comic={c} onOpen={() => onOpen(c)} />
        ))}
      </div>
      {!result && (
        <div className="comic-empty">
          <h3>找到想看的漫画</h3>
          <p>同名作品的不同来源会分别保留。先预览，再选择版本下载和追更。</p>
        </div>
      )}
      {result?.state === "completed" && !visibleComics.length && (
        <p className="comic-empty">
          未找到已标明中文的匹配版本。可查看语言待确认结果、补充译名或选择对应作品；具体失败原因见上方源状态。
        </p>
      )}
      {result?.state === "completed" &&
        result.sources.some((s) => s.has_next_page) && (
          <button
            className="button"
            onClick={() => void search(result.page + 1)}
          >
            查看下一页来源结果
          </button>
        )}
    </div>
  );
}
