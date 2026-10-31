export type Quality = {
  mode: "best" | "exact";
  quality_id: string | null;
  allow_fallback: boolean;
  stream_options?: StreamOptions;
};
export type StreamOptions = {
  video_min_quality: string | null;
  video_max_quality: string | null;
  audio_min_quality: string | null;
  audio_max_quality: string | null;
  codecs: ("av1" | "hevc" | "avc")[];
  allow_hdr: boolean;
  allow_dolby_vision: boolean;
  allow_dolby_audio: boolean;
  allow_hires: boolean;
};
export type Rule = {
  id: string;
  field:
    | "title"
    | "description"
    | "uploader_id"
    | "uploader_name"
    | "tags"
    | "published_at"
    | "favorited_at"
    | "part_count"
    | "duration_seconds"
    | "is_collaboration";
  op:
    | "equals"
    | "not_equals"
    | "contains"
    | "prefix"
    | "suffix"
    | "regex"
    | "gt"
    | "gte"
    | "lt"
    | "lte"
    | "between";
  value: string | number | boolean | [string | number, string | number];
  case_sensitive: boolean;
  negate?: boolean;
};
export type Group = { id: string; rules: Rule[] };
export type Rules = {
  schema_version: 1;
  revision: number;
  whitelist: Group[];
  blacklist: Group[];
};
export type Source = {
  kind: "favorite" | "collection" | "series" | "uploader" | "watch_later";
  id: string;
};
export type DestinationMode = "source" | "custom";
export type DestinationPreview = {
  destination_mode: DestinationMode;
  destination_subdirectory: string;
  destination_directory: string | null;
};
export type Account = {
  account_id: string;
  label: string;
  state: "ready" | "auth_required" | "revoked";
  revision: number;
  checked_at: number | null;
  code: string;
};
export type Target = {
  kind?: "media_server" | "assetlibrary";
  revision?: number | null;
  directory?: string | null;
  destination_subdirectory_supported?: boolean;
  target_id: string;
  label: string;
  state: string;
  servers: {
    server_id: string;
    label: string;
    kind: string;
    base_url?: string;
    library_id?: string;
    media_root?: string;
  }[];
};
export type Subscription = {
  subscription_id: string;
  label: string;
  revision: number;
  source: Source;
  account_id: string | null;
  target_id: string;
  initial_sync: "future_only" | "history";
  destination_subdirectory?: string;
  destination_mode?: DestinationMode;
  destination_directory?: string | null;
  quality: Quality;
  rules: Rules;
  state: "active" | "paused" | "auth_required" | "rule_error";
  baseline_ready: boolean;
  last_scan_at: number | null;
  next_scan_at: number;
  interval_seconds: number;
  code: string;
  counts: { members: number; queued: number };
};
export type Job = {
  media_kind?: "audio" | "video" | "book" | "comic" | null;
  comic_id?: string;
  comic_chapter_id?: string;
  comic_library_kind?: "general" | "private";
  comic_pages_done?: number;
  comic_pages_total?: number;
  comic_relative_path?: string;
  book_id?: string;
  book_chapters_done?: number;
  book_chapters_total?: number;
  book_artifact?: import("./books/types").BookArtifact | null;
  book_reused?: boolean;
  provider?: string;
  source_id?: string;
  audio_quality?: import("./music/types").AudioQuality | null;
  audio_unlock?: import("./music/types").AudioUnlock | null;
  audio_attempts?: import("./music/types").AudioAttempt[];
  audio_routing?: import("./music/types").AudioRouting | null;
  audio_receipt?: {
    files: { path: string; kind: string }[];
    warnings: string[];
    unlock?: import("./music/types").AudioUnlock | null;
  } | null;
  audio_reused?: boolean;
  job_id: string;
  subscription_id: string | null;
  media_key: string;
  bvid: string | null;
  cid: string | null;
  selected_cids: string[] | null;
  title: string;
  creator: string;
  state: string;
  stage: string;
  code: string;
  progress: number | null;
  created_at: number;
  updated_at: number;
  target_id: string;
  quality:
    | Quality
    | import("./music/types").MusicQuality
    | { mode: "book" | "comic-original" };
  destination_subdirectory?: string;
  destination_directory?: string | null;
  download_reused_cids?: string[];
  actual_quality: string | null;
  layout?: "single" | "multipart";
  cancel_requested: boolean;
  can_retry: boolean;
  can_cancel: boolean;
  can_delete: boolean;
  revision: number;
  asset_receipt: Record<string, unknown> | null;
  library_results: {
    reader_url?: string;
    page_count?: number;
    server_id: string;
    kind: string;
    state: string;
    code: string;
    item_id: string | null;
  }[];
};
export type StorageBreakdown = {
  state: "ready" | "scanning" | "partial" | "unavailable";
  subscription_bytes: number | null;
  emby_bytes: number | null;
  other_bytes: number | null;
  unused_bytes: number | null;
  measured_at: number | null;
  basis: "allocated" | "logical";
  code: string | null;
  refreshing: boolean;
};
export type StorageVolume = {
  storage_id: string;
  label: string;
  target_ids: string[];
  directories: string[];
  state: "available" | "unavailable";
  total_bytes: number | null;
  used_bytes: number | null;
  free_bytes: number | null;
  // An older snapshot has no breakdown; its categories remain unknown.
  breakdown?: StorageBreakdown;
};
export type MediaStorage = {
  state: "available" | "unavailable" | "not_configured";
  volumes: StorageVolume[];
};
export type RuntimeSample = {
  sampled_at: number;
  uptime_seconds: number;
  cpu_percent: number | null;
  memory_bytes: number | null;
  receive_bytes_per_second: number | null;
  send_bytes_per_second: number | null;
};
export type RecentJob = Pick<
  Job,
  "job_id" | "title" | "creator" | "state" | "updated_at" | "media_kind"
>;
export type MediaView = {
  configured: boolean;
  capabilities: {
    provider: string;
    source_kinds: string[];
    destination_modes?: DestinationMode[];
  };
  engine: { state: string; version: string | null; code: string };
  background?: {
    name: string;
    state: string;
    restarts: number;
    code: string | null;
  }[];
  accounts: Account[];
  targets: Target[];
  subscriptions: Subscription[];
  jobs: Job[];
  // Older servers omit overview. Its absence is unknown, never a count from the recent list.
  overview?: {
    runtime?: RuntimeSample;
    database?: {
      state: string;
      engine: string;
      schema_version: number;
      query_ms: number;
      size_bytes: number | null;
    };
    music?: {
      total: number;
      active: number;
      paused: number;
      attention: number;
      auto_download: number;
      members: number;
      files: number;
    };
    books?: {
      total: number;
      active: number;
      paused: number;
      attention: number;
      files: number;
    };
    recent?: { video: RecentJob[]; audio: RecentJob[]; book?: RecentJob[] };
    music_storage?: MediaStorage;
    jobs: {
      total: number;
      published: number;
      processing: number;
      queued: number;
      attention: number;
      cancelled: number;
    };
    storage: MediaStorage;
  } | null;
};
export type Format = { quality_id: string; label: string };
export type Part = {
  cid: string;
  index: number;
  title: string;
  duration_seconds: number | null;
  formats: Format[];
};
export type Preview = {
  preview_id: string;
  expires_at: number;
  video: {
    bvid: string;
    title: string;
    description: string | null;
    creator: { mid: string; name: string | null };
    cover_url: string | null;
    parts: Part[];
  };
};
export type RuleResult = {
  cid: string;
  decision: string;
  reason: string;
  automatic_enqueue_allowed: boolean;
  requires_rule_attention: boolean;
  trace: unknown;
};
export type RuleContext = {
  source: Source | null;
  member: boolean | null;
  fields: Partial<
    Record<
      Rule["field"],
      { available: boolean; value: string | number | boolean | null }
    >
  >;
};
export type RulePreview = { results: RuleResult[]; context?: RuleContext };
export type JobDetail = {
  job: Job;
  events: { state: string; code: string; at: number }[];
  metadata: null | {
    title: string;
    description: string;
    issues: string[];
    cover_available: boolean;
    original_title: string;
    original_description: string | null;
    title_overridden: boolean;
    description_overridden: boolean;
  };
};
export type QrSession = { qr_id: string; url: string; expires_at: number };
export const bestQuality = (): Quality => ({
  mode: "best",
  quality_id: null,
  allow_fallback: false,
});
export const emptyRules = (): Rules => ({
  schema_version: 1,
  revision: 1,
  whitelist: [],
  blacklist: [],
});
