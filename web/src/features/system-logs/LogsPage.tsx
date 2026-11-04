import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, apiRead } from "../../api/client";
import { GLOBAL_SCOPE } from "../../api/useDomainResource";
import { useAuth } from "../../app/Auth";
import { appendEntries, entriesOf, formatTimestamp, localInput, ns, type LogEntry, type LogQuery, type LogResponse } from "./logs";
import "./logs.css";

const MAX_ROWS = 3000;
const ROW_HEIGHT = 54;
const VIEW_HEIGHT = 486;

function initialQuery(): LogQuery {
  const now = Date.now();
  return {
    start_ns: String(BigInt(now - 3600000) * 1000000n),
    end_ns: String(BigInt(now) * 1000000n),
    role: null, level: null, module: null, correlation_id: null, trace_id: null,
    job_id: null, attempt_id: null, keyword: null, exclude_conversation: true, limit: 200,
  };
}
function value(form: FormData, name: string): string | null {
  return String(form.get(name) ?? "").trim() || null;
}
function queryFrom(form: FormData): LogQuery {
  return {
    start_ns: ns(String(form.get("start"))), end_ns: ns(String(form.get("end"))),
    role: value(form, "role"), level: value(form, "level"), module: value(form, "module"),
    correlation_id: value(form, "correlation_id"), trace_id: value(form, "trace_id"),
    job_id: value(form, "job_id"), attempt_id: value(form, "attempt_id"),
    keyword: value(form, "keyword"), exclude_conversation: form.has("exclude_conversation"), limit: 200,
  };
}
function logError(cause: unknown): string {
  if (cause instanceof ApiError) {
    if (cause.code === "log_query_not_configured") return "日志查询尚未配置。";
    if (cause.code === "connection_failed") return "连接中断，保留上次查询位置。";
    if (cause.code === "log_query_unavailable") return "日志查询服务暂不可用，保留上次查询位置。";
    return cause.message;
  }
  return cause instanceof Error ? cause.message : "日志读取失败。";
}
function printable(value: unknown): string {
  if (typeof value === "string") return value;
  if (value == null) return "未记录";
  return JSON.stringify(value, null, 2) ?? "未记录";
}
function redact(value: unknown, key = ""): unknown {
  if (/password|secret|token|authorization|cookie|credential|api[_-]?key|private[_-]?key/i.test(key)) return "[已隐藏]";
  if (Array.isArray(value)) return value.map((item) => redact(item));
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([name, item]) => [name, redact(item, name)]));
  if (typeof value === "string") return value
    .replace(/\bBearer\s+[^\s,;]+/gi, "Bearer [已隐藏]")
    .replace(/((?:password|secret|token|api[_-]?key)\s*[=:]\s*)[^\s,;]+/gi, "$1[已隐藏]");
  return value;
}

export default function LogsPage() {
  const { session } = useAuth();
  const csrf = session?.authenticated ? session.csrf : "";
  const [query, setQuery] = useState(initialQuery);
  const [follow, setFollow] = useState(false);
  const [rows, setRows] = useState<LogEntry[]>([]);
  const [selected, setSelected] = useState<LogEntry | null>(null);
  const [response, setResponse] = useState<LogResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [formError, setFormError] = useState("");
  const [scrollTop, setScrollTop] = useState(0);
  const [trimmed, setTrimmed] = useState(false);
  const rowsRef = useRef<LogEntry[]>([]);
  const generation = useRef(0);
  const current = useRef<AbortController | null>(null);
  const view = useRef<HTMLDivElement>(null);

  const load = useCallback(async (snapshot: LogQuery, cursor: string | null, append: boolean) => {
    if (!csrf) return;
    current.current?.abort();
    const controller = new AbortController();
    current.current = controller;
    const mark = ++generation.current;
    setLoading(true);
    setError("");
    try {
      const data = await apiRead<LogResponse>("system/logs", { ...GLOBAL_SCOPE, ...snapshot, cursor }, csrf, controller.signal);
      if (controller.signal.aborted || mark !== generation.current) return;
      const incoming = entriesOf(data, cursor ?? `snapshot:${snapshot.end_ns}`);
      const merged = append ? appendEntries(rowsRef.current, incoming) : incoming;
      rowsRef.current = merged.slice(-MAX_ROWS);
      setRows(rowsRef.current);
      setTrimmed((previous) => append ? previous || merged.length > MAX_ROWS : merged.length > MAX_ROWS);
      setResponse(data);
      if (!append) { setSelected(null); setScrollTop(0); if (view.current) view.current.scrollTop = 0; }
    } catch (cause) {
      if (!controller.signal.aborted && mark === generation.current) {
        setError(logError(cause));
        if (cause instanceof ApiError && [401, 403].includes(cause.status)) { rowsRef.current = []; setRows([]); setSelected(null); setResponse(null); }
      }
    } finally {
      if (!controller.signal.aborted && mark === generation.current) setLoading(false);
    }
  }, [csrf]);
  useEffect(() => {
    void load(query, null, false);
    return () => { ++generation.current; current.current?.abort(); };
  }, [load, query]);
  useEffect(() => {
    if (!follow) return;
    const timer = window.setInterval(() => {
      if (document.hidden) return;
      setQuery((previous) => {
        const duration = BigInt(previous.end_ns) - BigInt(previous.start_ns);
        const end = BigInt(Date.now()) * 1000000n;
        return { ...previous, start_ns: String(end - duration), end_ns: String(end) };
      });
    }, 5000);
    return () => clearInterval(timer);
  }, [follow]);

  const first = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - 5);
  const last = Math.min(rows.length, first + Math.ceil(VIEW_HEIGHT / ROW_HEIGHT) + 10);
  const exportableRows = rows.filter((row) => row.origin === "entries");
  const exportRows = () => {
    const payload = exportableRows.map((row) => redact(row.fields));
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url; link.download = "tianshu-logs.json"; link.click();
    URL.revokeObjectURL(url);
  };
  const linkId = (entry: LogEntry, field: string) => typeof entry.fields[field] === "string" || typeof entry.fields[field] === "number" ? String(entry.fields[field]) : "";
  return (
    <section className="panel system-logs">
      <div className="section-heading"><div><h2>运行日志</h2><p>查询实际记录与采集覆盖，业务结果以原任务回执为准。</p></div></div>
      <form className="domain-form" onSubmit={(event) => {
        event.preventDefault();
        try {
          const next = queryFrom(new FormData(event.currentTarget));
          const span = BigInt(next.end_ns) - BigInt(next.start_ns);
          if (span <= 0n || span > 86400000000000n) throw new Error("请选择不超过 24 小时的有效时间范围。");
          setFormError(""); setFollow(false); setQuery(next);
        } catch (cause) { setFormError(cause instanceof Error ? cause.message : "时间范围无效。"); }
      }}>
        <div className="log-filters">
          <label>开始时间<input name="start" type="datetime-local" required defaultValue={localInput(new Date(Date.now() - 3600000))} /></label>
          <label>结束时间<input name="end" type="datetime-local" required defaultValue={localInput(new Date())} /></label>
          <label>进程角色<input name="role" placeholder="全部" /></label>
          <label>级别<select name="level" defaultValue=""><option value="">全部</option>{["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"].map((level) => <option key={level}>{level}</option>)}</select></label>
          <label>模块<input name="module" /></label>
          <label>关联 ID<input name="correlation_id" /></label>
          <label>Trace ID<input name="trace_id" /></label>
          <label>任务 ID<input name="job_id" /></label>
          <label>Attempt ID<input name="attempt_id" /></label>
          <label>关键词<input name="keyword" /></label>
        </div>
        <label><input type="checkbox" name="exclude_conversation" defaultChecked /> 排除对话事件</label>
        <div className="domain-actions"><button className="button primary" disabled={loading}>查询</button><button type="button" className="button" onClick={() => setQuery({ ...query })} disabled={loading}>刷新快照</button><button type="button" className="button" onClick={() => setFollow(!follow)}>{follow ? "暂停跟随" : "自动跟随"}</button><button type="button" className="button" onClick={exportRows} disabled={!exportableRows.length}>导出已加载记录</button></div>
        {formError && <p role="alert">{formError}</p>}
      </form>
      <p className="muted">{follow ? "每 5 秒读取新的时间快照；暂停后可按当前快照继续翻页。" : "当前快照已固定；翻页保持原范围和筛选条件。"} 导出仅包含当前已加载的脱敏条目，不导出兼容流的原始行。</p>
      {error && <p role="alert">{error}</p>}
      {loading && <p role="status">正在读取日志…</p>}
      {response && <div className="log-coverage" role="status">
        <p>{response.coverage?.complete === true ? "已观测本地来源的持久化覆盖完整" : "已观测本地来源的持久化覆盖可能不完整"}{response.coverage?.missing?.length ? ` · ${response.coverage.missing.map(printable).join("、")}` : ""}</p>
        {response.coverage?.generated_coverage === "unknown" && <p>未能确认全部已生成记录的覆盖范围。</p>}
        {response.coverage?.collection_coverage === "unknown" && <p>采集和送达覆盖范围未知。</p>}
        <p>{response.coverage?.has_more ? "当前快照还有后续记录。" : response.coverage?.snapshot_complete === true ? "当前快照已读完。" : "当前快照的返回范围尚未确认完整。"} 采集状态与送达状态见下方详情。</p>
        {response.freshness && <details><summary>采集时点与状态</summary>
          <p>采样：{response.freshness.as_of ?? "未记录"} · 快照：{response.freshness.snapshot_at ?? "未记录"} · 有效至：{response.freshness.snapshot_expires_at ?? "未记录"}</p>
          <p>迟到补传须刷新新快照后查看。</p>
          {response.freshness.writers?.map((writer) => <p key={writer.instance_id}>{writer.role} · {writer.instance_id}：已生成序号 {writer.generated_sequence ?? "未知"}，已持久化序号 {writer.persisted_sequence ?? "未知"}，待处理 {writer.pending ?? "未知"}，缺口 {writer.missing_count ?? "未知"}；{writer.clean_shutdown === true ? "已正常退出" : writer.clean_shutdown === false ? "未记录正常退出" : "退出状态未知"}。</p>)}
          {response.freshness.collector && <p>采集器：{response.freshness.collector.status} · 已采集序号 {response.freshness.collector.collected_sequence ?? "未知"} · 已送达序号 {response.freshness.collector.delivered_sequence ?? "未知"}{response.freshness.collector.reason ? ` · ${response.freshness.collector.reason}` : ""}</p>}
          {response.freshness.loki && <p>日志查询后端：{response.freshness.loki.status}</p>}
          {response.freshness.disk && <p>日志盘可用 {response.freshness.disk.free_bytes ?? "未知"} / 总计 {response.freshness.disk.total_bytes ?? "未知"} 字节。</p>}
          <pre>{printable(response.freshness)}</pre>
        </details>}
        {response.coverage && <details><summary>覆盖详情</summary><pre>{printable(response.coverage)}</pre></details>}
      </div>}
      {trimmed && <p>页面只保留最近加载的 3000 条；可重新查询其他范围。</p>}
      {response && !rows.length && !error && <p>当前快照没有匹配日志。</p>}
      {!!rows.length && <div className="log-viewport" ref={view} onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)} aria-label="日志记录列表">
        <div style={{ height: rows.length * ROW_HEIGHT, position: "relative" }}>
          <div style={{ position: "absolute", top: first * ROW_HEIGHT, left: 0, right: 0 }}>
            {rows.slice(first, last).map((row) => <button type="button" className="log-row" key={row.key} onClick={() => setSelected(row)} aria-pressed={selected?.key === row.key}>
              <time>{formatTimestamp(row.timestamp)}</time><strong>{row.level}</strong><span>{row.role} · {row.module}</span><span>{row.message}{row.fields.persistence === "unverified" ? " · 落盘未确认" : ""}</span>
            </button>)}
          </div>
        </div>
      </div>}
      {response?.next_cursor && <button className="button" disabled={loading} onClick={() => { setFollow(false); void load(query, response.next_cursor!, true); }}>加载下一页</button>}
      {response && !response.next_cursor && response.coverage?.snapshot_complete !== true && <p className="muted">服务没有提供续页游标；不能把当前快照当作完整范围。</p>}
      {selected && <section className="log-detail" aria-label="日志详情"><h3>日志详情</h3>
        <div className="domain-actions">
          {linkId(selected, "job_id") && <a href={`#/system/tasks?job_id=${encodeURIComponent(linkId(selected, "job_id"))}`}>查看原任务</a>}
          {linkId(selected, "attempt_id") && <a href={`#/system/tasks?attempt=${encodeURIComponent(linkId(selected, "attempt_id"))}`}>查看模型尝试</a>}
          {linkId(selected, "incident_id") && <a href={`#/system/operations?incident_id=${encodeURIComponent(linkId(selected, "incident_id"))}`}>查看运维事件</a>}
        </div>
        <pre>{JSON.stringify(redact(selected.fields), null, 2)}</pre>
      </section>}
    </section>
  );
}
