import { useState } from "react";
import { GLOBAL_SCOPE, useDomainResource } from "../../api/useDomainResource";
import "./statistics.css";

type Metric = { total: number | null; samples: number; unknown: number };
type Metrics = Record<string, Metric>;
type Statistics = {
  status?: "available" | "unavailable";
  range: { start: string; end: string; timezone: string; resolution: string };
  overview: Metrics;
  cumulative: Metrics;
  series: { time: string; metrics: Metrics }[];
  breakdowns: Record<string, unknown>;
  resources: unknown;
  recovery_rate?: { eligible: number; evaluated: number; verified: number; rate: number | null } | null;
  freshness?: { source_watermarks: unknown; source_last_seen: unknown } | null;
  coverage: { complete: boolean; missing?: unknown[]; incomplete?: unknown; producers?: unknown; percentiles?: string; [key: string]: unknown };
  as_of: string | null;
};

const groups: { title: string; names: [string, string][] }[] = [
  { title: "消息与投递", names: [["received", "已接收"], ["processed", "已处理"], ["reply_required", "需要回复"], ["delivered", "已投递"], ["withdrawn", "已撤回"]] },
  { title: "模型调用与用量", names: [["physical_attempts", "实际模型调用"], ["business_success", "业务成功"], ["input_tokens", "实际输入 Token"], ["output_tokens", "实际输出 Token"], ["estimated_input_tokens", "估算输入 Token"], ["estimated_output_tokens", "估算输出 Token"], ["cached_input_tokens", "输入中的缓存 Token"], ["cache_write_tokens", "缓存写入 Token"], ["reasoning_tokens", "输出中的推理 Token"], ["actual_cost", "实际费用"], ["estimated_cost", "估算费用"]] },
  { title: "记忆、成长与生活", names: [["memory_applied", "记忆已应用"], ["memory_failed", "记忆未完成"], ["growth_material_blocked", "成长材料阻塞"], ["growth_review_blocked", "成长复核阻塞"], ["life_scheduled", "日程已安排"], ["life_intentional_idle", "主动留白"], ["life_deferred_no_slot", "暂时排不下"], ["life_stale_state", "依据已变化"], ["life_invalid_proposal", "无效日程提议"], ["uncovered_material_age_seconds", "未覆盖材料年龄合计（秒）"]] },
  { title: "独立任务与恢复", names: [["media_completed", "媒体任务完成"], ["media_failed", "媒体任务失败"], ["recovery_eligible", "符合恢复统计条件"], ["recovery_verified", "恢复后已验证"], ["recovery_failed", "恢复失败"]] },
  { title: "耗时分段（毫秒合计）", names: [["queue_ms", "排队"], ["execution_ms", "执行"], ["ttft_ms", "首个有效输出"], ["pool_wait_ms", "连接池等待"], ["dns_ms", "DNS"], ["connect_ms", "建立连接"], ["send_ms", "发送"], ["response_wait_ms", "等待响应"], ["read_ms", "读取响应"], ["effective_output_ms", "有效输出"], ["http_total_ms", "HTTP 总耗时"]] },
];
const names = Object.fromEntries(groups.flatMap((group) => group.names));
const chartOptions = ["physical_attempts", "input_tokens", "output_tokens", "received", "delivered", "memory_applied", "life_scheduled", "life_intentional_idle", "life_deferred_no_slot", "media_completed", "execution_ms", "response_wait_ms"];

function label(key: string): string { return names[key] ?? key; }
function display(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? "未知" : value.toLocaleString("zh-CN", { maximumFractionDigits: 4 });
}
function time(value: string | null | undefined, timezone: string): string {
  if (!value || Number.isNaN(Date.parse(value))) return value ?? "未记录";
  try { return new Date(value).toLocaleString("zh-CN", { timeZone: timezone, hour12: false }); }
  catch { return value; }
}
function makeRange(days: number, timezone: string, module: string, actor: string, route: string) {
  const now = Date.now();
  return { start: new Date(now - days * 86400000).toISOString(), end: new Date(now).toISOString(), timezone,
    module: module.trim() || null, actor_id: actor.trim() || null, route_id: route.trim() || null };
}

function Cards({ values }: { values: Metrics }) {
  return <div>{groups.map((group) => {
    const available = group.names.filter(([key]) => values[key] !== undefined);
    if (!available.length) return null;
    return <section key={group.title}><h3>{group.title}</h3><div className="statistics-cards">
      {available.map(([key, title]) => {
        const metric = values[key];
        return <article className="statistics-card" key={key}><span>{title}</span><strong>{display(metric.total)}</strong><small>样本 {display(metric.samples)} · 未知 {display(metric.unknown)}</small></article>;
      })}
    </div></section>;
  })}</div>;
}

function Trend({ series, metric, timezone }: { series: Statistics["series"]; metric: string; timezone: string }) {
  const data = series.map((point) => ({ time: point.time, metric: point.metrics?.[metric] }));
  const known = data.flatMap((point) => point.metric?.total == null ? [] : [point.metric.total]);
  const max = Math.max(0, ...known);
  const width = 720, height = 170;
  const positions = data.map((point, index) => ({ ...point, x: data.length > 1 ? 30 + index * (width - 60) / (data.length - 1) : width / 2,
    y: point.metric?.total == null ? null : height - 20 - (max > 0 ? point.metric.total / max * (height - 40) : 0) }));
  const segments: string[] = [];
  let segment: string[] = [];
  for (const point of positions) {
    if (point.y == null) { if (segment.length) segments.push(segment.join(" ")); segment = []; }
    else segment.push(`${point.x},${point.y}`);
  }
  if (segment.length) segments.push(segment.join(" "));
  return <div>
    {!known.length ? <p>所选时间范围没有可用样本。</p> : <svg className="statistics-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${label(metric)}的时间趋势；缺失样本处不连线`}>
      <line x1="30" y1={height - 20} x2={width - 30} y2={height - 20} stroke="currentColor" opacity="0.3" />
      {segments.map((points, index) => <polyline key={index} fill="none" stroke="currentColor" strokeWidth="3" points={points} />)}
      {positions.filter((point) => point.y != null).map((point, index) => <circle key={index} cx={point.x} cy={point.y!} r="3" fill="currentColor" />)}
    </svg>}
    <div className="table-scroll"><table><thead><tr><th>时间</th><th>{label(metric)}</th><th>样本</th><th>未知</th></tr></thead><tbody>
      {data.map((point, index) => <tr key={`${point.time}:${index}`}><td>{time(point.time, timezone)}</td><td>{display(point.metric?.total)}</td><td>{display(point.metric?.samples)}</td><td>{display(point.metric?.unknown)}</td></tr>)}
    </tbody></table></div>
  </div>;
}

function valueText(value: unknown): string {
  if (value == null) return "未知";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value, null, 2) ?? "未记录";
}

function Breakdown({ value }: { value: unknown }) {
  const rows: { group: string; metric: string; value: Metric }[] = [];
  if (value && typeof value === "object" && !Array.isArray(value)) {
    for (const [group, record] of Object.entries(value)) {
      if (!record || typeof record !== "object" || Array.isArray(record)) continue;
      for (const [metric, sample] of Object.entries(record)) {
        if (!sample || typeof sample !== "object" || Array.isArray(sample)) continue;
        const candidate = sample as Partial<Metric>;
        if ("total" in candidate && typeof candidate.samples === "number" && typeof candidate.unknown === "number") rows.push({ group, metric, value: candidate as Metric });
      }
    }
  }
  if (!rows.length) return <pre>{valueText(value)}</pre>;
  return <div className="table-scroll"><table><thead><tr><th>分组</th><th>指标</th><th>合计</th><th>样本</th><th>未知</th></tr></thead><tbody>
    {rows.map((row) => <tr key={`${row.group}:${row.metric}`}><td>{row.group}</td><td>{label(row.metric)}</td><td>{display(row.value.total)}</td><td>{display(row.value.samples)}</td><td>{display(row.value.unknown)}</td></tr>)}
  </tbody></table></div>;
}

export default function StatisticsPage() {
  const [days, setDays] = useState(1);
  const [timezone, setTimezone] = useState(() => Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai");
  const [module, setModule] = useState("");
  const [actor, setActor] = useState("");
  const [route, setRoute] = useState("");
  const [query, setQuery] = useState(() => makeRange(1, Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai", "", "", ""));
  const [metric, setMetric] = useState("physical_attempts");
  const [dimension, setDimension] = useState("module");
  const view = useDomainResource<Statistics>("system/statistics", { ...GLOBAL_SCOPE, ...query });
  const data = view.data;
  return <section className="panel system-statistics">
    <div className="section-heading"><div><h2>系统统计</h2><p>区分业务结果、实际模型调用、用量覆盖和资源观测。</p></div><button className="button" onClick={() => void view.refresh()} disabled={view.loading}>刷新统计</button></div>
    <form className="domain-form statistics-filters" onSubmit={(event) => { event.preventDefault(); setQuery(makeRange(days, timezone, module, actor, route)); }}>
      <label>时间范围<select value={days} onChange={(event) => setDays(Number(event.target.value))}><option value={1}>最近 1 天</option><option value={3}>最近 3 天</option><option value={7}>最近 7 天</option></select></label>
      <label>时区<input value={timezone} required onChange={(event) => setTimezone(event.target.value)} /></label>
      <label>模块<input value={module} onChange={(event) => setModule(event.target.value)} placeholder="全部" /></label>
      <label>角色 ID<input value={actor} onChange={(event) => setActor(event.target.value)} placeholder="全部" /></label>
      <label>模型路由 ID<input value={route} onChange={(event) => setRoute(event.target.value)} placeholder="全部" /></label>
      <button className="button primary" disabled={view.loading}>应用筛选</button>
    </form>
    {view.error && <p role="alert">统计暂不可用：{view.error}</p>}
    {view.loading && !data && <p role="status">正在读取统计…</p>}
    {data && (data.status === "unavailable" || !data.range) && <div role="alert"><p>统计投影暂不可用，现有业务不受此页面影响。</p><pre>{valueText(data.coverage)}</pre></div>}
    {data && data.status !== "unavailable" && data.range && <>
      <p className="muted">所选范围：{time(data.range.start, data.range.timezone)} — {time(data.range.end, data.range.timezone)} · {data.range.timezone} · 粒度 {data.range.resolution} · 统计截至 {time(data.as_of, data.range.timezone)}</p>
      <p role="status">{data.coverage.complete ? "统计来源已报告完整" : "统计来源尚未完整覆盖"}{data.coverage.missing?.length ? ` · ${data.coverage.missing.map(valueText).join("、")}` : ""}</p>
      <p className="muted">缓存与推理 Token 是已记录总量的子项，不再累加；排队、执行和 HTTP 阶段分别展示，不能把响应等待当成供应商纯推理时间。</p>
      <h3>所选时间范围</h3>{Object.keys(data.overview ?? {}).length ? <Cards values={data.overview} /> : <p>此范围没有返回统计样本；未覆盖的来源不能按零计算。</p>}
      {data.recovery_rate && <p>恢复验证：符合条件 {display(data.recovery_rate.eligible)} 项 · 已判定 {display(data.recovery_rate.evaluated)} 项 · 已验证 {display(data.recovery_rate.verified)} 项 · 验证比例 {data.recovery_rate.rate == null ? "未知（尚无已判定样本）" : `${(100 * data.recovery_rate.rate).toFixed(1)}%`}。仅以已判定结果为分母。</p>}
      <section><h3>趋势</h3><label>指标 <select value={metric} onChange={(event) => setMetric(event.target.value)}>{chartOptions.map((key) => <option value={key} key={key}>{label(key)}</option>)}</select></label><Trend series={data.series ?? []} metric={metric} timezone={data.range.timezone} /></section>
      <details><summary>累计统计</summary><Cards values={data.cumulative ?? {}} /></details>
      <section><h3>分组明细</h3><label>分组 <select value={dimension} onChange={(event) => setDimension(event.target.value)}>{[["module", "模块"], ["actor_id", "角色"], ["route_id", "模型路由"], ["family", "业务类型"], ["state", "状态"]].map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label>
        {data.breakdowns?.[dimension] == null ? <p>此分组没有返回数据。</p> : <Breakdown value={data.breakdowns[dimension]} />}
      </section>
      <details><summary>资源观测与统计覆盖</summary><h4>资源</h4><pre>{valueText(data.resources)}</pre><h4>来源覆盖</h4><pre>{valueText(data.coverage)}</pre><h4>来源水位与最近记录</h4><pre>{valueText(data.freshness)}</pre><p>没有新数据本身不代表故障；没有分布样本时不显示长期百分位，未知用量不按零计算。</p></details>
    </>}
  </section>;
}
