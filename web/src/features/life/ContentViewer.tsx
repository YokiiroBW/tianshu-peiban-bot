import { useEffect, useRef, useState } from "react";
import { readFailure } from "../../app/integrationApi";
import { readContent, originalContent, type LifeAccess } from "./runtimeApi";
import type { content_ref, content_read_response, range } from "./runtimeTypes";

const gapNames: Record<string, string> = {
  frames_sampled: "画面为抽样帧",
  audio_not_transcribed: "音轨尚未转成文字",
  image_resized: "图片展示已缩放",
  page_images_not_extracted: "页内图片未提取",
  no_text_layer: "没有可提取文字层",
  range_truncated: "本次范围已截短",
};
const contentNames: Record<content_ref["kind"], string> = {
  text: "文字",
  article: "文章",
  image: "图片",
  video: "视频",
  audio: "音频",
};
export const contentLabel = (kind: content_ref["kind"]) => contentNames[kind];
export const rangeText = (r: range) =>
  `${r.start}–${r.end} ${{ characters: "字符", seconds: "秒", pages: "页", bytes: "字节" }[r.unit]}`;
export function ContentViewer({
  access,
  contentRef,
  readingId = null,
  initialRange,
  onRead,
}: {
  access: LifeAccess;
  contentRef: content_ref;
  readingId?: string | null;
  initialRange?: range;
  onRead?: () => void;
}) {
  const unit = contentRef.coverage.unit;
  const imagePreview = contentRef.kind === "image";
  const [start, setStart] = useState(
      initialRange?.start ?? contentRef.coverage.start,
    ),
    [end, setEnd] = useState(
      initialRange?.end ??
        Math.min(
          contentRef.coverage.end,
          unit === "seconds"
            ? 30
            : unit === "characters"
              ? 8000
              : contentRef.coverage.end,
        ),
    );
  const [answer, setAnswer] = useState<content_read_response | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const current = useRef<AbortController | null>(null);
  useEffect(() => () => current.current?.abort(), []);
  async function read(e?: React.FormEvent) {
    e?.preventDefault();
    current.current?.abort();
    const control = new AbortController();
    current.current = control;
    setBusy(true);
    setError("");
    setAnswer(null);
    try {
      const result = await readContent(
        access,
        contentRef,
        imagePreview
          ? {
              unit,
              start: contentRef.coverage.start,
              end: contentRef.coverage.end,
            }
          : { unit, start, end },
        control.signal,
        readingId,
      );
      if (!control.signal.aborted) {
        setAnswer(result);
        onRead?.();
      }
    } catch (cause) {
      if (!control.signal.aborted) setError(readFailure(cause));
    } finally {
      if (!control.signal.aborted) setBusy(false);
    }
  }
  async function download() {
    current.current?.abort();
    const control = new AbortController();
    current.current = control;
    setBusy(true);
    setError("");
    try {
      const blob = await originalContent(access, contentRef, control.signal);
      if (control.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${imagePreview ? "原图" : "原件"}-版本${contentRef.version}`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) {
      if (!control.signal.aborted) setError(readFailure(cause));
    } finally {
      if (!control.signal.aborted) setBusy(false);
    }
  }
  return (
    <div className="life-content-reader">
      <p className="muted">
        {contentNames[contentRef.kind]} · 原件版本 {contentRef.version}
        {!imagePreview && ` · 可取范围 ${rangeText(contentRef.coverage)}`}
      </p>
      <form className="life-form-row" onSubmit={read}>
        {!imagePreview && (
          <>
            <label>
              从
              <input
                type="number"
                min={0}
                value={start}
                onChange={(e) => setStart(Number(e.target.value))}
              />
            </label>
            <label>
              到
              <input
                type="number"
                min={start + 1}
                value={end}
                onChange={(e) => setEnd(Number(e.target.value))}
              />
            </label>
          </>
        )}
        <button
          className="button primary"
          disabled={busy || (!imagePreview && end <= start)}
        >
          {imagePreview
            ? busy
              ? "正在查看图片…"
              : "查看图片"
            : busy
              ? "正在实读…"
              : "读取此范围"}
        </button>
      </form>
      <button
        className="button"
        disabled={busy}
        onClick={() => void download()}
      >
        {imagePreview ? "下载原图" : "下载授权原件"}
      </button>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {answer && (
        <article>
          <p role="status">
            {imagePreview ? (
              answer.representations.some((item) => item.kind === "image") ? (
                `已取得图片预览${answer.complete ? "" : "（预览有缺口）"}`
              ) : (
                "本次未取得可展示的图片。"
              )
            ) : (
              <>
                实际取得 {rangeText(answer.coverage)} ·{" "}
                {answer.complete ? "本次范围完整" : "本次范围有缺口"}
              </>
            )}
          </p>
          {answer.gaps.length > 0 && (
            <ul className="life-coverage-gaps">
              {answer.gaps.map((gap) => (
                <li key={gap}>
                  {imagePreview && gap === "range_truncated"
                    ? "图片预览尚未完整取得"
                    : (gapNames[gap] ?? gap)}
                </li>
              ))}
            </ul>
          )}
          {answer.text !== null && (
            <div className="life-original-text">{answer.text}</div>
          )}
          <div className="life-media-grid">
            {answer.representations.map((item, index) => (
              <figure key={`${item.sha256}:${index}`}>
                {item.kind === "audio_clip" ? (
                  <audio
                    controls
                    src={`data:${item.media_type};base64,${item.data_base64}`}
                  />
                ) : (
                  <img
                    src={`data:${item.media_type};base64,${item.data_base64}`}
                    alt={
                      item.kind === "video_frame"
                        ? `实际画面 ${item.at_seconds} 秒`
                        : "原件图片"
                    }
                  />
                )}
                <figcaption>
                  {item.at_seconds !== null
                    ? `${item.at_seconds} 秒画面`
                    : "原件内容"}
                </figcaption>
              </figure>
            ))}
          </div>
          {!imagePreview &&
            answer.text === null &&
            !answer.representations.length && (
              <p>此范围没有取得可展示的正文或媒体。</p>
            )}
        </article>
      )}
    </div>
  );
}
