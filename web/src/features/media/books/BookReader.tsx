import { DetailCard } from "../../../components/DetailCard";
import { useEffect, useMemo, useRef, useState } from "react";
import type { MediaController } from "../useMediaController";
import type { ChapterPreview, BookChapter } from "./types";
import type { AudioJob, AudioManifest } from "./audioTypes";
import { useBookReading, type ReadingPreference } from "./useBookReading";

export function BookReader({
  media,
  bookId,
  preview,
  jobs,
  onNext,
  autoplay,
  preference,
}: {
  media: MediaController;
  bookId: string;
  preview: ChapterPreview;
  jobs: AudioJob[];
  onNext: (chapter: BookChapter) => void;
  autoplay: boolean;
  preference: ReadingPreference;
}) {
  const ready = jobs.filter(
    (j) => j.chapter_id === preview.chapter.chapter_id && j.state === "ready",
  );
  const [choice, setChoice] = useState("");
  const job =
    ready.find((j) => j.id === choice) ??
    ready.find((j) => j.profile_id === preference.profileId) ??
    ready.at(-1);
  const [manifest, setManifest] = useState<AudioManifest | null>(null);
  const current = useRef(media);
  current.current = media;
  useEffect(() => {
    setManifest(null);
    let stopped = false;
    if (job)
      void current.current
        .request<AudioManifest>("book-audio-manifest", "books/audio/manifest", {
          job_id: job.id,
        })
        .then((result) => {
          if (!stopped && result) setManifest(result);
        });
    return () => {
      stopped = true;
      current.current.cancel("book-audio-manifest");
    };
  }, [job?.id]);
  if (job && (!manifest || manifest.job_id !== job.id))
    return (
      <article className="book-prose" role="status">
        正在读取本章音频和对应正文…
      </article>
    );
  return (
    <ReaderContent
      key={`${preview.chapter.chapter_id}:${manifest?.job_id ?? "text"}`}
      {...{
        media,
        bookId,
        preview,
        manifest,
        jobs,
        onNext,
        autoplay,
        preference,
      }}
      versionPicker={
        ready.length > 1 ? (
          <label>
            音频版本
            <select
              value={job?.id}
              onChange={(e) => {
                setChoice(e.target.value);
                preference.profileId =
                  ready.find((j) => j.id === e.target.value)?.profile_id ?? "";
              }}
            >
              {ready.map((r, i) => (
                <option key={r.id} value={r.id}>
                  已保存音频 · 版本 {i + 1}
                </option>
              ))}
            </select>
          </label>
        ) : null
      }
    />
  );
}

function ReaderContent({
  media,
  bookId,
  preview,
  manifest,
  jobs,
  onNext,
  autoplay,
  versionPicker,
  preference,
}: {
  media: MediaController;
  bookId: string;
  preview: ChapterPreview;
  manifest: AudioManifest | null;
  jobs: AudioJob[];
  onNext: (chapter: BookChapter) => void;
  autoplay: boolean;
  versionPicker: React.ReactNode;
  preference: ReadingPreference;
}) {
  const text = manifest?.text ?? preview.chapter.content;
  const hash = manifest?.content_hash ?? preview.chapter.content_hash;
  const reading = useBookReading(
    media,
    bookId,
    preview.chapter.chapter_id,
    hash,
    preference,
    manifest?.job_id,
  );
  const player = useRef<HTMLAudioElement>(null);
  const active = useRef<HTMLButtonElement>(null);
  const [offset, setOffset] = useState(0);
  const [follow, setFollow] = useState(true);
  const [continuous, setContinuous] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [playError, setPlayError] = useState("");
  const current = jobs.find((j) =>
    manifest
      ? j.id === manifest.job_id
      : j.chapter_id === preview.chapter.chapter_id,
  );
  const next = current
    ? jobs
        .filter(
          (j) =>
            j.order === current.order + 1 &&
            j.profile_id === current.profile_id,
        )
        .sort((a, b) => a.order - b.order)[0]
    : undefined;
  const advance = (chapter: AudioJob) => {
    if (current) preference.profileId = current.profile_id;
    onNext({
      chapter_id: chapter.chapter_id,
      title: chapter.title,
      order: chapter.order,
    });
  };
  const paragraphs = useMemo(() => {
    let at = 0;
    return text
      .split("\n")
      .map((line) => {
        const start = at;
        at += Array.from(line).length + 1;
        return { text: line, start, end: at - 1 };
      })
      .filter((p) => p.text.trim());
  }, [text]);
  const selected = paragraphs.findIndex(
    (p) => p.start <= offset && offset <= p.end,
  );
  useEffect(() => {
    const paragraph = active.current;
    const container = paragraph?.closest(".book-synced-prose");
    if (follow && paragraph && container) {
      const line = paragraph.getBoundingClientRect(),
        box = container.getBoundingClientRect();
      if (line.top < box.top || line.bottom > box.bottom)
        container.scrollBy({
          top: line.top - box.top - 12,
          behavior: "smooth",
        });
    }
  }, [selected, follow]);
  useEffect(() => {
    if (!reading.reader) return;
    setOffset(reading.reader.offset);
    if (player.current)
      player.current.currentTime = Math.min(
        reading.reader.seconds,
        manifest?.duration ?? 0,
      );
  }, [reading.reader?.reader_id]);
  async function play() {
    try {
      await player.current?.play();
      setPlayError("");
    } catch {
      setPlayError("播放尚未开始，请点击播放器的播放按钮。");
    }
  }
  return (
    <article className="book-reading-view">
      <div className="book-player">
        <h3>{preview.chapter.title}</h3>
        {hash === preview.chapter.content_hash && (
          <p className="book-fineprint muted">
            {preview.chapter.origin?.source_name
              ? `本章来自 ${preview.chapter.origin.source_name} · `
              : ""}
            {preview.cached ? "使用已经保存的正文" : "已从来源取得并保存正文"}
          </p>
        )}
        {versionPicker}
        {manifest ? (
          <>
            <audio
              ref={player}
              controls
              preload="metadata"
              src={manifest.url}
              onLoadedMetadata={() => {
                if (player.current) {
                  player.current.currentTime = Math.min(
                    reading.reader?.seconds ?? 0,
                    manifest.duration,
                  );
                  player.current.playbackRate = speed;
                }
                if (autoplay) void play();
              }}
              onTimeUpdate={() => {
                const seconds = player.current?.currentTime ?? 0;
                let cue: AudioManifest["cues"][number] | undefined;
                for (const item of manifest.cues) {
                  if (item.start > seconds) break;
                  cue = item;
                }
                if (cue) {
                  setOffset(cue.text_start);
                }
                reading.move(cue?.text_end ?? offset, seconds);
              }}
              onPause={() => void reading.save()}
              onSeeked={() => void reading.save()}
              onError={() =>
                setPlayError("音频加载失败，请检查登录状态后重新打开本章。")
              }
              onEnded={() => {
                void reading.save();
                if (continuous && next?.state === "ready") advance(next);
              }}
            />
            <div className="book-controls">
              <label>
                播放速度
                <select
                  value={speed}
                  onChange={(e) => {
                    const value = Number(e.target.value);
                    setSpeed(value);
                    if (player.current) player.current.playbackRate = value;
                  }}
                >
                  {[0.75, 1, 1.25, 1.5, 1.75, 2].map((v) => (
                    <option key={v} value={v}>
                      {v}×
                    </option>
                  ))}
                </select>
              </label>
              <label className="book-checkbox">
                <input
                  type="checkbox"
                  checked={continuous}
                  onChange={(e) => setContinuous(e.target.checked)}
                />
                连续播放
              </label>
              <label className="book-checkbox">
                <input
                  type="checkbox"
                  checked={follow}
                  onChange={(e) => setFollow(e.target.checked)}
                />
                正文跟随
              </label>
            </div>
            <p className="book-fineprint muted">
              {manifest.sync === "sentence" ? "句子时间轴同步" : "分段同步"} ·
              点击正文可跳转 · 音频已保存到 NAS
            </p>
            {hash !== preview.chapter.content_hash && (
              <p className="muted">
                正文已更新；当前显示此音频生成时保存的版本。
              </p>
            )}
          </>
        ) : (
          <p className="muted">
            本章尚未生成音频。可以先阅读，或在上方生成当前章节。
          </p>
        )}
        {playError && <p role="alert">{playError}</p>}
        <div className="book-co-reading">
          <label className="book-checkbox">
            <input
              type="checkbox"
              checked={reading.sharing}
              disabled={!reading.reader || !reading.binding}
              onChange={(e) => reading.share(e.target.checked)}
            />
            开启陪伴阅读
          </label>
          <label>
            陪读角色
            <select
              value={reading.binding}
              onChange={(e) => reading.bind(e.target.value)}
              disabled={!reading.choices.length}
            >
              {reading.choices.flatMap((c) =>
                c.actors.map((actor) => (
                  <option key={`${c.id}|${actor}`} value={`${c.id}|${actor}`}>
                    {c.actor_labels?.[actor] ?? actor}
                    {reading.choices.length > 1 ? ` · ${c.label}` : ""}
                  </option>
                )),
              )}
            </select>
          </label>
          <DetailCard title="陪读共享范围" className="book-fineprint muted">
            <p>
              角色平时可了解书库目录。开启后分享当前章节、位置和已读片段；离开阅读器或切到后台会停止陪读。
            </p>
          </DetailCard>
          <p role="status" className="book-fineprint">
            {!reading.synced
              ? "阅读位置尚未同步，陪读状态未确认。"
              : reading.sharing
                ? "正在共享阅读位置；可在聊天中与角色讨论。"
                : "陪读未开启"}
          </p>
        </div>
      </div>
      <div className="book-prose book-synced-prose" aria-label="阅读正文">
        {paragraphs.map((p, i) => (
          <button
            type="button"
            key={p.start}
            ref={i === selected ? active : undefined}
            className={`book-reading-paragraph ${i === selected ? "is-reading" : ""}`}
            onClick={() => {
              const cue = manifest?.cues.find((c) => c.text_end > p.start);
              if (cue && player.current) player.current.currentTime = cue.start;
              setOffset(p.start);
              reading.move(p.end, cue?.start ?? 0);
              void reading.save();
            }}
          >
            {p.text}
          </button>
        ))}
      </div>
      {next && (
        <button className="button" onClick={() => advance(next)}>
          下一章：{next.title}
          {next.state !== "ready" ? "（音频未就绪）" : ""}
        </button>
      )}
    </article>
  );
}
