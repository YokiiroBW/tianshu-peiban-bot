import { useCallback, useEffect, useRef, useState } from "react";
import type { Job } from "./types";
import type { MediaController } from "./useMediaController";

const FILTERS: Record<string, string[] | null> = {
  all: null,
  running: [
    "queued",
    "downloading",
    "validating",
    "metadata_ready",
    "publishing",
    "asset_indexed",
    "library_verifying",
    "auth_required",
    "waiting_metadata",
    "retry_wait",
    "unknown",
  ],
  attention: ["failed", "unknown", "waiting_metadata", "auth_required"],
  finished: ["completed", "published"],
};
type JobsPage = {
  jobs: Job[];
  page: { next_cursor: string | null; has_more: boolean };
};

/** A cursor belongs to its exact filter. Periodic head reads do not replace its continuation. */
export function useJobs(media: MediaController, filter: string) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [failed, setFailed] = useState(false);
  const cursor = useRef<string | null>(null);
  const epoch = useRef(0);
  const alive = useRef(false);
  const inFlight = useRef(false);
  const initial = useRef(false);
  const scope = useRef(filter);
  scope.current = filter;
  const read = useCallback(
    async (more = false) => {
      if (
        inFlight.current ||
        !alive.current ||
        document.hidden ||
        (more && !cursor.current)
      )
        return;
      const mine = epoch.current;
      inFlight.current = true;
      if (more) setLoadingMore(true);
      else if (!initial.current) setLoading(true);
      const result = await media.request<JobsPage>("jobs-list", "jobs/list", {
        states: FILTERS[filter] ?? null,
        page_size: 20,
        cursor: more ? cursor.current : null,
      });
      if (!alive.current || mine !== epoch.current || scope.current !== filter)
        return;
      if (result) {
        setJobs((current) => {
          if (!initial.current) return result.jobs;
          const incoming = new Map(result.jobs.map((job) => [job.job_id, job]));
          const existing = current.map(
            (job) => incoming.get(job.job_id) ?? job,
          );
          const known = new Set(current.map((job) => job.job_id));
          const additions = result.jobs.filter((job) => !known.has(job.job_id));
          return more
            ? [...existing, ...additions]
            : [...additions, ...existing];
        });
        if (
          more ||
          !initial.current ||
          (!cursor.current && result.page.has_more)
        ) {
          cursor.current = result.page.next_cursor;
          setHasMore(result.page.has_more);
        }
        initial.current = true;
        setFailed(false);
      } else setFailed(true);
      inFlight.current = false;
      setLoading(false);
      setLoadingMore(false);
    },
    [filter, media.request],
  );

  useEffect(() => {
    epoch.current += 1;
    alive.current = true;
    initial.current = false;
    inFlight.current = false;
    cursor.current = null;
    setJobs([]);
    setHasMore(false);
    setFailed(false);
    setLoading(true);
    void read();
    const hidden = () => {
      if (document.hidden) {
        media.cancel("jobs-list");
        epoch.current += 1;
        inFlight.current = false;
        setLoadingMore(false);
      } else void read();
    };
    document.addEventListener("visibilitychange", hidden);
    return () => {
      alive.current = false;
      epoch.current += 1;
      media.cancel("jobs-list");
      document.removeEventListener("visibilitychange", hidden);
    };
  }, [read, media.cancel]);
  useEffect(() => {
    const facts = new Map(
      (media.view?.jobs ?? []).map((job) => [job.job_id, job]),
    );
    setJobs((current) =>
      current
        .map((job) => facts.get(job.job_id) ?? job)
        .filter(
          (job) => !FILTERS[filter] || FILTERS[filter]!.includes(job.state),
        ),
    );
    if (media.view) void read();
  }, [media.view, read, filter]);
  const clear = () => {
    epoch.current += 1;
    media.cancel("jobs-list");
    inFlight.current = false;
    initial.current = false;
    cursor.current = null;
    setJobs([]);
    setHasMore(false);
    setLoading(true);
    setFailed(false);
  };
  return {
    jobs,
    loading,
    loadingMore,
    hasMore,
    failed,
    more: () => read(true),
    refresh: () => read(),
    clear,
    reset: () => {
      clear();
      void read();
    },
  };
}
