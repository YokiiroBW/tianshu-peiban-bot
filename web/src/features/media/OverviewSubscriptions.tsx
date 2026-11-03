import { ArrowRight, Music2, Rss } from "lucide-react";
import type { MediaView } from "./types";
import { count } from "./overviewPresentation";

export function OverviewSubscriptions({
  view,
  music = false,
}: {
  view: MediaView;
  music?: boolean;
}) {
  const data = view.overview?.music;
  const subscriptions = view.subscriptions;
  const title = music ? "音乐订阅" : "视频订阅";
  const sealChar = music ? "乐" : "影";
  const subtitle = music
    ? "歌单自动同步与本地曲库"
    : "按订阅规则持续检测并拉取更新";
  const total = music ? data?.total : subscriptions.length;
  const href = music ? "#/media/music" : "#/media/video";
  const groups = music
    ? [
        { label: "自动同步", value: data?.active, tone: "blue" },
        { label: "已暂停", value: data?.paused, tone: "gray" },
      ]
    : [
        {
          label: "运行中",
          value: subscriptions.filter((s) => s.state === "active").length,
          tone: "blue",
        },
        {
          label: "已暂停",
          value: subscriptions.filter((s) => s.state === "paused").length,
          tone: "gray",
        },
        {
          label: "需要处理",
          value: subscriptions.filter((s) =>
            ["auth_required", "rule_error"].includes(s.state),
          ).length,
          tone: "red",
        },
      ];
  const facts = music
    ? [
        ...groups,
        { label: "同步异常", value: data?.attention, tone: "red" },
        { label: "自动下载", value: data?.auto_download, tone: "blue" },
      ]
    : [
        ...groups,
        {
          label: "待建立基线",
          value: subscriptions.filter((s) => !s.baseline_ready).length,
          tone: "yellow",
        },
      ];
  const Icon = music ? Music2 : Rss;
  let offset = 0;
  return (
    <section
      className="curio-card media-card overview-subscriptions"
      aria-label={`${title}状态`}
    >
      <div className="section-head media-section-head">
        <div className="section-title-group">
          <div className="section-seal-icon" aria-hidden="true">
            {sealChar}
          </div>
          <div>
            <h2 className="section-title">
              <Icon aria-hidden="true" className="sr-only" />
              {title}
            </h2>
            <div className="section-sub">{subtitle}</div>
          </div>
        </div>
        <a className="ink-link overview-text-link" href={href}>
          管理{music ? "歌单" : "订阅"}
          <ArrowRight aria-hidden="true" />
        </a>
      </div>

      <div className="sub-ring-layout overview-subscription-monitor">
        <div className="donut-wrap overview-ring">
          <svg
            viewBox="0 0 180 180"
            role="img"
            aria-label={`${title}状态：${groups.map((g) => `${g.label} ${count(g.value)} 个`).join("，")}`}
          >
            <circle className="overview-ring-track" cx="90" cy="90" r="70" />
            {groups.map((g) => {
              const portion = total ? ((g.value ?? 0) / total) * 100 : 0;
              const start = offset;
              offset += portion;
              return portion > 0 ? (
                <circle
                  key={g.label}
                  className={`overview-ring-segment overview-tone-${g.tone}`}
                  cx="90"
                  cy="90"
                  r="70"
                  pathLength="100"
                  strokeDasharray={`${portion} ${100 - portion}`}
                  strokeDashoffset={-start}
                  transform="rotate(-90 90 90)"
                />
              ) : null;
            })}
          </svg>
          <div className="donut-center overview-ring-center">
            <strong>{count(total)}</strong>
            <span>{music ? "已订阅歌单" : "订阅来源"}</span>
          </div>
        </div>

        <div className="sub-stat-grid overview-subscription-legend">
          {facts.map((g) => (
            <div
              className={`sub-stat-box overview-legend-row overview-tone-${g.tone}`}
              key={g.label}
            >
              <div className="s-label">
                <span className="overview-dot" aria-hidden="true" />
                <span>{g.label}</span>
              </div>
              <strong className="s-val">{count(g.value)}</strong>
            </div>
          ))}
        </div>
      </div>

      <div className="overview-card-footer">
        {total === 0 ? (
          <span className="muted">还没有{title}</span>
        ) : total == null ? (
          <span className="muted">尚未取得音乐摘要</span>
        ) : music ? (
          <>
            <span>
              歌单成员 <strong>{count(data?.members)}</strong>
            </span>
            <span>
              本地歌曲 <strong>{count(data?.files)}</strong>
            </span>
          </>
        ) : (
          <>
            <span>
              来源成员{" "}
              <strong>
                {count(subscriptions.reduce((n, s) => n + s.counts.members, 0))}
              </strong>
            </span>
            <span className="celadon-tag">按订阅规则持续更新</span>
          </>
        )}
      </div>
    </section>
  );
}
