import { DetailCard } from "../../../components/DetailCard";
import { useState } from "react";
import type { MediaController } from "../useMediaController";
import { timestamp, word } from "../wording";
import { providerNames, type MusicMaintenanceView } from "./types";

export function MusicMaintenance({
  media,
  view,
  refresh,
}: {
  media: MediaController;
  view: MusicMaintenanceView;
  refresh: () => Promise<void>;
}) {
  const [enabled, setEnabled] = useState(view.settings.enabled);
  const [interval, setInterval] = useState(
    String(view.settings.playlist_interval_seconds),
  );
  const outcomes: Record<string, string> = {
    upgraded: "已升级音质",
    unchanged: "复用本地歌曲",
    downloaded: "已归档",
    partial: "部分渠道暂不可用",
    unavailable: "本轮未完成",
    pending: "等待巡检",
  };
  return (
    <section className="media-card media-stack" aria-label="音乐自动维护">
      <div className="media-section-head">
        <h3>自动维护</h3>
        <span className="muted">
          音质巡检 · {view.settings.enabled ? "已开启" : "已暂停"}
        </span>
      </div>
      <p className="muted">
        每 7
        天分批检查已归档的订阅歌曲，比较全部可用渠道，只升级验证后更高音质的版本。同一首歌只检查一次；新歌下载优先，限流或预算耗尽时顺延。
        暂不可查询的渠道单独等待，其他渠道继续比较；恢复后自动补查并复用同等或更好的本地文件。
      </p>
      <DetailCard title={"音乐自动维护设置"}>
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
        <div className="music-select-row">
          <label>
            歌单自动同步周期
            <select
              value={interval}
              onChange={(event) => setInterval(event.target.value)}
            >
              <option value="21600">每 6 小时</option>
              <option value="43200">每 12 小时</option>
              <option value="86400">每天</option>
              {![21600, 43200, 86400].includes(
                view.settings.playlist_interval_seconds,
              ) && (
                <option value={view.settings.playlist_interval_seconds}>
                  当前周期
                </option>
              )}
            </select>
          </label>
          <label className="media-check">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(event) => setEnabled(event.target.checked)}
            />
            每周检查音质升级
          </label>
          <button
            className="button"
            disabled={!!media.busy}
            onClick={async () => {
              if (
                await media.command("music/maintenance/save", {
                  enabled,
                  playlist_interval_seconds: Number(interval),
                  expected_revision: view.settings.revision,
                })
              ) {
                media.setNotice("自动维护设置已保存，歌单将错开同步。");
                await refresh();
              }
            }}
          >
            保存维护设置
          </button>
        </div>
      </DetailCard>
      <p className="muted">
        已记录 {view.counts.tracked} 首 · 已检查 {view.counts.checked} 首 · 到期{" "}
        {view.counts.due} 首 · 累计升级 {view.counts.upgrades} 次
      </p>
      <DetailCard title="请求节流与最近检查">
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

        <div className="media-stack">
          <p className="muted">
            同步、搜索、音质查询和下载共享请求控制：间隔至少{" "}
            {view.requests.interval_seconds} 秒，每个平台及账号最多{" "}
            {view.requests.hourly_limit} 次/小时、{view.requests.daily_limit}{" "}
            次/日。遇到限流统一冷却，遇到登录失效暂停该账号。这些是本地保护预算，不是平台承诺的安全阈值。
          </p>
          {view.requests.scopes
            .filter((scope) => !scope.account_id)
            .map((scope) => (
              <p key={scope.provider}>
                {providerNames[scope.provider]}：本小时 {scope.hour_used ?? 0}{" "}
                次 / 本日 {scope.day_used ?? 0} 次
                {scope.cooldown_until &&
                scope.cooldown_until * 1000 > Date.now()
                  ? ` · 冷却至 ${timestamp(scope.cooldown_until)}`
                  : ""}
              </p>
            ))}
          {view.recent.map((row) => (
            <div className="music-playlist" key={row.recording_id}>
              <strong>{row.title}</strong>
              <span>
                {outcomes[row.outcome] ?? word(row.outcome)} ·{" "}
                {row.checked_at ? timestamp(row.checked_at) : "尚未检查"}
              </span>
            </div>
          ))}
        </div>
      </DetailCard>
    </section>
  );
}
