import { StatusRail } from "../../components/StatusRail";
import type { Container, NodeSummary, Observation, Tone } from "./types";

export const kinds: Record<string, string> = {
  synology: "NAS",
  linux: "Linux 节点",
  openwrt: "OpenWrt 路由器",
};
export const states: Record<string, string> = {
  running: "运行中",
  restarting: "重启中",
  created: "尚未启动",
  paused: "已暂停",
  exited: "已停止",
  removing: "移除中",
  dead: "运行异常",
  unknown: "未知",
};
export const health: Record<string, string> = {
  healthy: "检查通过",
  unhealthy: "检查异常",
  starting: "检查启动中",
  none: "未配置检查",
  unknown: "检查未知",
};
const reasons: Record<string, string> = {
  read_observed: "已取得节点观测",
  not_sampled: "尚未取得读数",
  first_sample: "首次采样，等待下一次差值",
  counter_reset: "计数器已重置，等待下一次采样",
  stale: "采样间隔过长，等待连续读数",
  ssh_unavailable: "SSH 连接不可用",
  ssh_failed: "SSH 连接失败",
  infrastructure_executor_unavailable: "节点执行器尚未安装或部署配置不可用",
  infrastructure_executor_timeout: "节点执行器超时",
  infrastructure_executor_failed: "节点执行器运行失败",
  infrastructure_output_exceeded: "执行器结果超过读取上限",
  infrastructure_response_invalid: "执行器返回的观测无法核对",
  node_unavailable: "节点无法读取，保留上次数据",
  capability_not_supported: "节点未提供这项能力",
  cursor_invalidated: "采集期间清单变化，等待重新采集",
  inventory_incomplete: "清单尚未完整取得",
  infrastructure_cycle_timeout: "本轮采集到达时间上限",
  per_device_source_not_configured: "尚未接入逐终端流量来源",
};
export function stageLabel(stage: string | undefined) {
  return (
    (
      {
        queued: "等待采集",
        "node.snapshot": "读取主机",
        "docker.inventory": "读取容器清单",
        "docker.stats": "读取容器资源",
        "compose.inventory": "核对 Compose",
        finished: "本轮结束",
      } as Record<string, string>
    )[stage ?? ""] ?? "正在读取"
  );
}
export function explanation(codes: string[]) {
  return codes.map((code) => reasons[code] ?? code).join(" · ");
}
export function time(value: string | null | undefined) {
  if (!value || Number.isNaN(Date.parse(value))) return "尚无记录";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}
export function bytes(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    ++unit;
  }
  return `${value.toLocaleString("zh-CN", { maximumFractionDigits: unit ? 1 : 0 })} ${units[unit]}`;
}
export function rate(value: number | null | undefined) {
  return value == null ? "—" : `${bytes(value)}/s`;
}
export function percent(value: number | null | undefined) {
  return value == null ? "—" : `${(value * 100).toFixed(1)}%`;
}
export function duration(value: number | null | undefined) {
  if (value == null) return "—";
  const days = Math.floor(value / 86400),
    hours = Math.floor((value % 86400) / 3600),
    minutes = Math.floor((value % 3600) / 60);
  return `${days ? `${days} 天 ` : ""}${hours} 小时 ${minutes} 分`;
}
export function NodeRail({ node }: { node: NodeSummary }) {
  const labels: Record<string, { tone: Tone; label: string }> = {
    current: { tone: "blue", label: "当前可达" },
    offline: { tone: "gray", label: "节点离线" },
    stale: { tone: "gray", label: "读数已过时" },
    missing: { tone: "yellow", label: "等待采样" },
    disabled: { tone: "gray", label: "已暂停采集" },
  };
  const state = labels[node.connection_state] ?? {
    tone: "gray" as const,
    label: "状态未知",
  };
  return <StatusRail {...state} />;
}
export function containerTone(container: Container, current: boolean): Tone {
  if (!current || container.availability !== "ok") return "gray";
  if (container.state === "dead" || container.health === "unhealthy")
    return "red";
  if (
    ["restarting", "removing"].includes(container.state) ||
    container.health === "starting"
  )
    return "yellow";
  return container.state === "running" ? "blue" : "gray";
}
export function Freshness({
  observation,
}: {
  observation: Omit<Observation<unknown>, "data">;
}) {
  return (
    <div
      className={`infra-freshness ${observation.freshness !== "current" ? "infra-old" : ""}`}
    >
      <span>
        {observation.freshness === "current"
          ? "当前采样"
          : observation.observed_at
            ? "上次读数"
            : "尚无读数"}{" "}
        · {time(observation.observed_at)}
      </span>
      {observation.completeness?.state === "partial" && (
        <strong>
          部分结果 · {observation.completeness.returned} /{" "}
          {observation.completeness.total ?? "未知总数"}
        </strong>
      )}
      {observation.freshness !== "current" && observation.last_attempt_at && (
        <span>最近尝试 · {time(observation.last_attempt_at)}</span>
      )}
      {observation.reason_codes.length > 0 && (
        <span>{explanation(observation.reason_codes)}</span>
      )}
    </div>
  );
}
export function UnconfiguredInfrastructure() {
  return (
    <section className="panel infra-empty">
      <h2>节点观测尚未配置</h2>
      <p>
        请部署管理员安装节点执行器，并在部署配置中登记 SSH
        节点。配置完成后，这里会列出可选择的节点。
      </p>
      <p>节点地址与密钥由执行器管理，网页无需填写私钥。</p>
    </section>
  );
}
