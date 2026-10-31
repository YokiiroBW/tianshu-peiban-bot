import { useCallback, useEffect, useRef, useState } from "react";
import {
  call,
  isState,
  session as readSession,
  WebError,
  type BrowsePage,
  type Entry,
  type EntryDetail,
  type LibrariesPage,
  type Library,
  type Page,
  type PageState,
  type SearchPage,
} from "./api";

/** Where the page is looking, and what it asked for. */
export type Scope = {
  /** The library whose directory is listed, or none while only the library list is shown. */
  library: Library | null;
  /** The relative directory inside that library. `""` is its root. */
  parent: string;
  /** The submitted search, or `null` when no search is active. */
  query: string | null;
  /** Whether a search covers one library or everything the connection authorizes. */
  searchScope: "library" | "all";
};

const initial: Scope = {
  library: null,
  parent: "",
  query: null,
  searchScope: "library",
};

export type ResourcePage = ReturnType<typeof useAssetPage>;

/** One read the page performed, for its own freshness statement. */
type Stamp = { operation: string; code: string; at: number };

/**
 * The page's own request lifecycle.
 *
 * Everything that can change what is on screen is a *generation*: choosing a connection, opening
 * another library, entering a directory, submitting a search, refreshing or paging. A generation
 * owns one `AbortController`, so starting a new one aborts the read in flight, and a late answer
 * from an older generation is dropped instead of filling the page. A revoked or expired login
 * clears the body, because a stale listing must never survive it — and while a read is in flight
 * the page shows nothing from the previous one.
 */
export function useAssetPage() {
  const [state, setState] = useState<PageState | null>(null);
  const [libraries, setLibraries] = useState<Library[] | null>(null);
  const [librariesPage, setLibrariesPage] = useState<Page | null>(null);
  const [listing, setListing] = useState<BrowsePage | null>(null);
  const [results, setResults] = useState<SearchPage | null>(null);
  const [detail, setDetail] = useState<EntryDetail | null>(null);
  const [scope, setScope] = useState<Scope>(initial);
  const [authenticated, setAuthenticated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState("");
  const [failure, setFailure] = useState<{
    code: string;
    message: string;
  } | null>(null);
  const [absent, setAbsent] = useState("");
  const [stamp, setStamp] = useState<Stamp | null>(null);
  const generation = useRef(0);
  const active = useRef<AbortController | null>(null);
  const csrf = useRef("");
  /**
   * Which connection the library list on screen belongs to.
   *
   * The library list has its own continuation, and a continuation belongs to one connection's one
   * listing. This records the connection that produced the list so a later page can only ever be
   * appended to *that* list: if the connection changed, or the session lost its scope, the page is
   * replaced instead of being extended with another connection's libraries.
   */
  const librariesConnection = useRef<string | null>(null);

  /** Start a generation: the previous read is aborted, and only this one may write the page. */
  const begin = useCallback(() => {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    generation.current += 1;
    return { controller, mine: generation.current };
  }, []);

  /** The whole body goes away whenever what it described stops being true. */
  const clear = useCallback(() => {
    setLibraries(null);
    setLibrariesPage(null);
    setListing(null);
    setResults(null);
    setDetail(null);
    librariesConnection.current = null;
  }, []);

  const load = useCallback(
    async (signal: AbortSignal, next: Scope, retry = true) => {
      setLoading(true);
      setFailure(null);
      setPending(
        next.query !== null ? "search" : next.library ? "browse" : "libraries",
      );
      try {
        const current = await readSession(signal);
        setAuthenticated(current.authenticated);
        csrf.current = current.csrf;
        if (!current.authenticated) {
          // A revoked or logged-out session is not a stale view: the body goes with it.
          setState(null);
          clear();
          setScope(initial);
          return;
        }
        // The page state is a read of its own: which connections this login may use, and which one
        // this session already chose. It is asked before a connection is chosen, so the page can
        // offer the choice instead of reporting a refusal it caused itself — and it is a *read*:
        // `assets/state` never changes the session's scope, which `assets/connection` does.
        const page = await call<PageState>(
          "assets/state",
          {},
          current.csrf,
          signal,
        );
        setState(page);
        setAbsent(page.available ? "" : page.code);
        if (!page.connection || !page.available) {
          // No connection is chosen (or the page is not configured): no body belongs on screen.
          clear();
          return;
        }
        if (next.query !== null) {
          const found = await call<SearchPage>(
            "assets/search",
            next.searchScope === "library" && next.library
              ? {
                  query: next.query,
                  scope: "library",
                  library_id: next.library.library_id,
                }
              : { query: next.query, scope: "all" },
            current.csrf,
            signal,
          );
          setResults(found);
          setListing(null);
          setStamp({ operation: "assets.search", code: "ok", at: Date.now() });
          return;
        }
        if (next.library) {
          const found = await call<BrowsePage>(
            "assets/browse",
            {
              library_id: next.library.library_id,
              parent_relative_path: next.parent,
            },
            current.csrf,
            signal,
          );
          setListing(found);
          setResults(null);
          setStamp({ operation: "entries.browse", code: "ok", at: Date.now() });
          return;
        }
        const listed = await call<LibrariesPage>(
          "assets/libraries",
          {},
          current.csrf,
          signal,
        );
        setLibraries(listed.libraries);
        setLibrariesPage(listed.page);
        librariesConnection.current = listed.connection;
        setListing(null);
        setResults(null);
        setStamp({ operation: "libraries.list", code: "ok", at: Date.now() });
      } catch (cause) {
        if (signal.aborted) return;
        setListing(null);
        setResults(null);
        setDetail(null);
        if (cause instanceof WebError) {
          if (isState(cause, "session_expired", "unauthorized")) {
            // The login ended while it was being read: re-read once, then state it honestly.
            setAuthenticated(false);
            setState(null);
            clear();
            setScope(initial);
            if (retry) await load(signal, initial, false);
            setFailure({ code: cause.code, message: cause.message });
            return;
          }
          if (isState(cause, "assets_disabled")) {
            setState(null);
            setAbsent(cause.code);
          }
          setFailure({ code: cause.code, message: cause.message });
          setStamp({ operation: "read", code: cause.code, at: Date.now() });
          return;
        }
        setFailure({ code: "network", message: "连接中断，请重新连接。" });
      } finally {
        if (!signal.aborted) setLoading(false);
      }
    },
    [clear],
  );

  /** Start a new generation for one scope; the previous read stops here. */
  const run = useCallback(
    (next: Scope, clearFirst = true) => {
      const { controller } = begin();
      setScope(next);
      if (clearFirst) clear();
      return load(controller.signal, next);
    },
    [begin, clear, load],
  );

  useEffect(() => {
    const { controller } = begin();
    void load(controller.signal, initial);
    return () => {
      // Leaving the page stops its read too: nothing keeps running after the page is gone.
      controller.abort();
    };
    // A read happens on mount; every later read is an explicit generation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /**
   * Choosing a connection is one generation: the previous library's content goes away, the choice
   * is made server-side in this session, and the newly authorized library list is read.
   *
   * The choice's own answer is the page state, so it is kept as-is instead of being re-read: a
   * second state read here could arrive before the session change was visible and would then
   * overwrite the chosen connection with "none", leaving a page that had in fact connected.
   */
  const chooseConnection = useCallback(
    async (connection_id: string) => {
      const { controller, mine } = begin();
      setLoading(true);
      setFailure(null);
      clear();
      setScope(initial);
      try {
        const current = await readSession(controller.signal);
        csrf.current = current.csrf;
        const chosen = await call<PageState>(
          "assets/connection",
          { connection_id },
          current.csrf,
          controller.signal,
        );
        if (generation.current !== mine || controller.signal.aborted) return;
        setState(chosen);
        const listed = await call<LibrariesPage>(
          "assets/libraries",
          {},
          current.csrf,
          controller.signal,
        );
        if (generation.current !== mine || controller.signal.aborted) return;
        setLibraries(listed.libraries);
        setLibrariesPage(listed.page);
        librariesConnection.current = listed.connection;
        setStamp({ operation: "libraries.list", code: "ok", at: Date.now() });
      } catch (cause) {
        if (controller.signal.aborted || generation.current !== mine) return;
        setFailure({
          code: cause instanceof WebError ? cause.code : "network",
          message:
            cause instanceof WebError
              ? cause.message
              : "连接中断，请重新连接。",
        });
      } finally {
        if (!controller.signal.aborted && generation.current === mine)
          setLoading(false);
      }
    },
    [begin, clear],
  );

  /** Opening a library, entering a directory or leaving one clears the previous list. */
  const open = useCallback(
    (library: Library | null, parent: string) => {
      setDetail(null);
      void run({ library, parent, query: null, searchScope: "library" });
    },
    [run],
  );

  const search = useCallback(
    (query: string, searchScope: "library" | "all") => {
      setDetail(null);
      void run({
        ...scope,
        query: query.trim() ? query.trim() : null,
        searchScope,
      });
    },
    [run, scope],
  );

  /** Clearing a search restores the current range, not a global one. */
  const clearSearch = useCallback(() => {
    setDetail(null);
    void run({ ...scope, query: null });
  }, [run, scope]);

  const refresh = useCallback(() => {
    setDetail(null);
    void run(scope);
  }, [run, scope]);

  /**
   * Reading on is a new generation too: an older page must not arrive after this one.
   *
   * There are three listings a continuation can belong to — a search, a directory, and the
   * authorized library list itself. The library list is not a special case that may be skipped:
   * a connection with more libraries than fit in one page would otherwise be unreachable from the
   * only entry point that lists libraries. The connection that produced the list is part of the
   * binding, so a page read for one connection is never appended to another's list.
   */
  const more = useCallback(
    async (cursor: string) => {
      const { controller, mine } = begin();
      setLoading(true);
      setFailure(null);
      setPending("more");
      try {
        if (scope.query !== null) {
          const found = await call<SearchPage>(
            "assets/search",
            scope.searchScope === "library" && scope.library
              ? {
                  query: scope.query,
                  scope: "library",
                  library_id: scope.library.library_id,
                  cursor,
                }
              : { query: scope.query, scope: "all", cursor },
            csrf.current,
            controller.signal,
          );
          if (generation.current !== mine || controller.signal.aborted) return;
          setResults((old) =>
            old ? { ...found, hits: [...old.hits, ...found.hits] } : found,
          );
          return;
        }
        if (scope.library) {
          const found = await call<BrowsePage>(
            "assets/browse",
            {
              library_id: scope.library.library_id,
              parent_relative_path: scope.parent,
              cursor,
            },
            csrf.current,
            controller.signal,
          );
          if (generation.current !== mine || controller.signal.aborted) return;
          setListing((old) =>
            old
              ? { ...found, entries: [...old.entries, ...found.entries] }
              : found,
          );
          return;
        }
        const found = await call<LibrariesPage>(
          "assets/libraries",
          { cursor },
          csrf.current,
          controller.signal,
        );
        if (generation.current !== mine || controller.signal.aborted) return;
        setLibraries((old) =>
          old && librariesConnection.current === found.connection
            ? [...old, ...found.libraries]
            : found.libraries,
        );
        setLibrariesPage(found.page);
        librariesConnection.current = found.connection;
        setStamp({ operation: "libraries.list", code: "ok", at: Date.now() });
      } catch (cause) {
        if (controller.signal.aborted || generation.current !== mine) return;
        setFailure({
          code: cause instanceof WebError ? cause.code : "network",
          message:
            cause instanceof WebError
              ? cause.message
              : "连接中断，请重新连接。",
        });
      } finally {
        if (!controller.signal.aborted && generation.current === mine)
          setLoading(false);
      }
    },
    [begin, scope],
  );

  /** A detail is a current re-read of one entry, never a copy of the listing row. */
  const inspect = useCallback(
    async (entry: Entry) => {
      const { controller, mine } = begin();
      setPending("detail");
      setFailure(null);
      try {
        const found = await call<EntryDetail>(
          "assets/entry",
          { library_id: entry.library_id, entry_id: entry.entry_id },
          csrf.current,
          controller.signal,
        );
        if (generation.current !== mine || controller.signal.aborted) return;
        setDetail(found);
      } catch (cause) {
        if (controller.signal.aborted || generation.current !== mine) return;
        setDetail(null);
        setFailure({
          code: cause instanceof WebError ? cause.code : "network",
          message:
            cause instanceof WebError
              ? cause.message
              : "连接中断，请重新连接。",
        });
      } finally {
        if (generation.current === mine) setPending("");
      }
    },
    [begin],
  );

  return {
    state,
    libraries,
    librariesPage,
    listing,
    results,
    detail,
    scope,
    authenticated,
    loading,
    pending,
    failure,
    absent,
    stamp,
    chooseConnection,
    open,
    search,
    clearSearch,
    refresh,
    more,
    inspect,
    closeDetail: () => setDetail(null),
    dismissFailure: () => setFailure(null),
  };
}
