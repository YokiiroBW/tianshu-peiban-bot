import { ArrowRight, Cpu, Database, Network, Activity } from "lucide-react";
import { StatusRail } from "../../components/StatusRail";
import { ReasonNote } from "./ReasonNote";
import type { MediaView } from "./types";
import { bytes, mediaEngineState, uptime } from "./overviewPresentation";
import { timestamp, tone, word } from "./wording";

export function RuntimeCards({
  view,
  details = false,
}: {
  view: MediaView;
  details?: boolean;
}) {
  const runtime = view.overview?.runtime;
  const db = view.overview?.database;
  const databaseEngine = db?.engine || "引擎尚未取得";
  const sizeScope =
    db?.size_scope === "schema"
      ? `媒体 schema${db.size_scope_name ? `（${db.size_scope_name}）` : ""}的表、索引与 TOAST`
      : ((
          { database: "整个数据库", main_file: "数据库主文件" } as Record<
            string,
            string
          >
        )[db?.size_scope ?? ""] ?? "统计范围尚未取得");
  return (
    <>
      <section className="media-card overview-system" aria-label="系统状态">
        <div className="media-section-head">
          <h2>
            <Cpu aria-hidden="true" />
            系统状态
          </h2>
          <a href="#/media/runtime" aria-label="查看运行状态详情">
            <ArrowRight aria-hidden="true" />
          </a>
        </div>
        <div className="overview-primary-value">
          {runtime?.cpu_percent == null ? "—" : `${runtime.cpu_percent}%`}
          <span>CPU · 天枢进程</span>
        </div>
        <dl className="overview-facts">
          <div>
            <dt>进程内存</dt>
            <dd>{bytes(runtime?.memory_bytes)}</dd>
          </div>
          <div>
            <dt>已运行</dt>
            <dd>{uptime(runtime?.uptime_seconds)}</dd>
          </div>
        </dl>
        {details && (
          <p className="muted">
            CPU 以一个核心的 100%
            为单位，内存为天枢进程的驻留内存；首次采样后计算 CPU 差值。不是 NAS
            全机负载。
          </p>
        )}
      </section>
      <section className="media-card overview-database" aria-label="数据库状态">
        <div className="media-section-head">
          <h2>
            <Database aria-hidden="true" />
            数据库
          </h2>
          <StatusRail
            tone={db?.state === "ready" ? "blue" : "gray"}
            label={db?.state === "ready" ? "可读取" : "未取得"}
          />
        </div>
        <div className="overview-primary-value">
          {bytes(db?.size_bytes)}
          <span>媒体数据库 · {databaseEngine}</span>
        </div>
        <dl className="overview-facts">
          <div>
            <dt>摘要查询</dt>
            <dd>{db ? `${db.query_ms} ms` : "—"}</dd>
          </div>
          <div>
            <dt>结构版本</dt>
            <dd>{db ? `v${db.schema_version}` : "—"}</dd>
          </div>
          <div>
            <dt>大小统计范围</dt>
            <dd>{sizeScope}</dd>
          </div>
        </dl>
        {details && (
          <p className="muted">
            统计来自服务器返回的 {databaseEngine} 观测，大小范围为{sizeScope}
            。查询耗时包含服务处理和数据库等待，不代表浏览器网络延迟。
          </p>
        )}
      </section>
      <section className="media-card overview-network" aria-label="网络速率">
        <h2>
          <Network aria-hidden="true" />
          网络速率
        </h2>
        <dl className="overview-network-rates">
          <div>
            <dt>↓ 接收</dt>
            <dd>
              {runtime?.receive_bytes_per_second == null
                ? "—"
                : `${bytes(runtime.receive_bytes_per_second)}/s`}
            </dd>
          </div>
          <div>
            <dt>↑ 发送</dt>
            <dd>
              {runtime?.send_bytes_per_second == null
                ? "—"
                : `${bytes(runtime.send_bytes_per_second)}/s`}
            </dd>
          </div>
        </dl>
        <p className="overview-note muted">天枢容器 · 相邻采样平均速率</p>
        {details && (
          <p className="muted">
            采样当前容器非回环网络接口，包含该容器其他请求。首次采样、计数重置或环境不提供读数时显示
            —。
          </p>
        )}
      </section>
    </>
  );
}

export function LatencyCard({
  responseMs,
  stale,
  details = false,
}: {
  responseMs: number | null;
  stale: boolean;
  details?: boolean;
}) {
  return (
    <section className="media-card overview-latency" aria-label="网络连接延迟">
      <div className="media-section-head">
        <h2>
          <Activity aria-hidden="true" />
          连接延迟
        </h2>
        <a href="#/media/runtime" aria-label="查看连接测量详情">
          <ArrowRight aria-hidden="true" />
        </a>
      </div>
      <div className="overview-latency-reading">
        <span
          className={`overview-dot overview-tone-${stale ? "red" : "blue"}`}
          aria-hidden="true"
        />
        <span>浏览器 ↔ 天枢</span>
        <strong>
          {stale ? "连接中断" : responseMs == null ? "—" : `${responseMs} ms`}
        </strong>
      </div>
      <p className="overview-note muted">状态请求往返，包含服务处理时间</p>
      {details && (
        <p className="muted">
          每次成功刷新测量一次，不对音乐平台、媒体服务器发送额外探测；这里的数值不代表这些渠道的延迟。
        </p>
      )}
    </section>
  );
}

export function RuntimePanel({
  view,
  responseMs,
  stale,
}: {
  view: MediaView;
  responseMs: number | null;
  stale: boolean;
}) {
  return (
    <section
      className="media-stack media-runtime-details"
      aria-label="运行状态详情"
    >
      <div className="media-section-head">
        <h2>运行状态</h2>
        <a className="overview-text-link" href="#/media">
          返回概览
        </a>
      </div>
      <div className="overview-details-grid">
        <RuntimeCards view={view} details />
        <LatencyCard responseMs={responseMs} stale={stale} details />
      </div>
      <section className="media-card" aria-label="下载引擎详情">
        <h2>下载引擎</h2>
        <StatusRail
          tone={tone(mediaEngineState(view.engine))}
          label={word(mediaEngineState(view.engine))}
        />
        <ReasonNote code={view.engine.code} />
        {(view.engine.stale ||
          view.engine.owner_live === false ||
          !view.engine.observed_at) && (
          <p role="status">
            {view.engine.observed_at
              ? "工作进程观测已过期或原所有者已失活，当前运行状态尚未确认。"
              : "尚未取得工作进程的持久观测。"}
          </p>
        )}
        {view.background?.length ? (
          <ul>
            {view.background.map((row) => (
              <li key={row.name}>
                {row.name} ·{" "}
                {row.owner_live && row.observed_at
                  ? ((
                      {
                        running: "正在运行",
                        idle: "等待下一次任务",
                        waiting: "等待下一次任务",
                        stopped: "已停止",
                        failed: "本次任务未完成",
                        unknown: "尚未确认",
                      } as Record<string, string>
                    )[row.state] ?? "尚未确认")
                  : "工作进程状态未知"}
                <small>
                  {" "}
                  · 最近观测 {timestamp(row.observed_at)}
                  {row.next_due_at
                    ? ` · 下次时间 ${timestamp(row.next_due_at)}`
                    : ""}
                </small>
                {row.code && <ReasonNote code={row.code} />}
              </li>
            ))}
          </ul>
        ) : (
          <p>尚未取得后台任务观测。</p>
        )}
        <p className="muted">
          引擎版本：{view.engine.version || "尚未取得"} · 工作进程最近观测：
          {timestamp(view.engine.observed_at)}
        </p>
        <a className="overview-text-link" href="#/media/accounts">
          管理视频账号与媒体库
          <ArrowRight aria-hidden="true" />
        </a>
      </section>
    </section>
  );
}
