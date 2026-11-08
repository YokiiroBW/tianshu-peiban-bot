import { useState } from "react";
import {
  Activity,
  ArrowDownLeft,
  ArrowUpRight,
  Coins,
  Layers3,
} from "lucide-react";
import {
  coverageText,
  display,
  duration,
  label,
  metricValue,
  rowKey,
  rowName,
  sortedUsage,
  type Metric,
  type Metrics,
  type Statistics,
  type UsageDay,
  type UsageRow,
} from "./statisticsModel";

function Value({
  metric,
  name,
  compact = true,
}: {
  metric: Metric | undefined;
  name: string;
  compact?: boolean;
}) {
  return (
    <span
      title={`${metricValue(name, metric?.total, false)} · ${coverageText(metric)}`}
    >
      {metricValue(name, metric?.total, compact)}
      {metric?.unknown && metric.total != null ? (
        <small className="statistics-partial">已知部分</small>
      ) : null}
    </span>
  );
}

function StatCard({
  name,
  title,
  metric,
  icon,
}: {
  name: string;
  title: string;
  metric?: Metric;
  icon: React.ReactNode;
}) {
  return (
    <article className="statistics-card">
      <div className="statistics-card-label">
        {icon}
        <span>{title}</span>
      </div>
      <strong>
        <Value name={name} metric={metric} />
      </strong>
      <small>{coverageText(metric)}</small>
    </article>
  );
}

function UsageTable({
  rows,
  metric,
  title,
}: {
  rows: UsageRow[];
  metric: string;
  title: string;
}) {
  return (
    <div className="table-scroll statistics-table-scroll">
      <table className="statistics-table">
        <caption className="sr-only">{title}</caption>
        <thead>
          <tr>
            <th>名称</th>
            <th>总 Token</th>
            <th>输入</th>
            <th>输出</th>
            <th>调用</th>
            <th>失败</th>
            <th>结果未知</th>
            <th>用量覆盖</th>
          </tr>
        </thead>
        <tbody>
          {sortedUsage(rows, metric).map((row) => (
            <tr key={rowKey(row)}>
              <th scope="row">{rowName(row)}</th>
              {[
                "total_tokens",
                "input_tokens",
                "output_tokens",
                "physical_attempts",
                "model_failed",
                "model_unknown",
              ].map((name) => (
                <td key={name}>
                  <Value
                    name={name}
                    metric={row.metrics[name]}
                    compact={false}
                  />
                </td>
              ))}
              <td>{coverageText(row.metrics.total_tokens)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Ranking({
  title,
  subtitle,
  rows,
  metric,
  toolbar,
}: {
  title: string;
  subtitle: string;
  rows: UsageRow[];
  metric: string;
  toolbar?: React.ReactNode;
}) {
  const ordered = sortedUsage(rows, metric);
  const max = Math.max(
    0,
    ...ordered.map((row) => row.metrics[metric]?.total ?? 0),
  );
  const knownTotal = rows.reduce(
    (sum, row) => sum + (row.metrics[metric]?.total ?? 0),
    0,
  );
  return (
    <section
      className="statistics-surface statistics-ranking"
      aria-label={title}
    >
      <div className="statistics-section-title">
        <div>
          <h3>{title}</h3>
          <p>{subtitle}</p>
        </div>
        {toolbar}
      </div>
      {!rows.length ? (
        <div className="statistics-empty">此范围暂无可用记录</div>
      ) : (
        <>
          <ol className="statistics-rank-list">
            {ordered.slice(0, 6).map((row, index) => {
              const value = row.metrics[metric];
              return (
                <li key={rowKey(row)}>
                  <span className="statistics-rank-number">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <div className="statistics-rank-body">
                    <div className="statistics-rank-heading">
                      <span
                        className="statistics-rank-name"
                        title={rowName(row)}
                      >
                        {rowName(row)}
                      </span>
                      <strong>
                        <Value name={metric} metric={value} />
                      </strong>
                    </div>
                    <div className="statistics-bar-track" aria-hidden="true">
                      <span
                        style={{
                          width: `${value?.total != null && max > 0 ? (value.total / max) * 100 : 0}%`,
                        }}
                      />
                    </div>
                    <div className="statistics-rank-meta">
                      <span>
                        {value?.total != null && knownTotal > 0
                          ? `占已知${metric === "total_tokens" ? "用量" : "调用"} ${((value.total / knownTotal) * 100).toFixed(1)}%`
                          : "暂无完整用量"}
                      </span>
                      <span>{coverageText(row.metrics.total_tokens)}</span>
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>
          <details className="statistics-inline-details">
            <summary>查看全部 {display(rows.length)} 项及用量明细</summary>
            <UsageTable rows={rows} metric={metric} title={title} />
          </details>
        </>
      )}
    </section>
  );
}

function topNames(rows: UsageRow[]) {
  return (
    sortedUsage(rows, "total_tokens")
      .filter((row) => row.metrics.total_tokens?.total != null)
      .slice(0, 2)
      .map(
        (row) =>
          `${rowName(row)} ${display(row.metrics.total_tokens.total, true)}`,
      )
      .join(" · ") || "未记录"
  );
}

function DailyUsage({ days }: { days: UsageDay[] }) {
  const [metric, setMetric] = useState("total_tokens");
  const [selected, setSelected] = useState<string | null>(null);
  const maximum = Math.max(
    0,
    ...days.map((day) => day.metrics[metric]?.total ?? 0),
  );
  const hasKnown = days.some((day) => day.metrics[metric]?.total != null);
  const width = Math.max(620, days.length * 30 + 80),
    height = 230,
    left = 62,
    bottom = 190,
    top = 16;
  const step = (width - left - 16) / Math.max(1, days.length);
  const active = days.find((day) => day.date === selected);
  return (
    <section className="statistics-surface" aria-label="每日用量">
      <div className="statistics-section-title">
        <div>
          <h3>每日用量</h3>
          <p>悬停或点选柱形查看详情，空缺日期保留为未知。</p>
        </div>
        <label className="statistics-control">
          趋势指标
          <select
            value={metric}
            onChange={(event) => setMetric(event.target.value)}
          >
            {[
              "total_tokens",
              "input_tokens",
              "output_tokens",
              "physical_attempts",
            ].map((name) => (
              <option value={name} key={name}>
                {label(name)}
              </option>
            ))}
          </select>
        </label>
      </div>
      {!hasKnown ? (
        <div className="statistics-empty">
          此范围没有已知的{label(metric)}记录
        </div>
      ) : (
        <div className="statistics-chart-scroll">
          <svg
            className="statistics-chart"
            viewBox={`0 0 ${width} ${height}`}
            style={{ minWidth: width }}
            role="img"
            aria-label={`每日${label(metric)}柱状图；缺失用量不按零显示`}
          >
            {[0, 1, 2, 3, 4].map((tick) => {
              const y = bottom - (tick / 4) * (bottom - top);
              return (
                <g key={tick}>
                  <line
                    x1={left}
                    x2={width - 16}
                    y1={y}
                    y2={y}
                    className="statistics-grid-line"
                  />
                  <text x={left - 10} y={y + 4} textAnchor="end">
                    {display((maximum * tick) / 4, true)}
                  </text>
                </g>
              );
            })}
            {days.map((day, index) => {
              const value = day.metrics[metric],
                x = left + index * step + step * 0.2;
              const barHeight =
                value?.total == null
                  ? 0
                  : maximum > 0
                    ? (value.total / maximum) * (bottom - top)
                    : 0;
              const name = `${day.date} · ${label(metric)} ${display(value?.total)} · ${coverageText(value)}`;
              return (
                <g key={day.date}>
                  <g
                    role="button"
                    tabIndex={0}
                    aria-label={name}
                    className={`statistics-chart-bar ${selected === day.date ? "selected" : ""}`}
                    onClick={() => setSelected(day.date)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        setSelected(day.date);
                      }
                    }}
                  >
                    <title>{name}</title>
                    <rect
                      x={x - step * 0.1}
                      y={top}
                      width={step * 0.8}
                      height={bottom - top + 5}
                      fill="transparent"
                    />
                    {value?.total == null ? (
                      <line
                        x1={x}
                        x2={x + step * 0.6}
                        y1={bottom - 3}
                        y2={bottom - 3}
                        className="statistics-chart-missing"
                      />
                    ) : (
                      <rect
                        className="statistics-chart-column"
                        x={x}
                        y={bottom - Math.max(2, barHeight)}
                        width={step * 0.6}
                        height={Math.max(2, barHeight)}
                        rx="4"
                        opacity={value.unknown ? 0.6 : 1}
                      />
                    )}
                  </g>
                  {(index % Math.ceil(days.length / 10) === 0 ||
                    index === days.length - 1) && (
                    <text
                      x={x + step * 0.3}
                      y={bottom + 25}
                      textAnchor="middle"
                    >
                      {day.date.slice(5)}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
        </div>
      )}
      {active && (
        <p className="statistics-chart-selection" role="status">
          {active.date} · {label(metric)}{" "}
          <strong>{display(active.metrics[metric]?.total)}</strong> ·{" "}
          {coverageText(active.metrics[metric])}
        </p>
      )}
      <details className="statistics-inline-details">
        <summary>每日明细</summary>
        <div className="table-scroll statistics-table-scroll">
          <table className="statistics-table">
            <thead>
              <tr>
                <th>日期</th>
                <th>总 Token</th>
                <th>调用</th>
                <th>失败</th>
                <th>主要功能</th>
                <th>主要模型</th>
                <th>用量覆盖</th>
              </tr>
            </thead>
            <tbody>
              {[...days].reverse().map((day) => (
                <tr key={day.date}>
                  <th scope="row">{day.date}</th>
                  <td>
                    <Value
                      name="total_tokens"
                      metric={day.metrics.total_tokens}
                      compact={false}
                    />
                  </td>
                  <td>
                    <Value
                      name="physical_attempts"
                      metric={day.metrics.physical_attempts}
                      compact={false}
                    />
                  </td>
                  <td>
                    <Value
                      name="model_failed"
                      metric={day.metrics.model_failed}
                      compact={false}
                    />
                  </td>
                  <td>{topNames(day.by_function)}</td>
                  <td>{topNames(day.by_model)}</td>
                  <td>{coverageText(day.metrics.total_tokens)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}

export default function UsageDashboard({ data }: { data: Statistics }) {
  const [rankingMetric, setRankingMetric] = useState("total_tokens");
  const [modelGroup, setModelGroup] = useState<"by_model" | "by_provider">(
    "by_model",
  );
  const usage = data.model_usage;
  const values: Metrics =
    usage?.summary ??
    (data.breakdowns?.family as Record<string, Metrics> | undefined)?.models ??
    data.overview;
  const tokens = values.total_tokens;
  const execution = values.execution_ms;
  const average =
    execution?.total != null && execution.samples
      ? execution.total / execution.samples
      : null;
  return (
    <>
      <section className="statistics-overview" aria-label="模型用量概览">
        <article className="statistics-card statistics-hero">
          <div className="statistics-card-label">
            <Layers3 size={18} />
            <span>模型总 Token</span>
          </div>
          <strong className="statistics-hero-number">
            <Value name="total_tokens" metric={tokens} />
          </strong>
          <p>输入 + 输出 · {coverageText(tokens)}</p>
          <div className="statistics-token-split">
            <div>
              <span>
                <ArrowDownLeft size={14} />
                实际输入 Token
              </span>
              <strong>
                <Value name="input_tokens" metric={values.input_tokens} />
              </strong>
              <small>{coverageText(values.input_tokens)}</small>
            </div>
            <div>
              <span>
                <ArrowUpRight size={14} />
                实际输出 Token
              </span>
              <strong>
                <Value name="output_tokens" metric={values.output_tokens} />
              </strong>
              <small>{coverageText(values.output_tokens)}</small>
            </div>
          </div>
        </article>
        <div className="statistics-summary-grid">
          <StatCard
            name="physical_attempts"
            title="实际模型调用"
            metric={values.physical_attempts}
            icon={<Activity size={16} />}
          />
          <article className="statistics-card">
            <div className="statistics-card-label">平均执行耗时</div>
            <strong>{duration(average)}</strong>
            <small>{coverageText(execution)}</small>
          </article>
          <StatCard
            name="actual_cost"
            title="实际费用"
            metric={values.actual_cost}
            icon={<Coins size={16} />}
          />
          <StatCard
            name="model_failed"
            title="模型失败记录"
            metric={values.model_failed}
            icon={<Activity size={16} />}
          />
        </div>
      </section>
      <div className="statistics-token-details">
        {[
          ["cached_input_tokens", "缓存命中"],
          ["cache_write_tokens", "缓存写入"],
          ["reasoning_tokens", "推理 Token"],
          ["model_unknown", "结果未知"],
        ].map(([name, title]) => (
          <div key={name}>
            <span>{title}</span>
            <strong>
              <Value name={name} metric={values[name]} />
            </strong>
          </div>
        ))}
      </div>
      <p className="statistics-footnote">
        Token
        总量仅汇总输入、输出均已记录的尝试；缓存与推理为子项，不重复累加。费用未返回时显示未知。
      </p>
      <div className="statistics-section-title statistics-ranking-title">
        <div>
          <h3>用量分布</h3>
          <p>按实际执行时的功能和模型归属汇总</p>
        </div>
        <label className="statistics-control">
          排行依据
          <select
            value={rankingMetric}
            onChange={(event) => setRankingMetric(event.target.value)}
          >
            <option value="total_tokens">Token 用量</option>
            <option value="physical_attempts">调用次数</option>
          </select>
        </label>
      </div>
      {usage?.status === "available" ? (
        <>
          <div className="statistics-rankings">
            <Ranking
              title={
                modelGroup === "by_model" ? "模型用量排行" : "供应商用量排行"
              }
              subtitle={
                modelGroup === "by_model"
                  ? "相同供应商的同名模型合并显示"
                  : "汇总供应商下的全部模型"
              }
              rows={usage[modelGroup] ?? []}
              metric={rankingMetric}
              toolbar={
                <div className="statistics-segmented" aria-label="模型分组">
                  {(
                    [
                      ["by_model", "模型"],
                      ["by_provider", "供应商"],
                    ] as const
                  ).map(([key, title]) => (
                    <button
                      type="button"
                      key={key}
                      aria-pressed={modelGroup === key}
                      onClick={() => setModelGroup(key)}
                    >
                      {title}
                    </button>
                  ))}
                </div>
              }
            />
            <Ranking
              title="功能用量排行"
              subtitle="对话、记忆、生活计划等实际模型任务"
              rows={usage.by_function ?? []}
              metric={rankingMetric}
            />
          </div>
          <DailyUsage days={usage.daily ?? []} />
        </>
      ) : (
        <div className="statistics-surface statistics-empty">
          功能与模型用量明细暂不可用，已有统计仍显示在下方。
        </div>
      )}
    </>
  );
}
