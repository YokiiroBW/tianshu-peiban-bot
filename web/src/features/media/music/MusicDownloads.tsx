import { MediaDetailCard } from "../MediaDetailCard";
import { DetailCard } from "../../../components/DetailCard";
import { useEffect, useState } from "react";
import type { MediaController } from "../useMediaController";
import {
  providerNames,
  type AudioQuality,
  type AudioUnlock,
  type AudioAttempt,
  type AudioRouting,
  type MusicPlaylist,
  type MusicQuality,
} from "./types";
import { word } from "../wording";

export const musicLevels: Record<string, string> = {
  best: "最佳可用",
  hires: "Hi-Res",
  lossless: "无损",
  exhigh: "极高",
  higher: "较高",
  standard: "标准",
};
export function audioQualityText(quality?: AudioQuality | null) {
  if (!quality) return "尚未核验";
  return `${musicLevels[quality.level] ?? quality.level} · ${quality.codec.toUpperCase()} · ${quality.sample_rate / 1000} kHz${quality.bits_per_sample ? ` / ${quality.bits_per_sample} bit` : ""}${quality.fallback ? "（已降级）" : ""}`;
}
export function unlockText(unlock?: AudioUnlock | null) {
  if (!unlock) return "";
  const labels = {
    not_needed: "普通音频，无需解锁",
    decoding: "正在自动解锁",
    decoded: "已自动解锁",
    key_required: "解锁需要客户端密钥",
    unsupported: "暂不支持此加密版本",
    interrupted: "解锁已中断",
    failed: "解锁失败",
  };
  return `${labels[unlock.state]}${unlock.format ? ` · ${unlock.format.toUpperCase()}` : ""}`;
}
export function MusicDownloads({
  media,
  playlist,
  configured,
  refresh,
}: {
  media: MediaController;
  playlist: MusicPlaylist;
  configured: boolean;
  refresh: () => Promise<void>;
}) {
  const [quality, setQuality] = useState<MusicQuality>(
    playlist.quality ?? { level: "best", allow_fallback: true },
  );
  const [automatic, setAutomatic] = useState(playlist.auto_download ?? false);
  const [exporting, setExporting] = useState(false);
  useEffect(() => {
    setQuality(playlist.quality ?? { level: "best", allow_fallback: true });
    setAutomatic(playlist.auto_download ?? false);
  }, [playlist.revision]);
  const download = async () => {
    const result = await media.command<{ queued: number; unavailable: number }>(
      "music/playlists/download",
      {
        playlist_id: playlist.playlist_id,
        expected_revision: playlist.revision,
      },
    );
    if (result) {
      media.setNotice(
        `已加入下载队列 ${result.queued} 首，来源暂不可用 ${result.unavailable} 首。已有成品将先核验复用。`,
      );
      await refresh();
    }
  };
  return (
    <div className="media-stack music-downloads">
      <p className="muted">
        {playlist.auto_download ? "同步后自动下载新增歌曲" : "手动下载"} ·{" "}
        {musicLevels[playlist.quality?.level ?? "best"]}
      </p>
      <div className="music-actions">
        <button
          className="button"
          disabled={!configured || !!media.busy}
          onClick={() => void download()}
        >
          下载歌单
        </button>
        <a className="button" href="#/media/jobs">
          查看下载任务
        </a>
        <button
          className="button"
          disabled={!configured || exporting}
          onClick={async () => {
            setExporting(true);
            const result = await media.request<{
              directory: string;
              exported: number;
              total: number;
            }>("music-export", "music/playlists/export", {
              playlist_id: playlist.playlist_id,
            });
            if (result)
              media.setNotice(
                `已导出 ${result.exported}/${result.total} 首到 ${result.directory}，缺失歌曲保留在 JSON 清单。`,
              );
            setExporting(false);
          }}
        >
          {exporting ? "正在导出…" : "导出 M3U8 / JSON"}
        </button>
      </div>
      {!configured && <p className="muted">保存音乐目录后即可下载。</p>}
      <MediaDetailCard media={media} title="下载设置">
        <form
          className="media-stack"
          onSubmit={async (event) => {
            event.preventDefault();
            if (
              await media.command("music/playlists/downloads", {
                playlist_id: playlist.playlist_id,
                expected_revision: playlist.revision,
                quality,
                auto_download: automatic,
              })
            ) {
              media.setNotice(
                automatic
                  ? "下载设置已保存，歌单已加入队列。"
                  : "下载设置已保存。",
              );
              await refresh();
            }
          }}
        >
          <label>
            期望音质
            <select
              value={quality.level}
              onChange={(event) =>
                setQuality({
                  ...quality,
                  level: event.target.value as MusicQuality["level"],
                })
              }
            >
              {Object.entries(musicLevels).map(([key, name]) => (
                <option value={key} key={key}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label className="media-check">
            <input
              type="checkbox"
              checked={quality.allow_fallback}
              onChange={(event) =>
                setQuality({ ...quality, allow_fallback: event.target.checked })
              }
            />
            允许使用当前账号可下载的较低音质
          </label>
          <label className="media-check">
            <input
              type="checkbox"
              checked={automatic}
              onChange={(event) => setAutomatic(event.target.checked)}
            />
            同步后自动下载新增歌曲
          </label>
          <p className="muted">
            每次先搜索所有已登录平台，从 Hi-Res
            开始检查可下载音质，统一比较后选择最高可用音源；所选期望音质不会限制更高音质。已有同等或更高音质文件直接复用，需要时自动解锁、写标签并归档。关闭降级时，低于期望音质的结果不会归档。
          </p>
          <button
            className="button"
            disabled={!!media.busy || (automatic && !configured)}
          >
            保存下载设置
          </button>
        </form>
      </MediaDetailCard>
    </div>
  );
}

export function MusicAudioReceipt({
  receipt,
  unlock,
  attempts,
  routing,
}: {
  unlock?: AudioUnlock | null;
  attempts?: AudioAttempt[];
  routing?: AudioRouting | null;
  receipt:
    | {
        files: { path: string; kind: string }[];
        warnings: string[];
        unlock?: AudioUnlock | null;
      }
    | null
    | undefined;
}) {
  return (
    <section className="media-stack">
      <h4>音乐归档</h4>
      {routing && (
        <div className="media-stack">
          <p>
            聚合比较：已检查{" "}
            {routing.providers.filter((p) => !p.retry_at).length}{" "}
            个账号，优先最高可用音质。
          </p>
          {routing.complete === false && (
            <p className="media-warning">
              部分渠道待补查，当前选择可用渠道中的最高音质；补查后仅替换更高音质版本。
            </p>
          )}
          {routing.deferred?.map((channel, index) => (
            <p className="muted" key={index}>
              {providerNames[channel.provider]}：{word(channel.code)}
              ，恢复后自动补查。
            </p>
          ))}
          {routing.selected && (
            <p>
              {routing.selected.reused
                ? "复用已有文件"
                : `选择 ${providerNames[routing.selected.provider]}`}{" "}
              · {audioQualityText(routing.selected.quality)}
            </p>
          )}
          <DetailCard title="各平台查询结果">
            {routing.providers.map((provider, index) => (
              <p key={index}>
                {providerNames[provider.provider]}：
                {provider.retry_at
                  ? "暂不可查询，待补查"
                  : provider.available_levels.length
                    ? provider.available_levels
                        .map((level) => musicLevels[level] ?? "待核验音质")
                        .join("、")
                    : provider.matches === 0
                      ? "未找到可确认的同版本歌曲"
                      : "未取得可用音源"}
                {provider.failures
                  .filter((code) => code !== "music_download_forbidden")
                  .map((code) => ` · ${word(code)}`)
                  .join("")}
              </p>
            ))}
          </DetailCard>
        </div>
      )}
      {(unlock || receipt?.unlock) && (
        <p>{unlockText(unlock || receipt?.unlock)}</p>
      )}
      {!!attempts?.length && (
        <DetailCard title="其他来源的处理结果">
          {attempts.map((attempt, index) => (
            <p key={index}>
              {providerNames[attempt.provider]}：{word(attempt.code)}
            </p>
          ))}
        </DetailCard>
      )}
      {receipt ? (
        <>
          {receipt.files.map((file) => (
            <p className="music-file" key={file.path}>
              {file.path}
            </p>
          ))}
          {receipt.warnings.map((code) => (
            <p className="media-warning" key={code}>
              {word(code)}
            </p>
          ))}
        </>
      ) : (
        <p className="muted">尚未完成文件归档。</p>
      )}
    </section>
  );
}
