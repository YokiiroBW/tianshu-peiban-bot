import { DetailCard } from "../../../components/DetailCard";
import { useEffect, useRef, useState } from "react";
import type { MediaController } from "../useMediaController";
import type { AudioEstimate, AudioView, AudioJob } from "./audioTypes";
import { audioStatus } from "./audioTypes";

export function BookAudioPanel({
  media,
  bookId,
  chapterId,
  onView,
}: {
  media: MediaController;
  bookId: string;
  chapterId?: string;
  onView: (view: AudioView) => void;
}) {
  const current = useRef({ media, onView });
  current.current = { media, onView };
  const [view, setView] = useState<AudioView | null>(null);
  const [provider, setProvider] = useState("edge");
  const [range, setRange] = useState("chapter");
  const [automatic, setAutomatic] = useState(false);
  const [estimate, setEstimate] = useState<AudioEstimate | null>(null);
  const [retry, setRetry] = useState<AudioJob | null>(null);
  const [expanded, setExpanded] = useState(false);
  async function refresh() {
    const result = await current.current.media.request<AudioView>(
      "book-audio-view",
      "books/audio/view",
      { book_id: bookId },
    );
    if (result) {
      setView(result);
      current.current.onView(result);
    }
  }
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => {
      if (!document.hidden) void refresh();
    }, 3500);
    return () => {
      clearInterval(timer);
      current.current.media.cancel("book-audio-view");
      current.current.media.cancel("book-audio-estimate");
    };
  }, [bookId]);
  useEffect(() => {
    setEstimate(null);
  }, [provider, range, chapterId]);
  const selected = view?.providers.find((p) => p.provider === provider);
  const active = view?.jobs.filter((j) => j.state !== "ready") ?? [];
  const request = {
    book_id: bookId,
    provider,
    ...(range === "chapter"
      ? { chapter_ids: chapterId ? [chapterId] : [] }
      : {}),
  };
  return (
    <DetailCard
      title={
        <>
          提前生成听书音频 ·{" "}
          {view?.jobs.filter((j) => j.state === "ready").length ?? 0} 章已就绪
          {active.some((job) => ["unknown", "failed"].includes(job.state))
            ? " · 有任务需要处理"
            : ""}
        </>
      }
      className="book-audio-panel"
      open={expanded}
      onOpenChange={setExpanded}
    >
      {media.error && (
        <p className="media-message media-warning" role="alert">
          {media.error}
        </p>
      )}
      {media.notice && (
        <p className="media-message" role="status">
          {media.notice}
        </p>
      )}

      <div className="book-audio-controls">
        <label>
          生成服务
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
          >
            <option value="edge">Edge</option>
            <option value="fish">Fish Audio</option>
            <option value="local">本地接口</option>
          </select>
        </label>
        <label>
          生成范围
          <select value={range} onChange={(e) => setRange(e.target.value)}>
            <option value="chapter">当前章节</option>
            <option value="all">所有已保存正文</option>
          </select>
        </label>
        <label className="book-checkbox">
          <input
            type="checkbox"
            checked={automatic}
            onChange={(e) => setAutomatic(e.target.checked)}
          />
          追更后自动生成新增章节
        </label>
        <button
          className="button"
          disabled={
            !selected?.configured || (range === "chapter" && !chapterId)
          }
          onClick={async () => {
            const result = await media.request<AudioEstimate>(
              "book-audio-estimate",
              "books/audio/estimate",
              request,
            );
            if (result) setEstimate(result);
          }}
        >
          计算待生成章节
        </button>
      </div>
      {selected && !selected.configured && (
        <p className="muted">请先在“听书设置”中配置此服务。</p>
      )}
      {estimate && (
        <div className="book-audio-estimate">
          <p>
            新增 {estimate.new_chapters} 章，约{" "}
            {estimate.new_characters.toLocaleString()} 字；复用 {estimate.ready}{" "}
            章，已有任务 {estimate.existing} 章。
            {estimate.missing_text > 0 &&
              `另有 ${estimate.missing_text} 章正文尚未下载。`}
            {estimate.changed_text > 0 &&
              ` ${estimate.changed_text} 章正文已修改，保留原音频版本。`}
          </p>
          <button
            className="button primary"
            disabled={!!media.busy}
            onClick={async () => {
              const result = await media.command("books/audio/generate", {
                ...request,
                profile_id: estimate.profile_id,
                auto_new: automatic,
              });
              if (result) {
                setEstimate(null);
                setExpanded(false);
                await refresh();
              }
            }}
          >
            开始生成
            {provider === "fish" ? "（使用 Fish 额度）" : "并保存到 NAS"}
          </button>
        </div>
      )}
      {view?.plan && (
        <div className="book-controls">
          <span className="muted">
            {view.plan.auto_new
              ? "新增章节自动生成已开启"
              : "新增章节自动生成已关闭"}
          </span>
          <button
            className="button"
            onClick={async () => {
              await media.command("books/audio/control", {
                book_id: bookId,
                expected_revision: view.plan!.revision,
                action: view.plan!.paused ? "resume" : "pause",
              });
              await refresh();
            }}
          >
            {view.plan.paused ? "恢复生成" : "暂停生成"}
          </button>
          <button
            className="button"
            onClick={async () => {
              await media.command("books/audio/control", {
                book_id: bookId,
                expected_revision: view.plan!.revision,
                action: view.plan!.auto_new ? "auto_off" : "auto_on",
              });
              await refresh();
            }}
          >
            {view.plan.auto_new ? "关闭音频追更" : "开启音频追更"}
          </button>
        </div>
      )}
      {active.length > 0 && (
        <DetailCard title={<>生成队列（{active.length} 章）</>}>
          <ul className="book-audio-queue">
            {active.map((job) => (
              <li key={job.id}>
                <span>
                  {job.title} · {audioStatus(job.code)}
                  {job.completed_parts > 0
                    ? ` · 已保存 ${job.completed_parts} 段`
                    : ""}
                </span>
                {["unknown", "failed", "waiting"].includes(job.state) && (
                  <button className="button" onClick={() => setRetry(job)}>
                    处理
                  </button>
                )}
              </li>
            ))}
          </ul>
        </DetailCard>
      )}
      {retry && (
        <div className="media-message">
          <p>
            {retry.title}
            ：已有分段会复用。若上次请求结果不明，重新请求缺失分段可能再次扣费。
          </p>
          <button
            className="button"
            onClick={async () => {
              await media.command("books/audio/retry", {
                job_id: retry.id,
                expected_revision: retry.revision,
                allow_charge: true,
              });
              setRetry(null);
              await refresh();
            }}
          >
            重试缺失分段
          </button>
          <button className="button" onClick={() => setRetry(null)}>
            取消
          </button>
        </div>
      )}
    </DetailCard>
  );
}
