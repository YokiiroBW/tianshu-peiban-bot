/** The shared router keeps its existing numeric hash; this query is local to media. */
export function attentionJobsHref() {
  const url = new URL(window.location.href);
  url.searchParams.set("media-filter", "attention");
  url.hash = "#/media/jobs";
  return `${url.pathname}${url.search}${url.hash}`;
}

export function initialJobsFilter() {
  return new URLSearchParams(window.location.search).get("media-filter") ===
    "attention"
    ? "attention"
    : "all";
}

export function consumeJobsFilter() {
  const url = new URL(window.location.href);
  if (!url.searchParams.has("media-filter")) return;
  url.searchParams.delete("media-filter");
  window.history.replaceState(
    window.history.state,
    "",
    `${url.pathname}${url.search}${url.hash}`,
  );
}
