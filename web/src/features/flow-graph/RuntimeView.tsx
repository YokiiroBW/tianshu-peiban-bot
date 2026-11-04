import { useState } from "react";
import { GLOBAL_SCOPE, useDomainResource } from "../../api/useDomainResource";
import { RuntimeTasks } from "../settings/TasksPanel";

type LogView = {
  streams: { stream: Record<string, string>; values: [string, string][] }[];
  coverage: { complete: boolean; snapshot_complete?: boolean; has_more?: boolean; missing: string[] };
  next_end_ns: string | null;
  next_cursor?: string | null;
};
type Event = {
  key: string;
  stamp: string;
  role: string;
  body: Record<string, unknown>;
  line: string;
};
const roles = ["interactive", "model-broker", "bot-foreground", "bot-background", "independent", "operations"];
function localTime(value: Date) {
  return new Date(value.getTime() - value.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}
export default function RuntimeView() {
  const [range, setRange] = useState<{
    start_ns: string;
    end_ns: string;
  } | null>(null);
  const [filter, setFilter] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<Event | null>(null);
  const logs = useDomainResource<LogView>(
    "system/logs",
    { ...GLOBAL_SCOPE, ...range, cursor, limit: 300 },
    !!range,
  );
  const events = (logs.data?.streams ?? [])
    .flatMap((stream, streamIndex) =>
      stream.values.map(([stamp, line], index) => {
        let body: Record<string, unknown> = {};
        try {
          const parsed = JSON.parse(line);
          if (parsed && typeof parsed === "object" && !Array.isArray(parsed))
            body = parsed;
        } catch {
          /* The original line remains readable. */
        }
        return {
          key: `${streamIndex}:${stamp}:${index}`,
          stamp,
          line,
          body,
          role:
            typeof body.role === "string"
              ? body.role
              : (stream.stream.role ?? "unknown"),
        };
      }),
    )
    .sort((a, b) =>
      BigInt(a.stamp) < BigInt(b.stamp)
        ? -1
        : BigInt(a.stamp) > BigInt(b.stamp)
          ? 1
          : 0,
    );
  const visible = events.filter(
    (event) =>
      !filter ||
      [
        "trace_id",
        "request_id",
        "action_id",
        "attempt_id",
        "expression_id",
        "task_id",
      ].some((key) => event.body[key] === filter),
  );
  const lane = (role: string) => (
    <section key={role} className="flow-lane">
      <h4>
        {role}
        <small>
          {visible.filter((event) => event.role === role).length}条实际日志
        </small>
      </h4>
      <div>
        {visible
          .filter((event) => event.role === role)
          .map((event) => (
            <button
              key={event.key}
              className="flow-event"
              aria-pressed={selected?.key === event.key}
              onClick={() => setSelected(event)}
            >
              <time>
                {new Date(
                  Number(BigInt(event.stamp) / 1000000n),
                ).toLocaleTimeString("zh-CN")}
              </time>
              <strong>
                {typeof event.body.event === "string"
                  ? event.body.event
                  : typeof event.body.message === "string"
                    ? event.body.message
                    : "原始日志记录"}
              </strong>
              {typeof event.body.domain === "string" && (
                <span>{event.body.domain}</span>
              )}
              <small>{event.stamp} ns</small>
            </button>
          ))}
      </div>
    </section>
  );
  return (
    <div className="flow-runtime">
      <p>
        六类运行角色各有实际职责。泳道只列实际返回的日志，不补造缺失的请求阶段。
      </p>
      <form
        className="domain-form"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          setSelected(null);
          setCursor(null);
          setRange({
            start_ns: (
              BigInt(new Date(String(data.get("start"))).getTime()) * 1000000n
            ).toString(),
            end_ns: (
              BigInt(new Date(String(data.get("end"))).getTime()) * 1000000n
            ).toString(),
          });
        }}
      >
        <div className="domain-grid">
          <label>
            开始时间
            <input
              type="datetime-local"
              name="start"
              required
              defaultValue={localTime(new Date(Date.now() - 900000))}
            />
          </label>
          <label>
            结束时间
            <input
              type="datetime-local"
              name="end"
              required
              defaultValue={localTime(new Date())}
            />
          </label>
          <label>
            筛选已返回记录的原关联ID
            <input
              value={filter}
              onChange={(event) => setFilter(event.target.value.trim())}
              placeholder="trace / request / action / attempt / expression / task ID"
            />
          </label>
        </div>
        <button className="button" disabled={logs.loading}>
          读取实际运行范围
        </button>
      </form>
      {logs.error && <p role="alert">{logs.error}</p>}
      {logs.data && (
        <>
          <p>
            {logs.data.coverage.complete
              ? "已观测本地来源的持久化覆盖完整"
              : "已观测本地来源的持久化可能有缺口"}{" "}
            · {logs.data.coverage.missing.join("、") || "没有声明缺口"} ·{" "}
            {visible.length}条匹配记录
          </p>
          <div className="flow-swimlane-viewport">
            <div className="flow-swimlanes">{roles.map(lane)}</div>
          </div>
          {visible.some((event) => !roles.includes(event.role)) && (
            <details>
              <summary>未归属当前六角色的真实记录</summary>
              {visible
                .filter((event) => !roles.includes(event.role))
                .map((event) => (
                  <pre key={event.key}>{event.line}</pre>
                ))}
            </details>
          )}
          {logs.data.next_cursor && range && (
            <button
              className="button"
              onClick={() => {
                setSelected(null);
                setCursor(logs.data!.next_cursor!);
              }}
            >
              读取下一页
            </button>
          )}
          {!logs.data.next_cursor && logs.data.coverage.snapshot_complete !== true && (
            <p>当前查询未提供续页游标，不能确认范围完整。请查看<a href="#/system/logs">运行日志</a>的采集覆盖。</p>
          )}
        </>
      )}
      {selected && (
        <section className="flow-details">
          <h3>原日志证据 · {selected.role}</h3>
          <pre>{selected.line}</pre>
          <p>
            原时间戳 {selected.stamp} ns；没有记录的耗时、效果与阶段保持未知。
          </p>
        </section>
      )}
      <RuntimeTasks includeLogs={false} />
    </div>
  );
}
