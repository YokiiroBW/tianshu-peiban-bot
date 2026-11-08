import { functionNames } from "../settings/providerTypes";

export type Metric = { total: number | null; samples: number; unknown: number };
export type Metrics = Record<string, Metric>;
export type UsageRow = {
  function?: string;
  provider_id?: string;
  provider_name?: string | null;
  model?: string;
  metrics: Metrics;
};
export type UsageDay = {
  date: string;
  metrics: Metrics;
  by_function: UsageRow[];
  by_model: UsageRow[];
};
export type ModelUsage = {
  status: "available" | "unavailable";
  summary?: Metrics;
  by_function?: UsageRow[];
  by_model?: UsageRow[];
  by_provider?: UsageRow[];
  daily?: UsageDay[];
};
export type Statistics = {
  status?: "available" | "unavailable";
  range: { start: string; end: string; timezone: string; resolution: string };
  overview: Metrics;
  cumulative: Metrics;
  series: { time: string; metrics: Metrics }[];
  breakdowns: Record<string, unknown>;
  model_usage?: ModelUsage;
  resources: unknown;
  recovery_rate?: {
    eligible: number | null;
    evaluated: number | null;
    verified: number | null;
    rate: number | null;
  } | null;
  freshness?: { source_watermarks: unknown; source_last_seen: unknown } | null;
  coverage: {
    complete: boolean;
    missing?: unknown[];
    incomplete?: unknown;
    [key: string]: unknown;
  };
  as_of: string | null;
};

export const groups: { title: string; names: [string, string][] }[] = [
  {
    title: "消息与投递",
    names: [
      ["received", "已接收"],
      ["processed", "已处理"],
      ["reply_required", "需要回复"],
      ["delivered", "已投递"],
      ["withdrawn", "已撤回"],
    ],
  },
  {
    title: "模型调用与用量",
    names: [
      ["physical_attempts", "实际模型调用"],
      ["business_success", "业务成功"],
      ["input_tokens", "实际输入 Token"],
      ["output_tokens", "实际输出 Token"],
      ["estimated_input_tokens", "估算输入 Token"],
      ["estimated_output_tokens", "估算输出 Token"],
      ["cached_input_tokens", "输入中的缓存 Token"],
      ["cache_write_tokens", "缓存写入 Token"],
      ["reasoning_tokens", "输出中的推理 Token"],
      ["actual_cost", "实际费用"],
      ["estimated_cost", "估算费用"],
    ],
  },
  {
    title: "记忆、成长与生活",
    names: [
      ["memory_applied", "记忆已应用"],
      ["memory_failed", "记忆未完成"],
      ["growth_material_blocked", "成长材料阻塞"],
      ["growth_review_blocked", "成长复核阻塞"],
      ["life_scheduled", "日程已安排"],
      ["life_intentional_idle", "主动留白"],
      ["life_deferred_no_slot", "暂时排不下"],
      ["life_stale_state", "依据已变化"],
      ["life_invalid_proposal", "无效日程提议"],
      ["uncovered_material_age_seconds", "未覆盖材料年龄合计"],
    ],
  },
  {
    title: "独立任务与恢复",
    names: [
      ["media_completed", "媒体任务完成"],
      ["media_failed", "媒体任务失败"],
      ["recovery_eligible", "符合恢复统计条件"],
      ["recovery_evaluated", "恢复已判定"],
      ["recovery_verified", "恢复后已验证"],
      ["recovery_failed", "恢复失败"],
    ],
  },
  {
    title: "耗时分段",
    names: [
      ["queue_ms", "排队"],
      ["execution_ms", "执行"],
      ["ttft_ms", "首个有效输出"],
      ["pool_wait_ms", "连接池等待"],
      ["dns_ms", "DNS"],
      ["connect_ms", "建立连接"],
      ["send_ms", "发送"],
      ["response_wait_ms", "等待响应"],
      ["read_ms", "读取响应"],
      ["effective_output_ms", "有效输出"],
      ["http_total_ms", "HTTP 总耗时"],
    ],
  },
];
const names = Object.fromEntries(groups.flatMap((group) => group.names));
export function label(key: string): string {
  return (
    names[key] ??
    ({
      total_tokens: "总 Token",
      attempt_records: "尝试记录",
      model_failed: "失败",
      model_unknown: "结果未知",
    }[key] ||
      key)
  );
}
export function display(
  value: number | null | undefined,
  compact = false,
): string {
  if (value == null || !Number.isFinite(value)) return "未知";
  if (compact && Math.abs(value) >= 1e9)
    return `${(value / 1e9).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}B`;
  if (compact && Math.abs(value) >= 1e6)
    return `${(value / 1e6).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}M`;
  if (compact && Math.abs(value) >= 1e4)
    return `${(value / 1e3).toLocaleString("zh-CN", { maximumFractionDigits: 1 })}K`;
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}
export function duration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "未知";
  if (ms < 1000) return `${display(ms)} ms`;
  if (ms < 60000) return `${display(ms / 1000)} 秒`;
  if (ms < 3600000) return `${display(ms / 60000)} 分钟`;
  return `${display(ms / 3600000)} 小时`;
}
export function metricValue(
  key: string,
  value: number | null | undefined,
  compact = true,
): string {
  return key.endsWith("_ms")
    ? duration(value)
    : key.endsWith("_seconds")
      ? duration(value == null ? null : value * 1000)
      : display(value, compact);
}
export function coverageText(metric: Metric | undefined): string {
  if (!metric) return "暂无用量记录";
  return `已记录 ${display(metric.samples)} 项${metric.unknown ? ` · 未知 ${display(metric.unknown)} 项` : ""}`;
}
export function time(
  value: string | null | undefined,
  timezone: string,
): string {
  if (!value || Number.isNaN(Date.parse(value))) return value ?? "未记录";
  try {
    return new Date(value).toLocaleString("zh-CN", {
      timeZone: timezone,
      hour12: false,
    });
  } catch {
    return value;
  }
}
export function rowName(row: UsageRow): string {
  if (row.function)
    return row.function === "unassigned"
      ? "功能未记录"
      : (functionNames[row.function] ?? row.function);
  const provider =
    row.provider_name ||
    (row.provider_id === "unassigned" ? "供应商未记录" : row.provider_id) ||
    "供应商未记录";
  return row.model
    ? `${provider} / ${row.model === "unassigned" ? "模型未记录" : row.model}`
    : provider;
}
export function rowKey(row: UsageRow): string {
  return JSON.stringify([row.function, row.provider_id, row.model]);
}
export function sortedUsage(rows: UsageRow[], metric: string): UsageRow[] {
  return [...rows].sort(
    (a, b) =>
      (b.metrics[metric]?.total ?? -1) - (a.metrics[metric]?.total ?? -1) ||
      rowName(a).localeCompare(rowName(b)),
  );
}

/** Resolve midnight in the selected IANA zone, independently of the browser zone. */
export function makeRange(
  days: number,
  timezone: string,
  module: string,
  actor: string,
  route: string,
  date = "",
) {
  const now = new Date();
  let end = now;
  let start = new Date(now.getTime() - days * 86400000);
  if (days === 1 || date) {
    const formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone: timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    });
    const parts = Object.fromEntries(
      formatter.formatToParts(now).map((part) => [part.type, part.value]),
    );
    const calendar = date || `${parts.year}-${parts.month}-${parts.day}`;
    const midnight = (value: string) => {
      const desired = Date.parse(`${value}T00:00:00Z`);
      let result = desired;
      for (let index = 0; index < 3; index++) {
        const local = Object.fromEntries(
          formatter
            .formatToParts(new Date(result))
            .map((part) => [part.type, part.value]),
        );
        const represented = Date.UTC(
          Number(local.year),
          Number(local.month) - 1,
          Number(local.day),
          Number(local.hour),
          Number(local.minute),
          Number(local.second),
        );
        result += desired - represented;
      }
      return new Date(result);
    };
    start = midnight(calendar);
    if (date)
      end = midnight(
        new Date(Date.parse(`${calendar}T00:00:00Z`) + 86400000)
          .toISOString()
          .slice(0, 10),
      );
  }
  return {
    start: start.toISOString(),
    end: end.toISOString(),
    timezone,
    module: module.trim() || null,
    actor_id: actor.trim() || null,
    route_id: route.trim() || null,
  };
}
