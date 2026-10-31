import { controlReasons, MaintenanceNotice } from "./presentation";
import type { Availability, Maintenance } from "./types";

const labels: Record<string, string> = {
  start: "启动",
  stop: "停止",
  restart: "重启",
};
export function ActionButtons({
  actions,
  maintenance,
  available,
  busy,
  target,
  onAction,
}: {
  actions: Record<string, Availability>;
  maintenance: Maintenance;
  available: boolean;
  busy: boolean;
  target: "容器" | "应用";
  onAction: (name: string) => void;
}) {
  if (maintenance.required) return <MaintenanceNotice value={maintenance} />;
  const disabledReasons = [
    ...new Set(
      Object.entries(actions)
        .filter(([name, value]) => name in labels && !value.enabled)
        .flatMap(([, value]) => value.reason_codes),
    ),
  ];
  return (
    <>
      <div className="dockge-actions" aria-label={`${target}操作`}>
        {Object.entries(labels).map(([name, label]) => (
          <button
            key={name}
            className="button"
            disabled={busy || !available || !actions[name]?.enabled}
            title={controlReasons(actions[name]?.reason_codes ?? [])}
            onClick={() => onAction(name)}
          >
            {label}
            {target}
          </button>
        ))}
      </div>
      {!available && (
        <p className="dockge-source">
          等待当前完整目标身份与节点读回后可操作。
        </p>
      )}
      {disabledReasons.length > 0 && (
        <p className="dockge-source">{controlReasons(disabledReasons)}</p>
      )}
    </>
  );
}
