import {
  Cpu,
  HardDrive,
  MemoryStick,
  RefreshCw,
  Server,
  type LucideIcon,
} from "lucide-react";
import { useInfrastructureQuery } from "../infrastructure/api";
import { bytes, duration, percent, time } from "../infrastructure/presentation";
import type { PlatformHostResources } from "../infrastructure/types";
import "./workbench.css";

function fraction(
  used: number | null | undefined,
  total: number | null | undefined,
) {
  return used != null &&
    total != null &&
    Number.isFinite(used) &&
    Number.isFinite(total) &&
    used >= 0 &&
    total > 0
    ? used / total
    : null;
}

function ResourceTile({
  label,
  icon: Icon,
  ratio,
  detail,
}: {
  label: string;
  icon: LucideIcon;
  ratio: number | null;
  detail: string;
}) {
  const value =
    ratio != null && Number.isFinite(ratio) && ratio >= 0 ? ratio : null;
  return (
    <div className="host-resource-tile">
      <div className="host-resource-label">
        <Icon aria-hidden="true" />
        <span>{label}</span>
      </div>
      <strong className="host-resource-value">{percent(value)}</strong>
      <div
        className="host-resource-track"
        role={value == null ? undefined : "meter"}
        aria-label={label}
        aria-valuemin={value == null ? undefined : 0}
        aria-valuemax={value == null ? undefined : 100}
        aria-valuenow={value == null ? undefined : Math.min(100, value * 100)}
        aria-valuetext={
          value == null ? undefined : `${percent(value)} · ${detail}`
        }
      >
        {value != null && (
          <span style={{ width: `${Math.min(100, value * 100)}%` }} />
        )}
      </div>
      <small>{detail}</small>
    </div>
  );
}

export function HostResources() {
  const query = useInfrastructureQuery<PlatformHostResources>("host", {});
  const node = query.data?.node;
  const observation = node?.snapshot;
  const snapshot = observation?.data;
  const current = !query.error && observation?.freshness === "current";
  const filesystem =
    snapshot?.filesystems.find(
      (item) => item.mountpoint === "/" && item.total_bytes > 0,
    ) ?? snapshot?.filesystems.find((item) => item.total_bytes > 0);
  const state = query.error
    ? "读取中断"
    : query.loading
      ? "正在读取"
      : !query.data?.configured
        ? "待绑定主机"
        : !snapshot
          ? "等待采样"
          : current
            ? "当前采样"
            : "上次读数";

  return (
    <section
      className="panel host-resources"
      aria-label="天枢主机资源"
      data-current={current || undefined}
    >
      <div className="host-resources-heading">
        <div className="host-resources-title">
          <span className="host-resources-icon">
            <Server aria-hidden="true" />
          </span>
          <div>
            <h2>天枢主机资源</h2>
            <p>
              {node
                ? `${node.label}${snapshot?.identity.hostname ? ` · ${snapshot.identity.hostname}` : ""}`
                : "平台所在机器"}
            </p>
          </div>
        </div>
        <div className="host-resources-actions">
          <span className="badge host-resources-state">{state}</span>
          <button
            className="button"
            disabled={query.loading}
            onClick={() => void query.reload()}
          >
            <RefreshCw aria-hidden="true" />
            刷新读数
          </button>
        </div>
      </div>
      <div className="host-resource-grid">
        <ResourceTile
          label="CPU 使用率"
          icon={Cpu}
          ratio={snapshot?.cpu.usage_ratio ?? null}
          detail={
            snapshot?.cpu.usage_ratio == null
              ? "等待 CPU 采样"
              : "主机处理器用量"
          }
        />
        <ResourceTile
          label="内存"
          icon={MemoryStick}
          ratio={fraction(
            snapshot?.memory.used_bytes,
            snapshot?.memory.total_bytes,
          )}
          detail={`${bytes(snapshot?.memory.used_bytes)} / ${bytes(snapshot?.memory.total_bytes)}`}
        />
        <ResourceTile
          label="存储"
          icon={HardDrive}
          ratio={fraction(filesystem?.used_bytes, filesystem?.total_bytes)}
          detail={
            filesystem
              ? `${filesystem.mountpoint} · ${bytes(filesystem.used_bytes)} / ${bytes(filesystem.total_bytes)}`
              : "等待存储采样"
          }
        />
      </div>
      {!!snapshot?.filesystems.length && snapshot.filesystems.length > 1 && (
        <div className="host-resource-volumes">
          {snapshot.filesystems
            .filter((item) => item !== filesystem)
            .map((item) => (
              <span key={item.id} title={`可用 ${bytes(item.available_bytes)}`}>
                <HardDrive aria-hidden="true" />
                {item.mountpoint}
                <strong>
                  {percent(fraction(item.used_bytes, item.total_bytes))}
                </strong>
                <small>
                  {bytes(item.used_bytes)} / {bytes(item.total_bytes)}
                </small>
              </span>
            ))}
        </div>
      )}
      <div className="host-resources-footer">
        {snapshot?.system.uptime_seconds != null && (
          <span>已运行 {duration(snapshot.system.uptime_seconds)}</span>
        )}
        {observation?.observed_at && (
          <span>采样于 {time(observation.observed_at)}</span>
        )}
      </div>
      {query.error ? (
        <p className="host-resources-note" role="alert">
          {query.error}
          {snapshot ? " 当前保留上次采样读数。" : ""}
        </p>
      ) : query.data && !query.data.configured ? (
        <p className="host-resources-note">
          尚未绑定天枢运行主机，绑定后显示这台机器的资源采样。
        </p>
      ) : node && !snapshot ? (
        <p className="host-resources-note">正在等待这台主机的资源采样。</p>
      ) : snapshot && !current ? (
        <p className="host-resources-note">采样已过时，当前展示上次读数。</p>
      ) : null}
    </section>
  );
}
