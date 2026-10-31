export const providerNames = {
  netease: "网易云音乐",
  qq: "QQ 音乐",
  kugou: "酷狗音乐",
  local: "本地导入",
};
export type MusicProvider = "netease" | "qq" | "kugou";
export type MusicAccount = {
  account_id: string;
  provider: MusicProvider;
  user_id: string;
  label: string;
  state: "ready" | "auth_required" | "revoked";
  revision: number;
  checked_at: number;
};
export type SourcePlaylist = {
  source_id: string;
  name: string;
  description: string;
  cover_url: string | null;
  track_count: number | null;
  kind: "created" | "collected" | "liked";
};
export type MusicPlaylist = SourcePlaylist & {
  auto_download?: boolean;
  quality?: MusicQuality;
  playlist_id: string;
  account_id: string;
  revision: number;
  member_count: number;
  interval_seconds: number;
  state: "active" | "paused";
  synced_at: number;
  code: string;
};
export type MusicQuality = {
  level: "best" | "hires" | "lossless" | "exhigh" | "higher" | "standard";
  allow_fallback: boolean;
};
export type AudioQuality = {
  level: string;
  codec: string;
  sample_rate: number;
  channels: number;
  bits_per_sample: number;
  bit_rate: number;
  fallback: boolean;
};
export type AudioUnlock = {
  state:
    | "not_needed"
    | "decoding"
    | "decoded"
    | "key_required"
    | "unsupported"
    | "interrupted"
    | "failed";
  format?: string;
  tool?: string;
  version?: string;
};
export type AudioAttempt = {
  provider: keyof typeof providerNames;
  source_id: string;
  code: string;
  unlock?: AudioUnlock | null;
};
export type AudioRouting = {
  policy: "highest_available";
  complete?: boolean;
  deferred?: {
    provider: keyof typeof providerNames;
    code: string;
    retry_at: number;
  }[];
  providers: {
    provider: keyof typeof providerNames;
    matches: number;
    available_levels: string[];
    failures: string[];
    code?: string;
    retry_at?: number;
  }[];
  selected?: {
    provider: keyof typeof providerNames;
    quality: AudioQuality;
    reused: boolean;
  };
};
export type MusicSettings = {
  directory: string;
  layout: "artist_album" | "album_artist_album";
  filename: "artist_title";
  revision: number;
};
export type MusicView = {
  lyrics?: {
    tracked: number;
    synced: number;
    translated: number;
    counts: Record<string, number>;
  };
  maintenance?: MusicMaintenanceView;
  navidrome?: NavidromeView;
  imports?: {
    state: string;
    checked_at?: number;
    total?: number;
    failed?: number;
    queued?: number;
  };
  providers?: {
    provider: MusicProvider;
    label: string;
    available: boolean;
    login_types: string[];
  }[];
  configured: boolean;
  audio_download_available: boolean;
  accounts: MusicAccount[];
  playlists: MusicPlaylist[];
  settings: MusicSettings;
};
export type MusicLyrics = {
  state: string;
  coverage: { original: boolean; synced: boolean; translation_synced: boolean };
  source?: { provider: keyof typeof providerNames } | null;
  next_check_at?: number;
};
export type MusicMaintenanceView = {
  settings: {
    enabled: boolean;
    interval_seconds: number;
    playlist_interval_seconds: number;
    revision: number;
  };
  counts: { tracked: number; checked: number; due: number; upgrades: number };
  requests: {
    interval_seconds: number;
    hourly_limit: number;
    daily_limit: number;
    scopes: {
      provider: MusicProvider;
      account_id: string | null;
      hour_used: number;
      day_used: number;
      cooldown_until: number | null;
      auth_required: boolean | null;
      code: string | null;
    }[];
  };
  recent: {
    recording_id: string;
    title: string;
    outcome: string;
    checked_at: number | null;
  }[];
};
export type NavidromeConnection = {
  revision: number;
  connection_id: string;
  base_url: string;
  username: string;
  enabled: boolean;
  music_folder_id: string;
  music_directory: string;
  local_directory: string;
  version: string;
  can_scan: boolean;
  folders: { id: string; name: string }[];
  checked_at: number;
};
export type NavidromePlaylist = {
  link_id: string;
  playlist_id: string;
  revision: number;
  name: string;
  name_observed?: string;
  remote_id: string | null;
  public: boolean;
  auto_sync: boolean;
  status: string;
  code: string;
  total: number;
  synced_count: number;
  checked_at?: number;
  problem_count: number;
  problems: { position: number; title: string; code: string }[];
};
export type NavidromeView = {
  connection: NavidromeConnection | null;
  playlists: NavidromePlaylist[];
};
export type PlaylistDetail = {
  playlist: MusicPlaylist;
  tracks: {
    source_id: string;
    position: number;
    title: string;
    artists: string[];
    album: string;
    availability: string;
    audio_state: string;
    audio_code?: string;
    audio_quality?: AudioQuality | null;
    audio_unlock?: AudioUnlock | null;
    download_provider?: keyof typeof providerNames | null;
    download_source_id?: string | null;
    file?: string | null;
    job_id?: string | null;
  }[];
  offset: number;
  more: boolean;
};
