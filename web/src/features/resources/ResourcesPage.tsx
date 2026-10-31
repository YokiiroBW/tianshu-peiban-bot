import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ChevronRight,
  CornerLeftUp,
  Folder,
  FolderOpen,
  RefreshCw,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";
import { StatePanel } from "../../components/StatePanel";
import { StatusRail } from "../../components/StatusRail";
import { breadcrumb, libraryState, type Entry, type Library } from "./api";
import { DetailPanel, LibrarySummary } from "./DetailPanel";
import { EntryList } from "./EntryList";
import { useAssetPage } from "./useAssetPage";
import "./resources.css";

const connectionCode: Record<string, string> = {
  ready: "已选择",
  assets_disabled: "未登记",
  operator_not_authorized: "身份无权限",
  no_asset_connections: "没有可用连接",
  connection_required: "尚未选择",
};

function railFor(code: string) {
  if (code === "ready")
    return {
      tone: "blue" as const,
      label: "已连接",
      detail: "读取使用服务器登记的只读身份。",
    };
  if (code === "connection_required")
    return {
      tone: "gray" as const,
      label: "尚未选择连接",
      detail: "选择连接后才会读取资产库列表。",
    };
  return {
    tone: "red" as const,
    label: connectionCode[code] ?? "不可用",
    detail: "这个部署当前无法提供只读资产页。",
  };
}

/**
 * 资料与资源 → 资产库（`/#/resources/assets`）。
 *
 * A read-only window over the peer's authorized index. The page asks, shows what came back, and
 * states what it does not know: an offline index is not missing files, an unverified original is
 * not an available one, and a protocol without a preview port is not a page with thumbnails.
 */
export default function ResourcesPage() {
  const page = useAssetPage();
  const [query, setQuery] = useState("");
  const [searchScope, setSearchScope] = useState<"library" | "all">("library");
  const [selected, setSelected] = useState("");
  const heading = useRef<HTMLHeadingElement>(null);
  const searchBox = useRef<HTMLInputElement>(null);
  const trigger = useRef<HTMLElement | null>(null);

  useEffect(() => {
    document.title = "资产库 · 天枢";
    heading.current?.focus({ preventScroll: true });
  }, []);

  const state = page.state;
  const rail = railFor(state?.code ?? "");
  const connection = state?.connections.find(
    (item) => item.connection_id === state.connection,
  );
  const preview = state?.preview ?? page.listing?.preview ?? null;
  const crumbs = breadcrumb(page.scope.parent);
  const entries = page.listing?.entries ?? [];
  const hits = page.results?.hits ?? [];
  const busy = page.loading;
  const listing = page.scope.query !== null ? page.results : page.listing;
  const more = listing?.page.has_more ? listing.page.next_cursor : null;
  /**
   * The library list has its own continuation. Without it, a connection whose authorized libraries
   * do not fit in one page would have libraries that no entry point can reach: the library list is
   * the only place a library can be opened from.
   */
  const moreLibraries = page.librariesPage?.has_more
    ? page.librariesPage.next_cursor
    : null;

  /** Opening a directory stays inside the library the listing belongs to. */
  const openDirectory = (entry: Entry) => {
    trigger.current = null;
    void page.open(page.scope.library, entry.relative_path);
  };

  const selectEntry = (entry: Entry) => {
    setSelected(entry.entry_id);
    void page.inspect(entry);
  };

  const selectLibrary = (library: Library) => {
    setSelected("");
    setQuery("");
    void page.open(library, "");
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!query.trim()) {
      page.clearSearch();
      return;
    }
    setSelected("");
    page.search(query, searchScope);
  };

  const status = (() => {
    if (page.absent)
      return (
        <StatePanel kind="unconfigured" title="这个部署没有只读资产页">
          <p>
            {page.absent === "assets_disabled"
              ? "部署设置里没有登记资产页，因此这里没有可读取的资产连接。"
              : `当前状态：${page.absent}。`}
          </p>
        </StatePanel>
      );
    if (!page.authenticated)
      return (
        <StatePanel kind="unconfigured" title="需要先登录">
          <p>登录后才能读取资产库；这里不会显示任何未经授权的条目。</p>
          <p>
            <a className="button" href="#/login?next=%23%2Fresources%2F1">
              前往登录入口
            </a>
          </p>
        </StatePanel>
      );
    if (!state?.connection)
      return (
        <StatePanel kind="empty" title="请选择资产连接">
          <p>
            选择要查看的连接后会读取它的库列表。本页不会自动跨所有连接搜索。
          </p>
        </StatePanel>
      );
    return null;
  })();
  const browsing = !status && !page.failure && !busy && !!page.scope.library;
  const freshness = state && (
    <section className="asset-freshness" aria-label="读取说明">
      <h3 className="asset-section-title">读取说明</h3>
      <ul>
        <li>
          每次打开库、进入目录、提交搜索或刷新都会重新读取；切换时旧列表立即清除，不会先显示上一个库的内容。
        </li>
        <li>
          列表是上游索引在读取时刻的快照，不是实时目录状态；继续读取下一页会带上本次范围的续读标记。
        </li>
        <li>
          库里报告的“索引离线”只是一个状态：它不表示文件已经消失，原件是否可用本页无法验证。
        </li>
        <li>
          {page.stamp
            ? `最近一次读取：${new Date(page.stamp.at).toLocaleTimeString("zh-CN", { hour12: false })}（${page.stamp.operation}）。`
            : "本次进入页面后还没有完成一次读取。"}
        </li>
      </ul>
    </section>
  );

  return (
    <div className={browsing ? "scroll-page" : undefined}>
      <section className="asset-head">
        <h2 className="asset-title" ref={heading} tabIndex={-1}>
          资产库
        </h2>
        <p className="asset-readonly" role="note">
          <ShieldCheck aria-hidden="true" />
          只读页面：只显示上游授权返回的索引元数据，不写入、不下载、不预览原件。
        </p>
      </section>

      <section className="asset-connection" aria-label="连接与库">
        <StatusRail tone={rail.tone} label={rail.label}>
          <div className="asset-connection-body">
            <p className="asset-connection-detail">{rail.detail}</p>
            <div className="asset-connection-controls">
              <label className="asset-select">
                <span>资产连接</span>
                <select
                  value={state?.connection ?? ""}
                  disabled={!state || state.connections.length === 0}
                  onChange={(event) =>
                    void page.chooseConnection(event.target.value)
                  }
                >
                  <option value="">
                    {state && state.connections.length > 0
                      ? "请选择"
                      : "没有可用连接"}
                  </option>
                  {(state?.connections ?? []).map((item) => (
                    <option key={item.connection_id} value={item.connection_id}>
                      {item.label}
                      {item.credential_registered ? "" : "（未登记凭据）"}
                    </option>
                  ))}
                </select>
              </label>
              {connection && !connection.credential_registered && (
                <p className="asset-warning" role="status">
                  这个连接还没有登记服务凭据，读取会失败而不是显示空列表。
                </p>
              )}
              <p className="asset-muted">
                {state?.connection
                  ? `当前连接：${connection?.label ?? state.connection}`
                  : "尚未选择连接。"}
              </p>
            </div>
          </div>
        </StatusRail>
      </section>

      {status}

      {!status && (
        <>
          <section className="asset-toolbar" aria-label="浏览工具条">
            <nav className="asset-crumbs" aria-label="当前目录路径">
              <button
                type="button"
                className="asset-crumb"
                data-current={crumbs.length === 0 || undefined}
                aria-current={crumbs.length === 0 ? "location" : undefined}
                onClick={() =>
                  void page.open(
                    page.scope.library,
                    page.scope.query !== null ? page.scope.parent : "",
                  )
                }
              >
                <FolderOpen aria-hidden="true" />
                {page.scope.library?.display_name ?? "库根目录"}
              </button>
              {page.scope.query === null &&
                crumbs.map((part, index) => (
                  <span className="asset-crumb-part" key={`${part}-${index}`}>
                    <ChevronRight aria-hidden="true" />
                    <button
                      type="button"
                      className="asset-crumb"
                      data-current={index === crumbs.length - 1 || undefined}
                      aria-current={
                        index === crumbs.length - 1 ? "location" : undefined
                      }
                      onClick={() =>
                        void page.open(
                          page.scope.library,
                          crumbs.slice(0, index + 1).join("/"),
                        )
                      }
                    >
                      {part}
                    </button>
                  </span>
                ))}
              {page.scope.query !== null && (
                <span className="asset-crumb-part">
                  <ChevronRight aria-hidden="true" />
                  <span className="asset-crumb-query">
                    搜索：{page.scope.query}
                  </span>
                </span>
              )}
            </nav>
            <div className="asset-toolbar-actions">
              {page.scope.query === null && page.scope.parent !== "" && (
                <button
                  type="button"
                  className="button ghost"
                  onClick={() => {
                    const up = crumbs.slice(0, -1).join("/");
                    void page.open(page.scope.library, up);
                  }}
                >
                  <CornerLeftUp aria-hidden="true" />
                  返回上级
                </button>
              )}
              {page.scope.library && (
                <button
                  type="button"
                  className="button ghost"
                  onClick={() => void page.open(null, "")}
                >
                  <Folder aria-hidden="true" />
                  库列表
                </button>
              )}
              <button
                type="button"
                className="button"
                disabled={busy}
                onClick={() => page.refresh()}
              >
                <RefreshCw aria-hidden="true" />
                刷新
              </button>
            </div>
          </section>

          {page.scope.library && (
            <form className="asset-search" onSubmit={submit} role="search">
              <label className="asset-search-field">
                <span>在当前范围搜索名称</span>
                <input
                  ref={searchBox}
                  type="search"
                  value={query}
                  maxLength={200}
                  placeholder="输入名称片段后提交"
                  onChange={(event) => setQuery(event.target.value)}
                />
              </label>
              <label className="asset-select">
                <span>搜索范围</span>
                <select
                  value={searchScope}
                  onChange={(event) =>
                    setSearchScope(event.target.value as "library" | "all")
                  }
                >
                  <option value="library">当前库</option>
                  <option value="all">连接内全部授权库</option>
                </select>
              </label>
              <button type="submit" className="button">
                <Search aria-hidden="true" />
                搜索
              </button>
              {page.scope.query !== null && (
                <button
                  type="button"
                  className="button ghost"
                  onClick={() => {
                    setQuery("");
                    page.clearSearch();
                  }}
                >
                  <X aria-hidden="true" />
                  清除搜索
                </button>
              )}
            </form>
          )}

          {page.failure && (
            <StatePanel kind="error" title="这次读取没有成功">
              <p>{page.failure.message}</p>
              <p className="asset-muted">
                页面没有旧结果可以退回：失败的读取不会留下正文。
              </p>
              <p>
                <button
                  type="button"
                  className="button"
                  onClick={() => page.refresh()}
                >
                  <RefreshCw aria-hidden="true" />
                  重新读取
                </button>
              </p>
            </StatePanel>
          )}

          {!page.failure && busy && (
            <StatePanel kind="loading" title="正在读取">
              <p>正在向上游请求这一级的内容，请稍候。</p>
            </StatePanel>
          )}

          {!page.failure && !busy && !page.scope.library && page.libraries && (
            <section className="asset-libraries" aria-label="授权库列表">
              <h3 className="asset-section-title">授权库</h3>
              {page.libraries.length === 0 ? (
                <StatePanel kind="empty" title="这个连接没有授权库">
                  <p>
                    上游授权返回了空列表：这不是读取失败，而是当前凭据看不到任何库。
                  </p>
                </StatePanel>
              ) : (
                <ul className="asset-library-list">
                  {page.libraries.map((library) => {
                    const item = libraryState(library);
                    return (
                      <li key={library.library_id}>
                        <button
                          type="button"
                          className="asset-library-row"
                          onClick={() => selectLibrary(library)}
                        >
                          <span className={`asset-tone tone-${item.tone}`}>
                            {item.label}
                          </span>
                          <span className="asset-library-row-name">
                            {library.display_name}
                          </span>
                          <span className="asset-row-kind">
                            {library.category}
                          </span>
                          <span className="asset-row-hint" aria-hidden="true">
                            打开
                            <ChevronRight />
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
              {moreLibraries && (
                <div className="asset-more">
                  <button
                    type="button"
                    className="button"
                    disabled={busy}
                    onClick={() => void page.more(moreLibraries)}
                  >
                    继续读取下一页
                  </button>
                  <p className="asset-muted">
                    已显示 {page.libraries?.length ?? 0}{" "}
                    个授权库，上游还有更多。
                  </p>
                </div>
              )}
              {!moreLibraries && (page.libraries?.length ?? 0) > 0 && (
                <p className="asset-muted asset-complete">
                  这一级已完整显示 {page.libraries?.length ?? 0} 个授权库。
                </p>
              )}
            </section>
          )}

          {!page.failure && !busy && page.scope.library && (
            <div className="asset-body scroll-layout">
              <section
                className="asset-main scroll-pane"
                aria-label="文件列表"
                tabIndex={0}
              >
                {page.scope.query === null && (
                  <LibrarySummary
                    library={page.listing?.library ?? page.scope.library}
                  />
                )}
                {page.scope.query !== null && page.results && (
                  <StatusRail
                    tone={page.results.hits.length > 0 ? "blue" : "gray"}
                    label={
                      page.results.hits.length > 0 ? "搜索有结果" : "搜索无结果"
                    }
                  >
                    <p className="asset-muted">
                      {page.results.hits.length > 0
                        ? `在${page.scope.searchScope === "all" ? "连接内全部授权库" : "当前库"}中找到 ${page.results.hits.length} 个名称匹配的条目（命中依据：名称）。`
                        : `在${page.scope.searchScope === "all" ? "连接内全部授权库" : "当前库"}中没有名称匹配的条目。没有结果不等于没有文件。`}
                    </p>
                  </StatusRail>
                )}

                {page.scope.query !== null ? (
                  hits.length === 0 ? (
                    <StatePanel kind="empty" title="没有匹配的条目">
                      <p>
                        搜索按名称包含匹配，范围是你选择的
                        {page.scope.searchScope === "all"
                          ? "连接内全部授权库"
                          : "当前库"}
                        。可以换一个关键词，或清除搜索回到当前目录。
                      </p>
                    </StatePanel>
                  ) : (
                    <ul className="asset-list">
                      {hits.map((hit) => (
                        <li
                          key={`${hit.library.library_id}:${hit.entry.entry_id}`}
                        >
                          <button
                            type="button"
                            className="asset-row"
                            data-selected={
                              selected === hit.entry.entry_id || undefined
                            }
                            onClick={() => selectEntry(hit.entry)}
                          >
                            <span className="asset-row-name">
                              <span className="asset-row-label">
                                {hit.entry.name}
                              </span>
                              <span className="asset-row-path">
                                {hit.library.display_name} ·{" "}
                                {hit.entry.relative_path}
                              </span>
                            </span>
                            <span className="asset-row-kind">
                              {hit.hit_reason === "path"
                                ? "路径命中"
                                : "名称命中"}
                            </span>
                            <span className="asset-row-time">
                              {hit.library.availability === "offline"
                                ? "索引离线"
                                : "索引在线"}
                            </span>
                            <span className="asset-row-hint" aria-hidden="true">
                              查看详情
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )
                ) : entries.length === 0 ? (
                  <StatePanel kind="empty" title="这个目录里没有条目">
                    <p>
                      上游授权返回了这个目录的空列表：它是空目录，不是读取失败，也不代表上级目录为空。
                    </p>
                  </StatePanel>
                ) : (
                  <EntryList
                    entries={entries}
                    selected={selected}
                    busy={busy}
                    onOpen={openDirectory}
                    onSelect={selectEntry}
                  />
                )}

                {more && (
                  <div className="asset-more">
                    <button
                      type="button"
                      className="button"
                      disabled={busy}
                      onClick={() => void page.more(more)}
                    >
                      继续读取下一页
                    </button>
                    <p className="asset-muted">
                      已显示{" "}
                      {page.scope.query !== null
                        ? page.results?.hits.length
                        : page.listing?.entries.length}{" "}
                      条，上游还有更多。
                    </p>
                  </div>
                )}
                {!more && listing && listing.page.returned > 0 && (
                  <p className="asset-muted asset-complete">
                    这一级已完整显示 {listing.page.returned} 条。
                  </p>
                )}
                {freshness}
              </section>

              <aside
                key={selected}
                className="asset-side scroll-pane"
                aria-label="条目详情"
                tabIndex={0}
              >
                <DetailPanel
                  detail={page.detail}
                  preview={preview}
                  onClose={() => {
                    setSelected("");
                    page.closeDetail();
                  }}
                />
              </aside>
            </div>
          )}
        </>
      )}

      {!browsing && freshness}
    </div>
  );
}
