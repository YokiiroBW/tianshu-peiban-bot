import { Drawer } from "../../../components/Drawer";
import { DetailCard } from "../../../components/DetailCard";
import { useState } from "react";
import { StatusRail } from "../../../components/StatusRail";
import { useInfrastructureAction, useInfrastructureQuery } from "../api";
import { time } from "../presentation";
import { ActionButtons } from "./ActionButtons";
import { DeploymentPanel } from "./DeploymentPanel";
import { OperationHistory, OperationStatus } from "./Operations";
import {
  ControlSource,
  controlReasons,
  currentControl,
  healthLabel,
  MaintenanceNotice,
  runtimeLabel,
} from "./presentation";
import type { ControlStack, StackDetail } from "./types";
import { useControlRequest } from "./useControlRequest";

type Tab = "runtime" | "configure" | "versions" | "history";
export function StackInspector({
  nodeId,
  stackId,
  listedStack,
}: {
  nodeId: string;
  stackId: string;
  listedStack?: ControlStack;
}) {
  const query = useInfrastructureQuery<StackDetail>("control/detail", {
    node_id: nodeId,
    stack_id: stackId,
  });
  const refresh = useInfrastructureAction();
  const command = useControlRequest(`${nodeId}/stack/${stackId}`);
  const [tab, setTab] = useState<Tab>("runtime");
  const [intent, setIntent] = useState<"configure" | "update">("configure");
  const [restoreVersion, setRestoreVersion] = useState("");
  const view = query.data;
  const detail =
    view?.node_id === nodeId &&
    (!view.stack || view.stack.stack_id === stackId) &&
    (!view.refresh ||
      (view.refresh.node_id === nodeId && view.refresh.stack_id === stackId))
      ? view
      : null;
  const stack = detail?.stack;
  const listed = listedStack?.stack_id === stackId ? listedStack : null;
  const reading =
    !query.error &&
    (detail
      ? Boolean(
          detail.refresh &&
          !detail.refresh.settled &&
          ["queued", "running"].includes(detail.refresh.state),
        )
      : query.loading);
  const displayName = stack?.display_name ?? listed?.display_name;
  const available = Boolean(
    detail &&
    stack &&
    currentControl(detail.observation) &&
    !stack.maintenance.required,
  );
  const canConfigure = Boolean(
    available && stack?.allowed_actions.configure?.enabled,
  );
  return (
    <aside
      className="panel infra-pane infra-container-detail"
      aria-label="Compose 栈详情"
    >
      <header className="infra-detail-heading">
        <div className="infra-heading">
          <div>
            <p className="infra-eyebrow">Compose / Dockge</p>
            <h3 title={displayName}>
              {displayName ?? (reading ? "正在读取应用" : "应用详情不可用")}
            </h3>
          </div>
          <button
            className="button"
            disabled={refresh.pending}
            onClick={async () => {
              await refresh.send("control/detail", {
                node_id: nodeId,
                stack_id: stackId,
                refresh: true,
              });
              await query.reload();
            }}
            aria-label="刷新应用详情"
          >
            刷新
          </button>
        </div>
        {stack && (
          <>
            <StatusRail
              tone={
                available && stack.runtime_state === "running"
                  ? "blue"
                  : stack.runtime_state === "partial"
                    ? "yellow"
                    : "gray"
              }
              label={runtimeLabel(stack.runtime_state)}
            />
            <p className="infra-health">
              健康检查 · {healthLabel(stack.health_state)}
            </p>
          </>
        )}
      </header>
      <nav className="dockge-tabs" aria-label="应用详情视图">
        {(
          [
            ["runtime", "运行详情"],
            ["configure", "配置部署"],
            ["versions", "配置版本"],
            ["history", "操作记录"],
          ] as const
        ).map(([id, label]) => (
          <button
            className="button"
            key={id}
            aria-pressed={tab === id}
            onClick={() => {
              setTab(id);
              if (id === "configure") setIntent("configure");
            }}
          >
            {label}
          </button>
        ))}
      </nav>
      <div
        className="infra-detail-scroll"
        tabIndex={0}
        role="group"
        aria-label="应用详情内容"
      >
        {(query.error || refresh.error) && (
          <p className="infra-error" role="alert">
            {query.error || refresh.error}
          </p>
        )}
        {detail && (
          <ControlSource
            value={{
              ...detail.observation,
              reason_codes: [
                ...new Set([
                  ...detail.observation.reason_codes,
                  ...(detail.refresh?.reason_codes ?? []),
                ]),
              ],
            }}
          />
        )}
        {!detail || !detail.stack ? (
          <>
            {listed && <MaintenanceNotice value={listed.maintenance} />}
            <p
              role={reading ? "status" : "alert"}
              className={reading ? "dockge-source" : "infra-error"}
            >
              {reading
                ? "等待后台读取配置与运行状态…"
                : view && !detail
                  ? "应用详情与当前目标不一致，请刷新核对。"
                  : "应用详情无法读取，请刷新重试。"}
            </p>
          </>
        ) : (
          <>
            <OperationStatus
              {...command}
              recover={() => {
                void command.recover();
              }}
            />
            {(tab === "versions" || tab === "history") && (
              <MaintenanceNotice value={detail.stack.maintenance} />
            )}
            {tab === "runtime" && (
              <>
                <ActionButtons
                  actions={stack!.allowed_actions}
                  maintenance={stack!.maintenance}
                  available={available}
                  busy={command.busy}
                  target="应用"
                  onAction={(name) => {
                    void command.send("action", {
                      node_id: nodeId,
                      expected_connection_revision: detail.connection_revision,
                      action: `stack.${name}`,
                      stack_id: stackId,
                      expected_revision: stack!.revision,
                    });
                  }}
                />
                <button
                  className="button"
                  disabled={
                    !available ||
                    command.busy ||
                    !stack!.allowed_actions.update?.enabled
                  }
                  onClick={() => {
                    setIntent("update");
                    setTab("configure");
                  }}
                >
                  镜像更新
                </button>
                <dl className="infra-detail-list">
                  <div>
                    <dt>项目</dt>
                    <dd className="infra-mono">{stack!.project_name}</dd>
                  </div>
                  <div>
                    <dt>配置归属</dt>
                    <dd>
                      {{
                        platform: "平台管理",
                        external: "Dockge 外部管理",
                        unknown: "未知",
                      }[stack!.ownership] ?? "未知"}
                    </dd>
                  </div>
                  <div>
                    <dt>栈身份</dt>
                    <dd className="infra-mono">{stackId}</dd>
                  </div>
                  <div>
                    <dt>输入版本</dt>
                    <dd className="infra-mono">{stack!.revision}</dd>
                  </div>
                </dl>
                <section className="infra-section">
                  <h3>实际容器身份</h3>
                  <ul className="infra-compact-list">
                    {stack!.containers.map((id) => (
                      <li className="infra-mono" key={id}>
                        {id}
                      </li>
                    ))}
                  </ul>
                  {!stack!.containers.length && (
                    <p>本次读回没有列出容器，实际运行以节点结果为准。</p>
                  )}
                </section>
              </>
            )}
            {tab === "configure" && (
              <DetailCard
                title={intent === "update" ? "更新应用设置" : "修改应用配置"}
              >
                <DeploymentPanel
                  key={`${stackId}/${intent}`}
                  nodeId={nodeId}
                  connectionRevision={detail.connection_revision}
                  detail={detail}
                  intent={intent}
                  command={command}
                  available={
                    intent === "update"
                      ? available &&
                        Boolean(stack!.allowed_actions.update?.enabled)
                      : canConfigure
                  }
                />
              </DetailCard>
            )}
            {tab === "versions" && (
              <>
                <p className="dockge-source">
                  按预览恢复受管配置与运行版本，保留数据目录与卷。
                </p>
                {!detail.versions.length && <p>尚无可读取的配置版本。</p>}
                {detail.versions.map((version) => (
                  <article className="infra-card" key={version.version_id}>
                    <h4>
                      {version.label || version.version_id}
                      {version.current && " · 当前版本"}
                    </h4>
                    <p className="dockge-source">{time(version.created_at)}</p>
                    <p className="infra-mono dockge-source">
                      {version.version_id}
                    </p>
                    {version.reason_codes.length > 0 && (
                      <p>{controlReasons(version.reason_codes)}</p>
                    )}
                    <button
                      className="button"
                      disabled={
                        !available ||
                        command.busy ||
                        !stack!.allowed_actions.restore?.enabled ||
                        !version.restorable ||
                        version.current
                      }
                      onClick={() => setRestoreVersion(version.version_id)}
                    >
                      选择此版本预览恢复
                    </button>
                  </article>
                ))}
                {restoreVersion && (
                  <Drawer
                    title={"恢复应用版本"}
                    variant="card"
                    open={true}
                    onClose={() => setRestoreVersion("")}
                  >
                    <DeploymentPanel
                      key={`${stackId}/restore/${restoreVersion}`}
                      nodeId={nodeId}
                      connectionRevision={detail.connection_revision}
                      detail={detail}
                      intent="restore"
                      restoreVersion={restoreVersion}
                      command={command}
                      available={
                        available &&
                        Boolean(stack!.allowed_actions.restore?.enabled) &&
                        detail.versions.some(
                          (version) =>
                            version.version_id === restoreVersion &&
                            version.restorable &&
                            !version.current,
                        )
                      }
                    />
                  </Drawer>
                )}
              </>
            )}
            {tab === "history" && (
              <OperationHistory
                key={stackId}
                nodeId={nodeId}
                stackId={stackId}
              />
            )}
          </>
        )}
      </div>
    </aside>
  );
}
