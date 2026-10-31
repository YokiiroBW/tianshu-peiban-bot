import type { useMediaController } from "../useMediaController";

export type Media = ReturnType<typeof useMediaController>;
export type Kind = "general" | "private";
export type LanguageEvidence = { language: string; basis: string };
export type ComicVersion = LanguageEvidence & {
  id: string;
  scanlator: string;
  chapters: number;
};
export type Metadata = {
  title: string;
  author: string;
  artist: string;
  description: string;
  language: string;
  direction: "rtl" | "ltr" | "vertical";
  tags: string[];
};
export type Comic = {
  language_evidence?: LanguageEvidence;
  chinese_available?: boolean;
  comic_id: string;
  metadata: Metadata;
  source_id: string;
  source_name: string;
  source_type: "remote" | "local";
  library_kind: Kind;
  chapter_count: number;
  downloaded: number;
  revision: number;
  group_key: string;
  has_cover: boolean;
};
export type Chapter = {
  language_evidence?: LanguageEvidence;
  job?: {
    job_id: string;
    state: string;
    stage: string;
    code: string;
    comic_pages_done: number;
    comic_pages_total: number;
    can_retry: boolean;
  } | null;
  source_missing?: boolean;
  chapter_id: string;
  title: string;
  number: string;
  scanlator: string;
  order: number;
  page_count: number;
  artifact?: {
    page_count: number;
    relative_path: string;
    sha256: string;
    asset_receipt: { entry_id: string; content_version: string };
    komga: { reader_url: string };
  } | null;
};
export type Detail = {
  versions?: ComicVersion[];
  comic: Comic;
  chapters: Chapter[];
  total: number;
  next_offset: number | null;
  scanlators: string[];
};
export type Source = {
  health?: Partial<Record<SourceStage, SourceObservation>>;
  available?: boolean;
  search_checked_at?: number;
  download_checked_at?: number;
  download_code?: string;
  source_id: string;
  source_key: string;
  name: string;
  language: string;
  content_warning: string;
  enabled: boolean;
  code: string;
  revision: number;
};
export type SourceStage =
  | "connectivity"
  | "search"
  | "directory"
  | "preview"
  | "download"
  | "package"
  | "archive"
  | "reader";
export type SourceObservation = {
  code: string;
  checked_at: number;
  last_success_at?: number;
  last_failure_at?: number;
  last_failure_code?: string;
  count?: number;
  returned?: number;
  search_mode?: string;
  page_count?: number;
  library_kind?: Kind;
  cached?: boolean;
  http_status?: number;
  scope?: string;
  query_count?: number;
  query_errors?: string[];
};
export type Connection = {
  ca_pem?: string;
  url: string;
  username: string;
  has_credentials: boolean;
  password?: string;
  token?: string;
};
export type Target = {
  directory: string;
  asset_target_id: string;
  asset_library_id: string;
  komga_library_id: string;
  komga_root: string;
};
export type Settings = {
  publisher_socket?: string;
  import_socket?: string;
  revision: number;
  import_directory: string;
  interval_seconds: number;
  connections: Partial<
    Record<"suwayomi" | "komga" | "assetlibrary", Connection>
  >;
  targets: Partial<Record<Kind, Target>>;
};
export type Subscription = {
  version_policy?: { language: string; scanlator: string };
  comic_id: string;
  comic: Comic;
  state: string;
  revision: number;
  interval_seconds: number;
  next_scan_at: number;
  last_scan_at?: number;
  last_added?: number;
  code: string;
};
export type View = {
  settings: Settings;
  configured: boolean;
  subscriptions: Subscription[];
  counts: {
    comics: number;
    subscriptions: number;
    downloaded_chapters: number;
  };
};
export type SearchResult = {
  alias_resolution?: {
    state: string;
    names: string[];
    selected?: { title: string; provider: string; url: string } | null;
    candidates: { id: string; title: string; authors: string[] }[];
    provider_unavailable?: boolean;
  };
  search_id: string;
  state: string;
  query: string;
  queries?: string[];
  aliases?: string[];
  language?: string;
  page: number;
  total: number;
  comics: Comic[];
  sources: {
    source_id: string;
    name: string;
    code: string;
    count: number;
    filtered?: number;
    search_mode?: string;
    has_next_page?: boolean;
    query_errors?: string[];
    query_count?: number;
    language_filter?: string;
  }[];
};
export type Mutate = (
  operation: string,
  body: object,
  notice?: string,
) => Promise<boolean>;
export const kinds: [Kind, string][] = [
  ["general", "漫画"],
  ["private", "私有收藏"],
];
