export type SpeechConfig = {
  provider: "edge" | "fish" | "local";
  endpoint: string;
  model: string;
  voice: string;
  speed: number;
  pitch: number;
  daily_characters: number;
  revision: number;
  has_key: boolean;
  configured: boolean;
};
export type AudioSettings = {
  providers: SpeechConfig[];
  voices: { id: string; name: string }[];
  fish_models: string[];
  usage: Record<string, number>;
  budget_day: string;
};
export type AudioJob = {
  id: string;
  chapter_id: string;
  title: string;
  order: number;
  profile_id: string;
  state: string;
  code: string;
  completed_parts: number;
  content_hash: string;
  revision: number;
  artifact?: { duration: number; relative_path: string };
};
export type AudioView = AudioSettings & {
  jobs: AudioJob[];
  plan: {
    book_id: string;
    profile_id: string;
    auto_new: boolean;
    paused: boolean;
    revision: number;
  } | null;
};
export type AudioEstimate = {
  chapters: number;
  ready: number;
  existing: number;
  missing_text: number;
  changed_text: number;
  new_chapters: number;
  new_characters: number;
  profile_id: string;
};
export type AudioManifest = {
  job_id: string;
  text: string;
  content_hash: string;
  duration: number;
  url: string;
  sync: "sentence" | "segment";
  cues: { start: number; end: number; text_start: number; text_end: number }[];
};
export type ReaderState = {
  reader_id: string;
  book_id: string;
  chapter_id: string;
  content_hash: string;
  offset: number;
  seconds: number;
  sequence: number;
  sharing: boolean;
  revision: number;
};
export type ReadingView = {
  reader: ReaderState | null;
  progress: { chapter_id: string; offset: number; seconds: number } | null;
  choices: {
    id: string;
    label: string;
    actors: string[];
    actor_labels?: Record<string, string>;
  }[];
};

export function audioStatus(code: string) {
  return (
    (
      {
        ready: "已生成",
        queued: "等待生成",
        generating: "正在生成",
        paused: "已暂停",
        book_audio_unknown: "请求结果不明，已暂停重试",
        book_audio_remote_unknown: "服务结果不明，已暂停重试",
        book_audio_response_invalid: "返回的音频不完整，已暂停重试",
        book_audio_budget: "达到今日字符上限，等待额度恢复",
        book_audio_inflight: "等待同一内容的生成结果",
        book_audio_auth: "语音服务凭据无效",
        book_audio_quota: "语音服务额度不足",
        book_audio_rate_limited: "语音服务限流，稍后重试",
        book_audio_rejected: "语音服务拒绝请求，请检查配置",
        book_audio_assembly_failed: "音频合并失败，分段缓存仍保留",
        book_audio_file_missing: "已生成音频缺失或损坏，需手动重试",
        book_audio_not_configured: "语音服务尚未配置",
        book_audio_endpoint_changed: "本地服务地址已改变，请重新选择服务",
      } as Record<string, string>
    )[code] ?? code
  );
}
