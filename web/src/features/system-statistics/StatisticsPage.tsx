import { useState } from "react";
import { RefreshCw, SlidersHorizontal } from "lucide-react";
import { GLOBAL_SCOPE, useDomainResource } from "../../api/useDomainResource";
import UsageDashboard from "./UsageDashboard";
import {
  coverageText,
  display,
  groups,
  label,
  makeRange,
  metricValue,
  time,
  type Metric,
  type Metrics,
  type Statistics,
} from "./statisticsModel";
import "./statistics.css";

function Cards({
  values,
  businessOnly = false,
}: {
  values: Metrics;
  businessOnly?: boolean;
}) {
  return (
    <div className="statistics-metric-groups">
      {groups
        .filter(
          (group) =>
            !businessOnly ||
            !["模型调用与用量", "耗时分段"].includes(group.title),
        )
        .map((group) => {
          const available = group.names.filter(
            ([key]) => values[key] !== undefined,
          );
          if (!available.length) return null;
          return (
            <section className="statistics-surface" key={group.title}>
              <h3>{group.title}</h3>
              <dl className="statistics-metric-list">
                {available.map(([key, title]) => {
                  const metric = values[key];
                  return (
                    <div key={key}>
                      <dt>
                        {title}
                        <small>{coverageText(metric)}</small>
                      </dt>
                      <dd title={metricValue(key, metric.total, false)}>
                        {metricValue(key, metric.total)}
                        {metric.unknown > 0 && metric.total != null && (
                          <small>已知部分</small>
                        )}
                      </dd>
                    </div>
                  );
                })}
              </dl>
              {group.title === "耗时分段" && (
                <p className="statistics-footnote">
                  各阶段为已知样本的累计时长，分别计量；响应等待不等于模型纯推理耗时。
                </p>
              )}
            </section>
          );
        })}
    </div>
  );
}

function Trend({
  series,
  metric,
  timezone,
}: {
  series: Statistics["series"];
  metric: string;
  timezone: string;
}) {
  const data = series.map((point) => ({
    time: point.time,
    metric: point.metrics?.[metric],
  }));
  const max = Math.max(0, ...data.map((point) => point.metric?.total ?? 0));
  const width = 720,
    height = 160;
  const points = data.map((point, index) => ({
    ...point,
    x:
      data.length > 1
        ? 30 + (index * (width - 60)) / (data.length - 1)
        : width / 2,
    y:
      point.metric?.total == null
        ? null
        : height -
          20 -
          (max > 0 ? (point.metric.total / max) * (height - 40) : 0),
  }));
  const segments: string[] = [];
  let segment: string[] = [];
  for (const point of points) {
    if (point.y == null) {
      if (segment.length) segments.push(segment.join(" "));
      segment = [];
    } else segment.push(`${point.x},${point.y}`);
  }
  if (segment.length) segments.push(segment.join(" "));
  return (
    <>
      {data.some((point) => point.metric?.total != null) ? (
        <svg
          className="statistics-chart statistics-business-chart"
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={`${label(metric)}的时间趋势；缺失样本处不连线`}
        >
          <line
            x1="30"
            y1={height - 20}
            x2={width - 30}
            y2={height - 20}
            className="statistics-grid-line"
          />
          {segments.map((path, index) => (
            <polyline
              key={index}
              fill="none"
              stroke="currentColor"
              strokeWidth="3"
              points={path}
            />
          ))}
          {points
            .filter((point) => point.y != null)
            .map((point) => (
              <circle
                key={point.time}
                cx={point.x}
                cy={point.y!}
                r="3"
                fill="currentColor"
              >
                <title>
                  {time(point.time, timezone)} ·{" "}
                  {metricValue(metric, point.metric?.total, false)}
                </title>
              </circle>
            ))}
        </svg>
      ) : (
        <p className="statistics-empty">所选时间范围没有可用样本。</p>
      )}
      <div className="table-scroll statistics-table-scroll">
        <table className="statistics-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>{label(metric)}</th>
              <th>已记录</th>
              <th>未知</th>
            </tr>
          </thead>
          <tbody>
            {data.map((point) => (
              <tr key={point.time}>
                <th scope="row">{time(point.time, timezone)}</th>
                <td>{metricValue(metric, point.metric?.total, false)}</td>
                <td>{display(point.metric?.samples)}</td>
                <td>{display(point.metric?.unknown)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function valueText(value: unknown): string {
  if (value == null) return "未知";
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  )
    return String(value);
  return JSON.stringify(value, null, 2) ?? "未记录";
}

function Breakdown({ value }: { value: unknown }) {
  const rows: { group: string; metric: string; value: Metric }[] = [];
  if (value && typeof value === "object" && !Array.isArray(value)) {
    for (const [group, record] of Object.entries(value)) {
      if (!record || typeof record !== "object" || Array.isArray(record))
        continue;
      for (const [metric, sample] of Object.entries(record)) {
        if (!sample || typeof sample !== "object" || Array.isArray(sample))
          continue;
        const candidate = sample as Partial<Metric>;
        if (
          "total" in candidate &&
          typeof candidate.samples === "number" &&
          typeof candidate.unknown === "number"
        )
          rows.push({ group, metric, value: candidate as Metric });
      }
    }
  }
  if (!rows.length)
    return <p className="statistics-empty">此分组没有返回数据。</p>;
  return (
    <div className="table-scroll statistics-table-scroll">
      <table className="statistics-table">
        <thead>
          <tr>
            <th>分组</th>
            <th>指标</th>
            <th>合计</th>
            <th>已记录</th>
            <th>未知</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.group}:${row.metric}`}>
              <th scope="row">{row.group}</th>
              <td>{label(row.metric)}</td>
              <td>{metricValue(row.metric, row.value.total, false)}</td>
              <td>{display(row.value.samples)}</td>
              <td>{display(row.value.unknown)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function StatisticsPage() {
  const [days, setDays] = useState("1");
  const [date, setDate] = useState("");
  const [timezone, setTimezone] = useState(
    () => Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai",
  );
  const [module, setModule] = useState("");
  const [actor, setActor] = useState("");
  const [route, setRoute] = useState("");
  const [formError, setFormError] = useState("");
  const [query, setQuery] = useState(() =>
    makeRange(
      1,
      Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai",
      "",
      "",
      "",
    ),
  );
  const [metric, setMetric] = useState("received");
  const [dimension, setDimension] = useState("module");
  const view = useDomainResource<Statistics>("system/statistics", {
    ...GLOBAL_SCOPE,
    ...query,
  });
  const data = view.data;
  return (
    <section className="panel system-statistics">
      <div className="section-heading">
        <div>
          <h2>系统统计</h2>
          <p>模型用量、功能分布与每日变化，一目了然。</p>
        </div>
        <button
          className="button statistics-refresh"
          onClick={() => void view.refresh()}
          disabled={view.loading}
        >
          <RefreshCw size={15} />
          {view.loading ? "正在刷新" : "刷新统计"}
        </button>
      </div>
      <form
        className="domain-form statistics-filters"
        onSubmit={(event) => {
          event.preventDefault();
          try {
            if (days === "date" && !date) {
              setFormError("请选择统计日期。");
              return;
            }
            setQuery(
              makeRange(
                Number(days),
                timezone,
                module,
                actor,
                route,
                days === "date" ? date : "",
              ),
            );
            setFormError("");
          } catch {
            setFormError("请填写有效的时区和日期。");
          }
        }}
      >
        <div className="statistics-filter-main">
          <label>
            时间范围
            <select
              value={days}
              onChange={(event) => setDays(event.target.value)}
            >
              <option value="1">今日</option>
              <option value="3">最近 3 天</option>
              <option value="7">最近 7 天</option>
              <option value="30">最近 30 天</option>
              <option value="date">指定日期</option>
            </select>
          </label>
          {days === "date" && (
            <label>
              统计日期
              <input
                type="date"
                value={date}
                required
                onChange={(event) => setDate(event.target.value)}
              />
            </label>
          )}
          <button className="button primary" disabled={view.loading}>
            应用筛选
          </button>
          <span className="statistics-filter-hint">
            {data?.range
              ? `${data.range.timezone} · 统计截至 ${time(data.as_of, data.range.timezone)}`
              : "按所选时区统计"}
          </span>
        </div>
        <details className="statistics-advanced">
          <summary>
            <SlidersHorizontal size={14} />
            更多筛选
            {query.module || query.actor_id || query.route_id
              ? " · 已启用"
              : ""}
          </summary>
          <div className="statistics-filter-fields">
            <label>
              模块
              <input
                value={module}
                onChange={(event) => setModule(event.target.value)}
                placeholder="全部模块"
              />
            </label>
            <label>
              角色 ID
              <input
                value={actor}
                onChange={(event) => setActor(event.target.value)}
                placeholder="全部角色"
              />
            </label>
            <label>
              模型路由 ID
              <input
                value={route}
                onChange={(event) => setRoute(event.target.value)}
                placeholder="全部模型路由"
              />
            </label>
            <label>
              时区
              <input
                value={timezone}
                required
                onChange={(event) => setTimezone(event.target.value)}
              />
            </label>
          </div>
        </details>
      </form>
      {(view.error || formError) && (
        <p className="statistics-notice" role="alert">
          {formError || `统计暂不可用：${view.error}`}
        </p>
      )}
      {view.loading && !data && (
        <div className="statistics-empty" role="status">
          正在读取统计…
        </div>
      )}
      {data && (data.status === "unavailable" || !data.range) && (
        <div className="statistics-notice" role="alert">
          <p>统计投影暂不可用，现有业务不受此页面影响。</p>
          <pre>{valueText(data.coverage)}</pre>
        </div>
      )}
      {data && data.status !== "unavailable" && data.range && (
        <>
          <details
            className={`statistics-coverage ${data.coverage.complete ? "complete" : ""}`}
          >
            <summary>
              <span className="statistics-status-dot" />
              <span role="status">
                {data.coverage.complete
                  ? "统计来源已报告完整"
                  : "统计来源尚未完整覆盖"}
                {data.coverage.missing?.length
                  ? ` · ${data.coverage.missing.map(valueText).join("、")}`
                  : ""}
              </span>
              <span className="statistics-coverage-hint">查看统计口径</span>
            </summary>
            <p>
              所选范围：{time(data.range.start, data.range.timezone)} —{" "}
              {time(data.range.end, data.range.timezone)}
              。未覆盖的来源与缺失的用量均不按零计算。
            </p>
            <pre>{valueText(data.coverage.incomplete)}</pre>
          </details>
          <UsageDashboard data={data} />
          <div className="statistics-section-title">
            <div>
              <h3>业务运行</h3>
              <p>消息投递、领域处理与恢复结果分别统计</p>
            </div>
          </div>
          {Object.keys(data.overview ?? {}).length ? (
            <Cards values={data.overview} businessOnly />
          ) : (
            <p className="statistics-empty">
              此范围没有返回统计样本；未覆盖的来源不能按零计算。
            </p>
          )}
          {data.recovery_rate && (
            <p className="statistics-recovery">
              恢复验证：符合条件 {display(data.recovery_rate.eligible)} 项 ·
              已判定 {display(data.recovery_rate.evaluated)} 项 · 已验证{" "}
              {display(data.recovery_rate.verified)} 项 · 验证比例{" "}
              {data.recovery_rate.rate == null
                ? "未知（尚无已判定样本）"
                : `${(100 * data.recovery_rate.rate).toFixed(1)}%`}
              。仅以已判定结果为分母。
            </p>
          )}
          <details className="statistics-surface statistics-details">
            <summary>模型用量与耗时明细</summary>
            <Cards
              values={Object.fromEntries(
                Object.entries(data.overview).filter(([key]) =>
                  groups
                    .filter((group) =>
                      ["模型调用与用量", "耗时分段"].includes(group.title),
                    )
                    .some((group) =>
                      group.names.some(([name]) => name === key),
                    ),
                ),
              )}
            />
          </details>
          <details className="statistics-surface statistics-details">
            <summary>其他指标趋势</summary>
            <label className="statistics-control">
              指标
              <select
                value={metric}
                onChange={(event) => setMetric(event.target.value)}
              >
                {groups
                  .flatMap((group) => group.names)
                  .map(([key, title]) => (
                    <option value={key} key={key}>
                      {title}
                    </option>
                  ))}
              </select>
            </label>
            <Trend
              series={data.series ?? []}
              metric={metric}
              timezone={data.range.timezone}
            />
          </details>
          <details className="statistics-surface statistics-details">
            <summary>累计统计</summary>
            <p className="statistics-footnote">
              当前筛选条件下，截至所选范围结束的历史累计。
            </p>
            <Cards values={data.cumulative ?? {}} />
          </details>
          <details className="statistics-surface statistics-details">
            <summary>更多分组明细</summary>
            <label className="statistics-control">
              分组
              <select
                value={dimension}
                onChange={(event) => setDimension(event.target.value)}
              >
                {[
                  ["module", "模块"],
                  ["actor_id", "角色"],
                  ["route_id", "模型路由"],
                  ["family", "业务类型"],
                  ["state", "状态"],
                ].map(([key, text]) => (
                  <option key={key} value={key}>
                    {text}
                  </option>
                ))}
              </select>
            </label>
            <Breakdown value={data.breakdowns?.[dimension]} />
          </details>
          <details className="statistics-surface statistics-details">
            <summary>资源观测与统计覆盖</summary>
            <h4>资源</h4>
            <pre>{valueText(data.resources)}</pre>
            <h4>来源覆盖</h4>
            <pre>{valueText(data.coverage)}</pre>
            <h4>来源水位与最近记录</h4>
            <pre>{valueText(data.freshness)}</pre>
            <p className="statistics-footnote">
              没有新数据本身不代表故障；没有分布样本时不显示长期百分位。
            </p>
          </details>
        </>
      )}
    </section>
  );
}
