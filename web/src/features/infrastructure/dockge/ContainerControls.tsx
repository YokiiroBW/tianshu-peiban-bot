import { DetailCard } from "../../../components/DetailCard";
import { useInfrastructureAction, useInfrastructureQuery } from "../api";
import { ActionButtons } from "./ActionButtons";
import { OperationHistory, OperationStatus } from "./Operations";
import { ControlSource, controlReasons, currentControl } from "./presentation";
import type { ContainerControlDetail } from "./types";
import { useControlRequest } from "./useControlRequest";

export function ContainerControls({
  nodeId,
  containerId,
  revision,
  current,
}: {
  nodeId: string;
  containerId: string;
  revision: number;
  current: boolean;
}) {
  const query = useInfrastructureQuery<ContainerControlDetail>(
    "control/detail",
    { node_id: nodeId, container_id: containerId },
  );
  const command = useControlRequest(`${nodeId}/container/${containerId}`);
  const refresh = useInfrastructureAction();
  const view = query.data;
  const target =
    view?.container?.container_id === containerId ? view.container : null;
  return (
    <section aria-label="容器控制">
      <div className="infra-heading">
        <h4>容器操作</h4>
        <button
          className="button"
          disabled={refresh.pending}
          onClick={async () => {
            await refresh.send("control/detail", {
              node_id: nodeId,
              container_id: containerId,
              refresh: true,
            });
            await query.reload();
          }}
        >
          刷新控制状态
        </button>
      </div>
      {view && <ControlSource value={view.observation} />}
      {refresh.error && (
        <p role="alert" className="infra-error">
          {refresh.error}
        </p>
      )}
      {target && view ? (
        <ActionButtons
          actions={target.allowed_actions}
          maintenance={target.maintenance}
          available={
            current &&
            currentControl(view.observation) &&
            view.connection_revision === revision
          }
          busy={command.busy}
          target="容器"
          onAction={(name) => {
            void command.send("action", {
              node_id: nodeId,
              expected_connection_revision: revision,
              action: `container.${name}`,
              container_id: containerId,
            });
          }}
        />
      ) : (
        <p className="dockge-source">
          {query.loading
            ? "正在核对容器控制能力…"
            : "未取得此容器的控制描述，暂不可操作。"}
          {query.error || controlReasons(view?.observation.reason_codes ?? [])}
        </p>
      )}
      <OperationStatus
        {...command}
        recover={() => {
          void command.recover();
        }}
      />
      {target && (
        <DetailCard title="操作记录" className="infra-section">
          <OperationHistory
            nodeId={nodeId}
            stackId={target?.stack_id ?? undefined}
            containerId={containerId}
          />
        </DetailCard>
      )}
    </section>
  );
}
