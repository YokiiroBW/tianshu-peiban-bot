import { useEffect, useMemo, useState } from "react";
import { Activity, ArrowRight, RefreshCw, Search } from "lucide-react";
import { StatePanel } from "../../components/StatePanel";
import { useGraphRead } from "./api";
import {
  eventTone,
  formatTime,
  outcomeLabel,
  type SafeEvent,
  type Trace,
  type TraceIndex,
  type TraceWindow,
} from "./types";

const services: Record<string, string> = {
  platform: "消息入口 / Platform",
  companion: "陪伴 / Companion",
  memory: "记忆 / Memory",
  "memory-knowledge": "资料 / Knowledge",
  gateway: "模型 / Gateway",
};

function TraceDetail({
  correlation,
  window,
}: {
  correlation: string;
  window: TraceWindow;
}) {
  const query = useGraphRead<Trace>("trace", {
    correlation_id: correlation,
    ...window,
  });
  const [stage, setStage] = useState("");
  const [selectedEvent, setSelectedEvent] = useState("");
  const data = query.data;
  const selected = data?.events.find(
    (event) => event.event_id === selectedEvent,
  );
  const stageEvents = data?.stages.find((item) => item.id === stage)?.event_ids;
  const lanes = useMemo(() => {
    const visibleEvents = (data?.events ?? []).filter(
      (event) => !stage || stageEvents?.includes(event.event_id),
    );
    return Object.entries(services).map(([id, label]) => ({
      id,
      label,
      events: visibleEvents.filter((event) => event.service === id),
    }));
  }, [data, stage, stageEvents]);
  useEffect(() => {
    setStage("");
    setSelectedEvent("");
  }, [correlation]);
  if (query.loading && !data)
    return (
      <StatePanel kind="loading" title="正在读取关联事件">
        按选定时间窗读取真实去敏记录。
      </StatePanel>
    );
  if (query.error)
    return (
      <StatePanel
        kind="error"
        title="关联事件读取失败"
        action={
          <button className="button" onClick={() => void query.reload()}>
            重试
          </button>
        }
      >
        {query.error}
      </StatePanel>
    );
  if (!data) return null;
  if (data.availability !== "ready")
    return (
      <StatePanel kind="unconfigured" title="运行日志查询未接入">
        部署端尚未连接只读日志查询；当前没有可用的事件证据。
      </StatePanel>
    );
  return (
    <div className="flow-trace-detail">
      <header className="flow-toolbar">
        <div>
          <h3>本次实际观测</h3>
          <code className="flow-correlation">{correlation}</code>
        </div>
        <button
          className="button"
          disabled={query.loading}
          onClick={() => void query.reload()}
        >
          <RefreshCw aria-hidden="true" />
          刷新本关联
        </button>
      </header>
      {!data.complete && (
        <p className="flow-warning" role="status">
          本次结果未完整覆盖时间窗，最多展示 1000
          条事件。缺少记录不能认定步骤未发生。
        </p>
      )}
      <section className="flow-stage-reference" aria-label="阶段观测图">
        <p>可涉及的业务阶段 · 虚线仅为阅读方向，不表示本次执行顺序</p>
        <div className="flow-stages">
          {data.stages.map((item, index) => {
            const events = data.events.filter((event) =>
              item.event_ids.includes(event.event_id),
            );
            const failures = events.some((event) =>
              ["failed", "rejected"].includes(event.outcome),
            );
            const tone = failures ? "red" : events.length ? "blue" : "gray";
            return (
              <div className="flow-stage-slot" key={item.id}>
                <button
                  type="button"
                  className={`flow-stage tone-${tone}`}
                  aria-pressed={stage === item.id}
                  onClick={() => {
                    setStage(stage === item.id ? "" : item.id);
                    setSelectedEvent("");
                  }}
                >
                  <strong>{item.label}</strong>
                  <span>
                    {failures
                      ? "有失败事件"
                      : events.length
                        ? `已观测 ${events.length} 条事件`
                        : "未观测"}
                  </span>
                </button>
                {index < data.stages.length - 1 && (
                  <ArrowRight aria-hidden="true" />
                )}
              </div>
            );
          })}
        </div>
      </section>
      <div className="flow-toolbar">
        <p>
          {data.events.length} 条关联事件 ·{" "}
          {stage ? "仅显示选定阶段" : "按服务查看全部事件"}
        </p>
        {stage && (
          <button className="button" onClick={() => setStage("")}>
            显示全部阶段
          </button>
        )}
      </div>
      <p className="flow-hint">
        同一关联号表示观测关联。各服务按记录时间排列，同一实例的序号可核对记录顺序；跨服务时钟与相邻卡片不能证明父子调用。成功事件也不等于整条业务完成。
      </p>
      {(stage === "schedule" || stage === "learning" || stage === "model") && (
        <p className="flow-hint">
          {stage === "model"
            ? "首块输出只证明输出已开始；每条上游结束事件只说明该次尝试的结果。"
            : stage === "learning"
              ? "异步处理及 outbox 回执不能单独证明全部记忆已落库。"
              : "此处观测主动投递及相关回执，不据此认定生活内容或日程已生成。"}
        </p>
      )}
      {!data.events.length ? (
        <StatePanel kind="empty" title="该时间窗未找到事件">
          可能尚未采集、已超出保留范围或相关号不在此时间窗。
        </StatePanel>
      ) : (
        <div
          className="flow-swimlane-viewport"
          tabIndex={0}
          aria-label="实际事件泳道，可横向滚动"
        >
          <div className="flow-swimlanes">
            {lanes.map((lane) => (
              <section
                className="flow-lane"
                key={lane.id}
                aria-label={lane.label}
              >
                <h4>
                  <Activity aria-hidden="true" />
                  {lane.label}
                  <small>{lane.events.length}</small>
                </h4>
                <div>
                  {lane.events.map((event) => (
                    <button
                      type="button"
                      className={`flow-event tone-${eventTone(event)}`}
                      key={event.event_id}
                      aria-pressed={selectedEvent === event.event_id}
                      onClick={() => setSelectedEvent(event.event_id)}
                    >
                      <time>
                        {new Date(event.timestamp).toLocaleTimeString("zh-CN", {
                          hour12: false,
                          fractionalSecondDigits: 3,
                        })}
                      </time>
                      <strong>{event.event}</strong>
                      <span>
                        {outcomeLabel(event.outcome)} · #{event.sequence}
                      </span>
                      {event.duration_ms !== null && (
                        <span>
                          该事件耗时 {event.duration_ms.toLocaleString()} ms
                        </span>
                      )}
                      {event.error_code && <code>{event.error_code}</code>}
                      <small>实例 {event.instance_id.slice(0, 8)}</small>
                    </button>
                  ))}
                  {!lane.events.length && (
                    <p className="flow-empty-column">
                      未观测到{stage ? "该阶段的" : "此服务的"}事件
                    </p>
                  )}
                </div>
              </section>
            ))}
          </div>
        </div>
      )}
      {selected && <EventDetail event={selected} />}
    </div>
  );
}

function EventDetail({ event }: { event: SafeEvent }) {
  return (
    <section className="flow-details" aria-label="事件证据">
      <h3>事件证据</h3>
      <dl className="flow-evidence">
        <div>
          <dt>事件</dt>
          <dd>{event.event}</dd>
        </div>
        <div>
          <dt>服务</dt>
          <dd>{event.service}</dd>
        </div>
        <div>
          <dt>发生时间</dt>
          <dd>{formatTime(event.timestamp)}</dd>
        </div>
        <div>
          <dt>实例 / 序号</dt>
          <dd>
            <code>{event.instance_id}</code> / {event.sequence}
          </dd>
        </div>
        <div>
          <dt>事件 ID</dt>
          <dd>
            <code>{event.event_id}</code>
          </dd>
        </div>
        <div>
          <dt>结果</dt>
          <dd>{outcomeLabel(event.outcome)}</dd>
        </div>
        <div>
          <dt>耗时</dt>
          <dd>
            {event.duration_ms === null
              ? "未记录"
              : `${event.duration_ms} ms（此事件记录值）`}
          </dd>
        </div>
        <div>
          <dt>错误码</dt>
          <dd>{event.error_code ?? "无"}</dd>
        </div>
      </dl>
    </section>
  );
}

export default function RuntimeView() {
  const [minutes, setMinutes] = useState(15);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<{
    correlation: string;
    window: TraceWindow;
  } | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [associationKind, setAssociationKind] = useState("all");
  const query = useGraphRead<TraceIndex>("traces", { minutes });
  useEffect(() => {
    if (!autoRefresh) return;
    const timer = window.setInterval(() => {
      if (!document.hidden) void query.reload();
    }, 15000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, query.reload]);
  const data = query.data;
  const traces =
    data?.traces.filter(
      (trace) =>
        trace.correlation_id.includes(search.trim()) &&
        (associationKind === "all" ||
          (associationKind === "business"
            ? trace.business_observed
            : !trace.business_observed)),
    ) ?? [];
  const validSearch = /^[a-f0-9]{32}$/.test(search.trim());
  return (
    <div className="flow-runtime">
      <div className="flow-filters">
        <label>
          关联类型
          <select
            aria-label="关联类型"
            value={associationKind}
            onChange={(event) => setAssociationKind(event.target.value)}
          >
            <option value="all">全部关联</option>
            <option value="business">有明确业务阶段</option>
            <option value="service">服务观测关联</option>
          </select>
        </label>
        <label>
          查询时间窗
          <select
            aria-label="查询时间窗"
            value={minutes}
            onChange={(event) => {
              setMinutes(Number(event.target.value));
              setSelected(null);
            }}
          >
            <option value={5}>最近 5 分钟</option>
            <option value={15}>最近 15 分钟</option>
            <option value={60}>最近 1 小时</option>
          </select>
        </label>
        <label className="flow-search">
          <Search aria-hidden="true" />
          <input
            aria-label="关联号"
            placeholder="筛选或输入完整 correlation ID"
            maxLength={32}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <button
          className="button"
          disabled={!validSearch || !data || data.availability !== "ready"}
          onClick={() =>
            data &&
            setSelected({ correlation: search.trim(), window: data.window })
          }
        >
          查询关联号
        </button>
        <button
          className="button"
          disabled={query.loading}
          onClick={() => void query.reload()}
        >
          <RefreshCw aria-hidden="true" />
          刷新列表
        </button>
        <label className="flow-checkbox">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(event) => setAutoRefresh(event.target.checked)}
          />
          每 15 秒刷新列表
        </label>
      </div>
      {query.loading && !data ? (
        <StatePanel kind="loading" title="正在查询运行日志">
          读取固定业务来源的去敏事件。
        </StatePanel>
      ) : query.error ? (
        <StatePanel
          kind="error"
          title="运行日志查询失败"
          action={
            <button className="button" onClick={() => void query.reload()}>
              重试
            </button>
          }
        >
          {query.error}
        </StatePanel>
      ) : data?.availability === "not_configured" ? (
        <StatePanel kind="unconfigured" title="运行日志查询未接入">
          部署端尚未连接只读日志查询。配置关系仍可查看，实际运行路径没有可用证据。
        </StatePanel>
      ) : (
        data && (
          <>
            <div className="flow-toolbar">
              <p>
                本时间窗已读取 {data.event_count} 条事件 · {data.traces.length}{" "}
                个已列明关联
              </p>
              <small>列表上限 30 个关联</small>
            </div>
            {!data.complete && (
              <p className="flow-warning" role="status">
                列表为有界查询结果，未完整覆盖时间窗。可输入完整关联号单独查询；不以缺少记录判断业务未执行。
              </p>
            )}
            <div className="flow-runtime-layout">
              <aside className="flow-trace-list" aria-label="运行关联列表">
                {traces.map((trace) => (
                  <button
                    type="button"
                    className={`flow-trace-summary ${trace.has_failure ? "tone-red" : trace.has_unknown ? "tone-yellow" : ""}`}
                    aria-pressed={
                      selected?.correlation === trace.correlation_id
                    }
                    key={trace.correlation_id}
                    onClick={() =>
                      setSelected({
                        correlation: trace.correlation_id,
                        window: data.window,
                      })
                    }
                  >
                    <strong>
                      {trace.has_failure
                        ? "有失败事件"
                        : trace.has_unknown
                          ? "有未知结果"
                          : "已观测事件"}
                    </strong>
                    <span className="flow-tag">
                      {trace.business_observed
                        ? "有明确业务阶段"
                        : "服务观测关联"}
                    </span>
                    <code>{trace.correlation_id}</code>
                    <span>
                      {trace.event_count} 条 · {trace.services.join(" / ")}
                    </span>
                    <time>{formatTime(trace.last_at)}</time>
                  </button>
                ))}
                {!traces.length && (
                  <p className="flow-empty-column">
                    {search
                      ? "列表中没有匹配关联号，可输入完整关联号查询。"
                      : "当前时间窗没有关联记录。"}
                  </p>
                )}
              </aside>
              <div className="flow-runtime-main">
                {selected ? (
                  <TraceDetail
                    key={`${selected.correlation}:${selected.window.end_ns}`}
                    correlation={selected.correlation}
                    window={selected.window}
                  />
                ) : (
                  <StatePanel kind="empty" title="选择一个运行关联">
                    查看实际事件、队列记录、已有耗时和失败位置。异步记忆、生活及日程可有独立关联号。
                  </StatePanel>
                )}
              </div>
            </div>
          </>
        )
      )}
    </div>
  );
}
