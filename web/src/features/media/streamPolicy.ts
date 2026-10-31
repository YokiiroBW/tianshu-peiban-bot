import type { Quality, StreamOptions } from "./types";

export const videoQualities = [
  { quality_id: "16", label: "360P" },
  { quality_id: "32", label: "480P" },
  { quality_id: "64", label: "720P" },
  { quality_id: "74", label: "720P 60帧" },
  { quality_id: "80", label: "1080P" },
  { quality_id: "112", label: "1080P 高码率" },
  { quality_id: "116", label: "1080P 60帧" },
  { quality_id: "120", label: "4K" },
  { quality_id: "125", label: "HDR" },
  { quality_id: "126", label: "杜比视界" },
  { quality_id: "127", label: "8K" },
];
// Audio IDs are identifiers, not a numeric ranking.
export const audioQualities = [
  { quality_id: "30216", label: "64 kbps" },
  { quality_id: "30232", label: "132 kbps" },
  { quality_id: "30280", label: "192 kbps" },
  { quality_id: "30250", label: "杜比音频" },
  { quality_id: "30251", label: "Hi-Res" },
];
export const codecWords = {
  av1: "AV1",
  hevc: "HEVC / H.265",
  avc: "AVC / H.264",
};
export const defaultStreamOptions = (): StreamOptions => ({
  video_min_quality: null,
  video_max_quality: null,
  audio_min_quality: null,
  audio_max_quality: null,
  codecs: [],
  allow_hdr: true,
  allow_dolby_vision: true,
  allow_dolby_audio: true,
  allow_hires: true,
});
export function withStreamOptions(
  quality: Quality,
  options: StreamOptions,
): Quality {
  const { stream_options: _previous, ...legacy } = quality;
  return options.video_min_quality === null &&
    options.video_max_quality === null &&
    options.audio_min_quality === null &&
    options.audio_max_quality === null &&
    !options.codecs.length &&
    options.allow_hdr &&
    options.allow_dolby_vision &&
    options.allow_dolby_audio &&
    options.allow_hires
    ? legacy
    : { ...legacy, stream_options: options };
}
const rank = (values: typeof videoQualities, id: string) =>
  values.findIndex((value) => value.quality_id === id);
export function streamPolicyError(quality: Quality): string {
  const options = quality.stream_options;
  if (!options) return "";
  if (
    options.video_min_quality &&
    options.video_max_quality &&
    rank(videoQualities, options.video_min_quality) >
      rank(videoQualities, options.video_max_quality)
  )
    return "最低视频质量不能高于最高视频质量。";
  if (
    options.audio_min_quality &&
    options.audio_max_quality &&
    rank(audioQualities, options.audio_min_quality) >
      rank(audioQualities, options.audio_max_quality)
  )
    return "最低音频质量不能高于最高音频质量。";
  return "";
}
export function exactOutsidePolicy(quality: Quality): boolean {
  if (
    quality.mode !== "exact" ||
    !quality.quality_id ||
    !quality.stream_options
  )
    return false;
  const options = quality.stream_options;
  const requested = rank(videoQualities, quality.quality_id);
  return (
    (options.video_min_quality !== null &&
      requested < rank(videoQualities, options.video_min_quality)) ||
    (options.video_max_quality !== null &&
      requested > rank(videoQualities, options.video_max_quality)) ||
    (quality.quality_id === "125" && !options.allow_hdr) ||
    (quality.quality_id === "126" && !options.allow_dolby_vision)
  );
}
export function streamPolicySummary(quality: Quality): string {
  const options = quality.stream_options;
  if (!options) return "不限制质量范围与编码，允许特殊流";
  const label = (list: typeof videoQualities, id: string | null) =>
    id ? (list.find((item) => item.quality_id === id)?.label ?? id) : "不限";
  const parts: string[] = [];
  if (options.video_min_quality || options.video_max_quality)
    parts.push(
      `视频 ${label(videoQualities, options.video_min_quality)} ～ ${label(videoQualities, options.video_max_quality)}`,
    );
  if (options.audio_min_quality || options.audio_max_quality)
    parts.push(
      `音频 ${label(audioQualities, options.audio_min_quality)} ～ ${label(audioQualities, options.audio_max_quality)}`,
    );
  if (options.codecs.length)
    parts.push(
      `仅 ${options.codecs.map((codec) => codecWords[codec]).join(" → ")}`,
    );
  const excluded = [
    !options.allow_hdr && "HDR",
    !options.allow_dolby_vision && "杜比视界",
    !options.allow_dolby_audio && "杜比音频",
    !options.allow_hires && "Hi-Res",
  ].filter(Boolean);
  if (excluded.length) parts.push(`不使用 ${excluded.join("、")}`);
  return parts.join("；") || "不限制质量范围与编码，允许特殊流";
}
