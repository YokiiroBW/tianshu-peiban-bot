export type BookMetadata = {
  title: string;
  author: string;
  description?: string;
  language?: string;
  tags?: string[];
  category?: string;
  cover_url?: string;
  publisher?: string;
  published_at?: string;
  isbn?: string;
  series?: string;
  series_index?: string;
};

export type BookArtifact = {
  root: string;
  relative_path: string;
  bytes: number;
  chapters: number;
  expected_chapters: number;
  complete: boolean;
  published_at: number;
  version: number;
};

export type Book = {
  source_selection?: {
    mode: string;
    checked_at: number;
    code: string;
    preferred_book_id: string;
    preferred_source_name: string;
    repaired_chapters: number;
    added_chapters: number;
    unmatched_chapters: number;
    chapters_by_source: { source_name: string; chapters: number }[];
    last_repair?: {
      chapter_title: string;
      from_source: string;
      source_name: string;
      code: string;
    } | null;
    candidates: {
      book_id: string;
      source_name: string;
      code: string;
      readable: boolean;
      latest_number?: number;
    }[];
  } | null;
  readability?: {
    state: "previewed" | "sampled" | "complete" | "failed";
    checked_at: number;
    sample_count: number;
    chapter_title?: string;
    code?: string;
  } | null;
  has_cover?: boolean;
  book_id: string;
  metadata: BookMetadata;
  metadata_revision: number;
  field_sources: Record<string, string>;
  manual_fields: string[];
  source_id: string;
  source_name: string;
  source_type: "remote" | "local" | "epub";
  chapter_count: number;
  downloaded: number;
  latest_chapter: string;
  revision: number;
  artifact: BookArtifact | null;
  status: string;
  group_key: string;
};

export type BookSource = {
  source_id: string;
  name: string;
  group: string;
  url: string;
  enabled: boolean;
  revision: number;
  code: string;
  checked_at: number | null;
  capabilities: {
    search: boolean;
    content: boolean;
    javascript: boolean;
    unsupported: string[];
    webview_http_fallback?: boolean;
  };
};

export type BookFeed = {
  feed_id: string;
  url: string;
  title?: string;
  warehouse_key?: string;
  page_url?: string;
  interval_seconds: number;
  state: string;
  code: string;
  revision: number;
  last_scan_at?: number;
  next_scan_at: number;
  result?: {
    imported: number;
    invalid: number;
    added?: number;
    updated?: number;
    unchanged?: number;
    reasons?: Record<string, number>;
  };
};

export type WarehouseEntry = {
  kind: "source" | "collection";
  entry_id: string;
  key: string;
  title: string;
  page_url: string;
  url: string;
  badges: string[];
  updated?: string;
  description?: string;
};
export type WarehousePage = {
  entries: WarehouseEntry[];
  page: number;
  has_next: boolean;
  page_url: string;
};

export type BookSubscription = {
  book_id: string;
  state: "active" | "paused";
  interval_seconds: number;
  last_scan_at: number | null;
  next_scan_at: number;
  code: string;
  revision: number;
  book: Book;
};

export type BookSettings = {
  directory: string;
  interval_seconds: number;
  auto_metadata: boolean;
  open_library: boolean;
  opds_url: string;
  opds_username?: string;
  opds_has_credentials?: boolean;
  revision: number;
};

export type BooksView = {
  configured: boolean;
  settings: BookSettings;
  sources: BookSource[];
  source_count: number;
  groups: string[];
  feeds: BookFeed[];
  subscriptions: BookSubscription[];
  library_count: number;
  engine: {
    name: string;
    available: boolean;
    javascript: string;
    webview: boolean;
  };
};

export type BookSearchResult = {
  search_id: string;
  state: "running" | "completed" | "cancelled";
  query: string;
  books: Book[];
  sources: { source_id: string; name: string; code: string; count: number }[];
  total: number;
  next_offset: number | null;
};

export type BookChapter = {
  chapter_id: string;
  title: string;
  order: number;
  is_vip?: boolean;
};
export type BookDetail = {
  book: Book;
  chapters: BookChapter[];
  offset: number;
  total: number;
};
export type ChapterPreview = {
  readability?: Book["readability"];
  chapter: BookChapter & {
    content: string;
    content_hash: string;
    origin?: { source_name: string; source_id: string; book_id: string } | null;
  };
  cached: boolean;
};
export type MetadataCandidate = {
  candidate_id: string;
  provider: string;
  metadata: Partial<BookMetadata>;
};
export type MetadataResult = {
  candidates: MetadataCandidate[];
  providers: { name: string; code: string; count?: number }[];
};
