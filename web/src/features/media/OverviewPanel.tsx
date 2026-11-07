import { useState } from "react";
import {
  ArrowRight,
  CheckCheck,
  ListVideo,
  LoaderCircle,
  TriangleAlert,
} from "lucide-react";
import { StatusRail } from "../../components/StatusRail";
import { attentionJobsHref } from "./jobsNavigation";
import type { MediaView } from "./types";
import { timestamp, tone, word } from "./wording";
import { count } from "./overviewPresentation";
import { OverviewSubscriptions } from "./OverviewSubscriptions";
import { OverviewStorage } from "./OverviewStorage";
import { BookOverview } from "./books/BookOverview";
import { RuntimeCards, LatencyCard } from "./RuntimePanel";
import "./overview.css";

const metrics = [
  {
    key: "published",
    label: "已完成",
    seal: "成",
    sub: "已归档入库任务",
    icon: CheckCheck,
    tone: "blue",
  },
  {
    key: "processing",
    label: "处理中",
    seal: "行",
    sub: "当前活跃传输流",
    icon: LoaderCircle,
    tone: "blue",
  },
  {
    key: "queued",
    label: "等待队列",
    seal: "候",
    sub: "排期等待拉取",
    icon: ListVideo,
    tone: "yellow",
  },
  {
    key: "attention",
    label: "需要处理",
    seal: "警",
    sub: "需人工核验重试",
    icon: TriangleAlert,
    tone: "red",
  },
] as const;

export function OverviewPanel({
  view,
  responseMs,
  stale,
}: {
  view: MediaView;
  responseMs: number | null;
  stale: boolean;
}) {
  const [medium, setMedium] = useState<"video" | "audio" | "book">(
    () =>
      (["video", "audio", "book"] as const).find(
        (kind) =>
          (view.overview?.recent?.[kind]?.length ??
            view.jobs.filter((job) => (job.media_kind ?? "video") === kind)
              .length) > 0,
      ) ?? "video",
  );
  const counts = view.overview?.jobs;
  const available =
    counts &&
    [
      counts.total,
      counts.published,
      counts.processing,
      counts.queued,
      counts.attention,
      counts.cancelled,
    ].every((v) => Number.isSafeInteger(v) && v >= 0);
  const recent =
    view.overview?.recent?.[medium] ??
    view.jobs
      .filter((job) => (job.media_kind ?? "video") === medium)
      .slice(0, 5);
  return (
    <section className="media-overview" aria-label="订阅概览">
      {/* 一体咬合式四联任务 KPI 博古横格 */}
      <div
        className="curio-kpi-lattice overview-kpis"
        aria-label="全部下载任务统计"
      >
        {metrics.map((metric) => (
          <a
            className={`kpi-cell overview-kpi overview-tone-${metric.tone}`}
            href={
              metric.key === "attention" ? attentionJobsHref() : "#/media/jobs"
            }
            key={metric.key}
            aria-label={`${metric.label}，${available ? count(counts[metric.key]) + " 个任务" : "总量暂不可用"}，打开下载中心`}
          >
            <div>
              <div className="kpi-head">
                <span
                  className={`section-seal-icon${metric.tone === "red" ? " is-seal-alert" : ""}`}
                >
                  {metric.seal}
                </span>
                <span className="overview-kpi-label">{metric.label}</span>
              </div>
              <strong
                className={`kpi-num overview-kpi-value${metric.tone === "red" ? " is-seal-alert-text" : ""}`}
              >
                {available ? count(counts[metric.key]) : "—"}
              </strong>
              <div className="kpi-sub">{metric.sub}</div>
            </div>
            <ArrowRight className="overview-kpi-arrow" aria-hidden="true" />
          </a>
        ))}
      </div>

      {available ? (
        <div className="overview-total-banner">
          <p className="overview-total muted">
            累计 {count(counts.total)} 个任务 · 其中 {count(counts.cancelled)}{" "}
            个已取消
          </p>
          <a className="ink-link" href="#/media/jobs">
            打开下载中心 →
          </a>
        </div>
      ) : (
        <p className="overview-unavailable muted" role="status">
          任务总量暂不可用。仍可到下载中心查看已读取的任务。
        </p>
      )}

      {/* 三栏十卡博古列阵 */}
      <div className="media-dashboard-grid overview-columns">
        <aside
          className="media-col-stack overview-system-column"
          aria-label="系统与网络摘要"
        >
          <RuntimeCards view={view} />
        </aside>

        <div className="media-col-stack overview-main">
          <OverviewSubscriptions view={view} />
          <OverviewSubscriptions view={view} music />
          <BookOverview view={view} />
        </div>

        <aside
          className="media-col-stack overview-side"
          aria-label="存储与下载摘要"
        >
          <OverviewStorage view={view} />
          <LatencyCard responseMs={responseMs} stale={stale} />
          <section
            className="curio-card media-card overview-recent"
            aria-label="最近下载"
          >
            <div className="section-head media-section-head">
              <div className="section-title-group">
                <div className="section-seal-icon" aria-hidden="true">
                  录
                </div>
                <div>
                  <h2 className="section-title">最近任务</h2>
                  <div className="section-sub">下载、导入与曲库扫描</div>
                </div>
              </div>
              <a className="ink-link overview-text-link" href="#/media/jobs">
                全部
                <ArrowRight aria-hidden="true" />
              </a>
            </div>
            <div
              className="overview-recent-tabs workspace-tabs-bar"
              role="group"
              aria-label="最近下载类型"
            >
              {(["video", "audio", "book"] as const).map((kind) => (
                <button
                  key={kind}
                  className={`ws-tab ${medium === kind ? "is-selected active" : ""}`}
                  aria-pressed={medium === kind}
                  onClick={() => setMedium(kind)}
                >
                  {kind === "video"
                    ? "视频"
                    : kind === "audio"
                      ? "音乐"
                      : "书籍"}
                </button>
              ))}
            </div>
            {recent.length ? (
              <div className="overview-recent-list">
                {recent.map((job) => (
                  <a
                    className="overview-recent-row"
                    href="#/media/jobs"
                    key={job.job_id}
                    aria-label={`在下载中心查看任务：${job.title}`}
                  >
                    <div className="overview-recent-copy">
                      <h3 title={job.title}>{job.title}</h3>
                      <p className="muted">{timestamp(job.updated_at)}</p>
                    </div>
                    <StatusRail
                      tone={tone(job.state)}
                      label={word(job.state)}
                    />
                  </a>
                ))}
              </div>
            ) : (
              <div className="empty-box">
                <p className="overview-empty muted">
                  近期暂无
                  {medium === "video"
                    ? "视频"
                    : medium === "audio"
                      ? "音乐"
                      : "书籍"}
                  任务
                </p>
              </div>
            )}
          </section>
        </aside>
      </div>
    </section>
  );
}
