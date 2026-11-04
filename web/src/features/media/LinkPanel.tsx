import { useEffect, useRef, useState, type FormEvent } from "react";
import { Download, Search } from "lucide-react";
import { StatePanel } from "../../components/StatePanel";
import { nativeResourceUrl } from "../../api/client";
import { AccountField, QualityField, TargetField } from "./Fields";
import { bestQuality, type Job, type Preview } from "./types";
import type { MediaController } from "./useMediaController";
import { timestamp } from "./wording";
import { streamPolicyError } from "./streamPolicy";

export function LinkPanel({
  media,
  onJobs,
}: {
  media: MediaController;
  onJobs: () => void;
}) {
  const [url, setUrl] = useState("");
  const [account, setAccount] = useState("");
  const [target, setTarget] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [quality, setQuality] = useState(bestQuality);
  const [reading, setReading] = useState(false);
  const version = useRef(0);
  const expiryTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const parseTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const [expired, setExpired] = useState(false);
  const mediaRef = useRef(media);
  mediaRef.current = media;
  useEffect(
    () => () => {
      mediaRef.current.cancel("resolve");
      clearTimeout(expiryTimer.current);
      version.current += 1;
    },
    [],
  );
  const clear = () => {
    version.current += 1;
    media.cancel("resolve");
    setReading(false);
    setPreview(null);
    setSelected([]);
    setExpired(false);
    setQuality(bestQuality());
    clearTimeout(expiryTimer.current);
  };
  const resolve = async (text = url) => {
    if (!text.trim()) return;
    clearTimeout(parseTimer.current);
    clear();
    const mine = version.current;
    setReading(true);
    media.clearError();
    const result = await media.request<Preview>("resolve", "resolve", {
      url: text.trim(),
      account_id: account || null,
    });
    if (mine !== version.current) return;
    if (result) {
      setPreview(result);
      setSelected(result.video.parts.map((part) => part.cid));
      setExpired(result.expires_at * 1000 <= Date.now());
      expiryTimer.current = setTimeout(
        () => setExpired(true),
        Math.max(0, result.expires_at * 1000 - Date.now()),
      );
    }
    setReading(false);
  };
  useEffect(() => {
    if (
      !/^https?:\/\/(?:[^/]+\.)?(?:bilibili\.com|b23\.tv)\//i.test(url.trim())
    )
      return;
    parseTimer.current = setTimeout(() => {
      void resolve(url);
    }, 700);
    return () => clearTimeout(parseTimer.current);
    // The submitted scope, rather than a rebuilt controller object, drives debounced parsing.
  }, [url, account]);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void resolve();
  };
  const parts =
    preview?.video.parts.filter((part) => selected.includes(part.cid)) ?? [];
  const formats = parts.length
    ? parts[0].formats.filter((format) =>
        parts.every((part) =>
          part.formats.some((item) => item.quality_id === format.quality_id),
        ),
      )
    : [];
  const enqueue = async () => {
    if (
      !preview ||
      !target ||
      !selected.length ||
      expired ||
      streamPolicyError(quality)
    )
      return;
    const result = await media.command<{ jobs: Job[] }>("enqueue", {
      preview_id: preview.preview_id,
      part_cids: selected,
      account_id: account || null,
      target_id: target,
      quality,
    });
    if (result) {
      media.setNotice(
        `已受理 ${result.jobs.length} 个下载任务，可在下载任务查看执行进度。`,
      );
      onJobs();
    }
  };
  return (
    <section aria-label="链接下载" className="media-stack">
      <div className="media-card">
        <div className="media-section-head">
          <h2>从视频链接开始</h2>
          <a href="#/system/tasks">查看全部任务记录</a>
        </div>
        <p className="muted">解析后选择分 P、画质和媒体库，再创建下载任务。</p>
        <form className="media-link-form" onSubmit={submit}>
          <label className="media-grow">
            B 站视频链接
            <input
              type="url"
              value={url}
              placeholder="https://www.bilibili.com/video/BV…"
              required
              maxLength={4096}
              onChange={(event) => {
                clear();
                setUrl(event.target.value);
              }}
            />
          </label>
          <button className="button" disabled={reading || !!media.busy}>
            <Search aria-hidden="true" />
            {reading ? "正在解析…" : "解析链接"}
          </button>
        </form>
        <div className="media-form-grid">
          <AccountField
            accounts={media.view?.accounts ?? []}
            value={account}
            onChange={(value) => {
              clear();
              setAccount(value);
            }}
          />
          <TargetField
            targets={media.view?.targets ?? []}
            value={target}
            onChange={setTarget}
          />
        </div>
      </div>
      {reading && (
        <StatePanel kind="loading" title="正在读取视频与可用画质">
          <p>读取使用当前选择的 B 站账号。</p>
        </StatePanel>
      )}
      {!preview && !reading && (
        <StatePanel kind="empty" title="粘贴链接，查看视频">
          <p>
            支持 B
            站视频链接和短链接。账号失效、视频无权限或缺失字段会在这里说明。
          </p>
        </StatePanel>
      )}
      {preview && (
        <article className="media-card media-preview">
          <div className="media-preview-head">
            {preview.video.cover_url &&
            /^https?:\/\//.test(preview.video.cover_url) ? (
              <img
                className="media-cover"
                src={nativeResourceUrl(preview.video.cover_url)}
                alt={`${preview.video.title} 的封面`}
                referrerPolicy="no-referrer"
              />
            ) : (
              <div className="media-cover media-cover-empty">
                来源未提供封面
              </div>
            )}
            <div>
              <h2>{preview.video.title}</h2>
              <p className="muted">
                {preview.video.creator.name || "UP 主昵称缺失"} ·{" "}
                {preview.video.bvid}
              </p>
              <p className="media-description">
                {preview.video.description || "来源未提供简介"}
              </p>
              <p className="muted">
                预览有效至 {timestamp(preview.expires_at)}
              </p>
            </div>
          </div>
          <fieldset className="media-parts">
            <legend>
              选择分 P（已选 {selected.length} / {preview.video.parts.length}）
            </legend>
            <label className="media-check">
              <input
                type="checkbox"
                checked={
                  selected.length === preview.video.parts.length &&
                  selected.length > 0
                }
                onChange={(event) => {
                  setSelected(
                    event.target.checked
                      ? preview.video.parts.map((part) => part.cid)
                      : [],
                  );
                  setQuality((current) => ({ ...current, ...bestQuality() }));
                }}
              />
              全选分 P
            </label>
            {preview.video.parts.map((part) => (
              <label key={part.cid} className="media-part">
                <input
                  type="checkbox"
                  checked={selected.includes(part.cid)}
                  onChange={(event) => {
                    setSelected((current) =>
                      event.target.checked
                        ? [...current, part.cid]
                        : current.filter((id) => id !== part.cid),
                    );
                    setQuality((current) => ({ ...current, ...bestQuality() }));
                  }}
                />
                <span>
                  <strong>
                    P{part.index} · {part.title}
                  </strong>
                  <small>
                    {part.duration_seconds == null
                      ? "时长缺失"
                      : `${Math.floor(part.duration_seconds / 60)}分${Math.round(part.duration_seconds % 60)}秒`}{" "}
                    ·{" "}
                    {part.formats.length
                      ? part.formats.map((format) => format.label).join(" / ")
                      : "暂无可取得画质"}
                  </small>
                </span>
              </label>
            ))}
          </fieldset>
          <QualityField
            value={quality}
            onChange={setQuality}
            formats={formats}
          />
          {expired && (
            <p className="media-warning" role="status">
              解析预览已过期，请重新解析链接后下载。
            </p>
          )}
          <div className="media-actions">
            <button
              className="button primary"
              disabled={
                !!media.busy ||
                !target ||
                !selected.length ||
                expired ||
                !!streamPolicyError(quality) ||
                parts.some((part) => !part.formats.length)
              }
              onClick={() => void enqueue()}
            >
              <Download aria-hidden="true" />
              {media.busy === "enqueue"
                ? "正在受理…"
                : `创建下载任务（${selected.length} 个分 P）`}
            </button>
            {!target && <span className="muted">请选择目标媒体库。</span>}
          </div>
        </article>
      )}
    </section>
  );
}
