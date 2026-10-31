import { bytes, percent } from "./presentation";
import type { ContainerStats } from "./types";
import "./container-resource-meter.css";

function reading(value: number | null | undefined) {
  return value != null && Number.isFinite(value) && value >= 0 ? value : null;
}

export function ContainerResourceMeter({
  kind,
  stats,
  detailed = false,
}: {
  kind: "cpu" | "memory";
  stats: ContainerStats | null | undefined;
  detailed?: boolean;
}) {
  const available = stats?.availability === "ok" ? stats : null;
  const memory = available?.memory;
  const adjusted = reading(memory?.cache_adjusted_usage_bytes);
  const usage = adjusted ?? reading(memory?.raw_usage_bytes);
  const limit = reading(memory?.limit_bytes);
  const ratio =
    kind === "cpu"
      ? reading(available?.cpu?.usage_ratio)
      : usage != null && limit != null && limit > 0
        ? reading(usage / limit)
        : null;
  const label = kind === "cpu" ? "CPU" : "内存";
  const value = percent(ratio);
  const basis = adjusted != null ? "扣除缓存" : "原始用量";
  const note =
    kind === "cpu"
      ? "单核为 100%"
      : `${basis} ${bytes(usage)} · 限额 ${bytes(limit)}${limit === 0 ? "（无有效分母）" : ""}`;
  const description = `${label} ${value} · ${note}`;

  return (
    <div
      className="infra-resource-meter"
      data-resource={kind}
      data-detailed={detailed || undefined}
      data-available={ratio != null}
    >
      <div className="infra-resource-heading">
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
      <div
        className="infra-resource-track"
        role={ratio == null ? undefined : "meter"}
        aria-label={ratio == null ? undefined : `${label}用量`}
        aria-valuemin={ratio == null ? undefined : 0}
        aria-valuemax={ratio == null ? undefined : Math.max(100, ratio * 100)}
        aria-valuenow={ratio == null ? undefined : ratio * 100}
        aria-valuetext={ratio == null ? undefined : description}
        title={description}
      >
        {ratio != null && (
          <span style={{ width: `${Math.min(100, ratio * 100)}%` }} />
        )}
      </div>
      <small className="infra-resource-note" title={note}>
        {detailed || kind === "cpu" ? note : bytes(usage)}
      </small>
      {detailed && kind === "memory" && memory?.cache_source && (
        <small className="infra-resource-note">
          缓存来源 {memory.cache_source}
        </small>
      )}
    </div>
  );
}
