import { uploadMedia } from "../../../api/uploads";
import { useAuth } from "../../../app/Auth";
import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, Download, RefreshCw } from "lucide-react";
import type { MediaController } from "../useMediaController";
import { StatePanel } from "../../../components/StatePanel";
import { BookSearch } from "./BookSearch";
import { BookDetail } from "./BookDetail";
import { BookSubscriptions } from "./BookSubscriptions";
import { BookSources } from "./BookSources";
import { BookWarehouse } from "./BookWarehouse";
import { BookSettings } from "./BookSettings";
import { BookLibrary } from "./BookLibrary";
import { BookAudioSettings } from "./BookAudioSettings";
import type { ReadingView } from "./audioTypes";
import type {
  Book,
  BooksView,
  BookSearchResult,
  BookDetail as Detail,
  BookChapter,
  ChapterPreview,
  BookMetadata,
  BookSource,
  BookFeed,
} from "./types";
import type { SourceImportResult } from "./BookSources";
import "./books.css";

const tabs = [
  ["search", "聚合搜索"],
  ["subscriptions", "我的订阅"],
  ["library", "本地书库"],
  ["sources", "书源管理"],
  ["settings", "整理设置"],
  ["audio", "听书设置"],
] as const;
const slots = [
  "books-view",
  "books-search-start",
  "books-search-poll",
  "books-search-stop",
  "books-detail",
  "books-preview",
  "books-toc",
  "books-metadata",
  "books-library",
  "books-sources",
  "books-feed",
  "books-export",
  "books-scan",
  "books-bookmark",
];

export function BooksPanel({ media }: { media: MediaController }) {
  const { session } = useAuth();
  const current = useRef(media);
  current.current = media;
  const mutations = useRef(0);
  const [mutating, setMutating] = useState(false);
  const [view, setView] = useState<BooksView | null>(null);
  const [tab, setTab] = useState("search");
  const [warehouseOpen, setWarehouseOpen] = useState(false);
  const [search, setSearch] = useState<BookSearchResult | null>(null);
  const searchRef = useRef(search);
  searchRef.current = search;
  const carry = useRef<Book[]>([]);
  const [selected, setSelected] = useState<Book | null>(null);
  const selectedId = useRef<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [preview, setPreview] = useState<ChapterPreview | null>(null);
  const [library, setLibrary] = useState<{ books: Book[]; total: number }>({
    books: [],
    total: 0,
  });
  const libraryQuery = useRef("");
  const pendingDownload = useRef<{ book: Book; operation: string } | null>(
    null,
  );

  const refresh = useCallback(async () => {
    const result = await current.current.request<BooksView>(
      "books-view",
      "books/view",
      {},
    );
    if (result) setView(result);
    return result;
  }, []);
  const readLibrary = useCallback(
    async (query = libraryQuery.current, offset = 0) => {
      const changed = query !== libraryQuery.current;
      libraryQuery.current = query;
      const result = await current.current.request<{
        books: Book[];
        total: number;
      }>("books-library", "books/library/view", { query, offset });
      if (result)
        setLibrary((previous) => ({
          ...result,
          books: changed
            ? result.books
            : offset
              ? [
                  ...new Map(
                    [...previous.books, ...result.books].map((book) => [
                      book.book_id,
                      book,
                    ]),
                  ).values(),
                ]
              : [
                  ...result.books,
                  ...previous.books.filter(
                    (book) =>
                      !result.books.some(
                        (current) => current.book_id === book.book_id,
                      ),
                  ),
                ],
        }));
    },
    [],
  );

  useEffect(() => {
    if (!media.loading) void refresh();
  }, [media.loading, refresh]);
  const pendingFeeds = view?.feeds.some(
    (feed) => feed.state === "active" && feed.code === "book_feed_pending",
  );
  useEffect(() => {
    if (!pendingFeeds) return;
    const timer = setInterval(() => {
      if (!document.hidden && !mutations.current) void refresh();
    }, 2500);
    return () => clearInterval(timer);
  }, [pendingFeeds, refresh]);
  useEffect(() => {
    const timer = setInterval(() => {
      if (!document.hidden && !mutations.current) void refresh();
    }, 30000);
    const visible = () => {
      if (!document.hidden && !mutations.current) void refresh();
    };
    document.addEventListener("visibilitychange", visible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
      for (const slot of slots) current.current.cancel(slot);
    };
  }, [refresh]);
  useEffect(() => {
    if (tab !== "library") return;
    void readLibrary();
    const timer = setInterval(() => {
      if (!document.hidden) void readLibrary();
    }, 15000);
    return () => clearInterval(timer);
  }, [tab, readLibrary]);
  useEffect(() => {
    if (!search || search.state !== "running") return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const identity = search.search_id;
    const poll = async () => {
      const result = await current.current.request<BookSearchResult>(
        "books-search-poll",
        "books/search/results",
        { search_id: identity },
      );
      if (stopped) return;
      if (result)
        setSearch({ ...result, books: [...carry.current, ...result.books] });
      if (!result || result.state === "running")
        timer = setTimeout(poll, result ? 750 : 3000);
    };
    timer = setTimeout(poll, 200);
    return () => {
      stopped = true;
      clearTimeout(timer);
      current.current.cancel("books-search-poll");
    };
  }, [search?.search_id, search?.state]);

  async function mutate<T>(operation: string, body: object, notice?: string) {
    mutations.current += 1;
    setMutating(true);
    current.current.cancel("books-view");
    try {
      const result = await current.current.command<T>(
        "books/" + operation,
        body,
      );
      if (result) {
        if (notice) current.current.setNotice(notice);
        await refresh();
      }
      return result;
    } finally {
      mutations.current -= 1;
      if (!mutations.current) setMutating(false);
    }
  }
  async function stopSearch() {
    const result = searchRef.current;
    if (!result || result.state !== "running") return;
    current.current.cancel("books-search-poll");
    setSearch({ ...result, state: "cancelled" });
    await current.current.request("books-search-stop", "books/search/cancel", {
      search_id: result.search_id,
    });
  }
  async function startSearch(query: string, group: string, offset = 0) {
    carry.current =
      offset && searchRef.current?.query === query
        ? searchRef.current.books
        : [];
    await stopSearch();
    current.current.clearError();
    const result = await current.current.request<BookSearchResult>(
      "books-search-start",
      "books/search/start",
      { query, group, offset },
    );
    if (result)
      setSearch({ ...result, books: [...carry.current, ...result.books] });
  }
  async function readChapter(book: Book, chapter: BookChapter) {
    current.current.clearError("books-preview");
    setPreview(null);
    const result = await current.current.request<ChapterPreview>(
      "books-preview",
      "books/preview",
      { book_id: book.book_id, chapter_id: chapter.chapter_id },
    );
    if (result && selectedId.current === book.book_id) {
      setPreview(result);
      const updated = { ...book, readability: result.readability };
      setSelected(updated);
      setDetail((previous) =>
        previous
          ? {
              ...previous,
              book: { ...previous.book, readability: result.readability },
            }
          : previous,
      );
      setSearch((previous) =>
        previous
          ? {
              ...previous,
              books: previous.books.map((item) =>
                item.book_id === book.book_id
                  ? { ...item, readability: result.readability }
                  : item,
              ),
            }
          : previous,
      );
    }
  }
  async function openBook(book: Book) {
    current.current.clearError();
    selectedId.current = book.book_id;
    setSelected(book);
    setDetail(null);
    setPreview(null);
    current.current.cancel("books-preview");
    current.current.cancel("books-metadata");
    current.current.cancel("books-toc");
    const result = await current.current.request<Detail>(
      "books-detail",
      "books/detail",
      { book_id: book.book_id },
    );
    if (result && selectedId.current === book.book_id) {
      setDetail(result);
      setSelected(result.book);
      const bookmark = await current.current.request<ReadingView>(
        "books-bookmark",
        "books/reading/view",
        { book_id: book.book_id },
      );
      if (selectedId.current !== book.book_id) return;
      const chapter = bookmark?.progress
        ? {
            chapter_id: bookmark.progress.chapter_id,
            title: "继续阅读",
            order: 0,
          }
        : result.chapters[0];
      if (chapter) void readChapter(result.book, chapter);
    }
  }
  function closeBook() {
    selectedId.current = null;
    setSelected(null);
    setDetail(null);
    setPreview(null);
    for (const slot of [
      "books-detail",
      "books-preview",
      "books-metadata",
      "books-toc",
    ])
      current.current.cancel(slot);
  }
  async function moreChapters() {
    if (!detail) return;
    const result = await current.current.request<Detail>(
      "books-toc",
      "books/chapters",
      { book_id: detail.book.book_id, offset: detail.chapters.length },
    );
    if (result && selectedId.current === result.book.book_id)
      setDetail((previous) =>
        previous
          ? { ...result, chapters: [...previous.chapters, ...result.chapters] }
          : result,
      );
  }
  async function download(book: Book, operation: string) {
    if (
      operation === "subscriptions/save" &&
      view?.subscriptions.some(
        (subscription) => subscription.book_id === book.book_id,
      )
    ) {
      closeBook();
      setTab("subscriptions");
      return;
    }
    if (!view?.settings.directory && !book.artifact) {
      pendingDownload.current = { book, operation };
      closeBook();
      setTab("settings");
      current.current.setNotice(
        "先填写书籍存档目录。保存后会继续刚才的下载操作。",
      );
      return;
    }
    const result = await mutate(
      operation,
      { book_id: book.book_id },
      operation === "subscriptions/save"
        ? "书籍已订阅，首次下载已进入下载中心。"
        : "整理任务已进入下载中心。",
    );
    if (result) {
      closeBook();
      setTab(operation === "subscriptions/save" ? "subscriptions" : "library");
    }
  }
  async function saveSettings(body: object) {
    const result = await mutate("settings/save", body, "书籍整理设置已保存。");
    const pending = pendingDownload.current;
    if (result && pending) {
      pendingDownload.current = null;
      const job = await mutate(
        pending.operation,
        { book_id: pending.book.book_id },
        "设置已保存，刚才选择的书籍已进入下载中心。",
      );
      if (job)
        setTab(
          pending.operation === "subscriptions/save"
            ? "subscriptions"
            : "library",
        );
    }
  }
  async function applyMetadata(fields: Partial<BookMetadata>, source: string) {
    const book = detail?.book ?? selected;
    if (!book) return false;
    const result = await mutate<{ book: Book }>(
      "metadata/apply",
      {
        book_id: book.book_id,
        fields,
        source,
        expected_revision: book.metadata_revision,
      },
      "书籍资料已保存；已有 EPUB 会在后台更新。",
    );
    if (!result) return false;
    if (selectedId.current === book.book_id) {
      setSelected(result.book);
      setDetail((previous) =>
        previous ? { ...previous, book: result.book } : previous,
      );
    }
    setSearch((previous) =>
      previous
        ? {
            ...previous,
            books: previous.books.map((item) =>
              item.book_id === result.book.book_id ? result.book : item,
            ),
          }
        : previous,
    );
    return true;
  }
  async function importLocal(file: File) {
    if (file.size > 32 * 1024 * 1024) {
      current.current.setNotice(
        "单个导入文件暂支持 32 MiB 以内；文件尚未上传。",
      );
      return;
    }
    if (!session?.csrf) return;
    const active = new AbortController();
    const uploaded = await uploadMedia(file, {}, session.csrf, active.signal);
    const result = await mutate(
      "library/import",
      { filename: file.name, media: uploaded.artifact.media },
      view?.settings.directory
        ? "原文已导入，EPUB 整理已进入下载中心。"
        : "原文已导入；设置存档目录后可整理为 EPUB。",
    );
    if (result) {
      setTab("library");
      await readLibrary();
    }
  }
  async function toggleSource(source: BookSource) {
    const result = await mutate<{ source: BookSource }>("sources/control", {
      source_id: source.source_id,
      expected_revision: source.revision,
      enabled: !source.enabled,
    });
    return result?.source;
  }
  async function feedAction(feed: BookFeed, action: string, interval?: number) {
    if (action === "refresh") {
      const result = await current.current.request(
        "books-feed",
        "books/feeds/refresh",
        { feed_id: feed.feed_id },
      );
      if (result) {
        current.current.setNotice("规则集合已更新，原有本地启停选择保持。");
        await refresh();
      }
    } else
      await mutate("feeds/control", {
        feed_id: feed.feed_id,
        expected_revision: feed.revision,
        action,
        ...(interval ? { interval_seconds: interval } : {}),
      });
  }
  async function exportSource(source: BookSource) {
    const result = await current.current.request<{ sources: unknown[] }>(
      "books-export",
      "books/sources/export",
      { source_id: source.source_id },
    );
    if (!result) return;
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(result.sources, null, 2)], {
        type: "application/json",
      }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = "book-sources.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  if (!view)
    return (
      <StatePanel
        kind={media.error ? "error" : "loading"}
        title={media.error ? "暂时无法读取书籍订阅" : "正在读取书籍订阅"}
      >
        <p>{media.error || "正在读取书源、书库与追更状态。"}</p>
        <button className="button" onClick={() => void refresh()}>
          重新读取
        </button>
      </StatePanel>
    );
  const alternatives = selected
    ? (search?.books ?? []).filter(
        (book) => book.group_key === selected.group_key,
      )
    : [];
  return (
    <div className="media-page books-page">
      <div className="book-page-heading">
        <div>
          <h2>
            <BookOpen aria-hidden="true" />
            书籍订阅
          </h2>
          <p className="muted">从找到一本书，到持续追更、整理入库。</p>
        </div>
        <div className="book-controls">
          <a className="button" href="#/media/jobs">
            <Download aria-hidden="true" />
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
      <nav className="book-tabs" aria-label="书籍订阅功能">
        {tabs.map(([key, label]) => (
          <button
            key={key}
            aria-current={tab === key ? "page" : undefined}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </nav>
      {tab === "search" && (
        <BookSearch
          view={view}
          result={search}
          onSearch={(query, group, offset) =>
            void startSearch(query, group, offset)
          }
          onCancel={() => void stopSearch()}
          onPreview={(book) => void openBook(book)}
          onSubscribe={(book) => void download(book, "subscriptions/save")}
          onTab={setTab}
        />
      )}
      {tab === "subscriptions" && (
        <BookSubscriptions
          subscriptions={view.subscriptions}
          busy={!!media.busy || mutating}
          onControl={(subscription, action, interval) => {
            void mutate(
              "subscriptions/control",
              {
                book_id: subscription.book_id,
                expected_revision: subscription.revision,
                action,
                ...(interval ? { interval_seconds: interval } : {}),
              },
              action === "remove"
                ? "订阅已删除，已下载文件、阅读进度和当前任务保留。"
                : "追更设置已更新。",
            );
          }}
          onScan={(subscription) => {
            void current.current
              .request("books-scan", "books/subscriptions/scan", {
                book_id: subscription.book_id,
              })
              .then((result) => {
                if (result) {
                  current.current.setNotice(
                    "已检查目录，新增或缺失章节已进入下载中心。",
                  );
                  void refresh();
                }
              });
          }}
          onPreview={(book) => void openBook(book)}
        />
      )}
      {tab === "library" && (
        <BookLibrary
          books={library.books}
          total={library.total}
          busy={!!media.busy || mutating}
          onPreview={(book) => void openBook(book)}
          onImport={importLocal}
          onRefresh={(query) => void readLibrary(query)}
          onMore={() =>
            void readLibrary(libraryQuery.current, library.books.length)
          }
          onRebuild={(book) => void download(book, "library/rebuild")}
        />
      )}
      {tab === "sources" && (
        <BookSources
          view={view}
          busy={!!media.busy || mutating}
          onWarehouse={() => setWarehouseOpen(true)}
          onImport={(sources) =>
            mutate<SourceImportResult>("sources/import", { sources })
          }
          onFeed={async (url) =>
            !!(await mutate(
              "feeds/save",
              { url },
              "规则订阅已保存，后台将检查实际内容。",
            ))
          }
          onFeedAction={(feed, action, interval) =>
            void feedAction(feed, action, interval)
          }
          onToggle={toggleSource}
          onExport={(source) => void exportSource(source)}
          onLoad={(offset, query) =>
            current.current.request("books-sources", "books/sources/list", {
              offset,
              query,
            })
          }
        />
      )}
      {tab === "settings" && (
        <BookSettings
          key={view.settings.revision}
          settings={view.settings}
          busy={!!media.busy || mutating}
          onSave={(body) => void saveSettings(body)}
        />
      )}
      {warehouseOpen && (
        <BookWarehouse
          media={media}
          feeds={view.feeds}
          onClose={() => setWarehouseOpen(false)}
          onSubscribe={async (entries, interval) => {
            const result = await mutate<{ feeds: BookFeed[] }>(
              "warehouse/subscribe",
              {
                entries: entries.map(({ kind, entry_id, title }) => ({
                  kind,
                  entry_id,
                  title,
                })),
                interval_seconds: interval,
              },
            );
            return result?.feeds;
          }}
        />
      )}
      {tab === "audio" && <BookAudioSettings media={media} />}
      {selected && (
        <BookDetail
          media={media}
          key={selected.book_id}
          book={selected}
          detail={detail}
          preview={preview}
          alternatives={alternatives}
          busy={!!media.busy || mutating}
          onClose={closeBook}
          error={media.error}
          onOpen={(book) => void openBook(book)}
          onChapter={(chapter) => void readChapter(selected, chapter)}
          onMore={() => void moreChapters()}
          onDownload={() => void download(selected, "downloads/create")}
          onSubscribe={() => void download(selected, "subscriptions/save")}
          onMetadataSearch={(query) =>
            current.current.request("books-metadata", "books/metadata/search", {
              book_id: selected.book_id,
              query,
            })
          }
          onMetadataApply={applyMetadata}
        />
      )}
    </div>
  );
}
