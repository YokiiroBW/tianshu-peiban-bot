import { useState } from "react";
import { StatusRail } from "../../../components/StatusRail";
import { useInfrastructureAction, useInfrastructureQuery } from "../api";
import { time } from "../presentation";
import { controlReasons, healthLabel, runtimeLabel } from "./presentation";
import type { ExecutionResult, Operation, OperationList } from "./types";

const states: Record<string, string> = {
  queued: "已受理 · 等待执行",
  running: "后台处理中",
  observed: "已完成读回",
  partial: "部分完成",
  failed: "操作失败",
  unknown: "结果未知",
};
const stages: Record<string, string> = {
  queued: "等待处理",
  reading: "读取节点",
  planning: "生成预览",
  submitting: "提交节点",
  reconciling: "核对实际结果",
  finished: "本轮结束",
  pulling: "拉取镜像",
  applying: "应用配置",
  observing: "读取实际运行状态",
  accepted: "节点已受理",
  validating: "核对目标和配置",
  complete: "节点执行结束",
  unknown: "等待确认节点结果",
};
const actionLabels: Record<string, string> = {
  "container.start": "启动容器",
  "container.stop": "停止容器",
  "container.restart": "重启容器",
  "stack.start": "启动应用",
  "stack.stop": "停止应用",
  "stack.restart": "重启应用",
  "stack.apply": "执行部署计划",
  create: "创建部署",
  configure: "修改部署",
  update: "更新镜像",
  restore: "恢复配置版本",
};
const acceptance: Record<string, string> = {
  not_sent: "尚未发送",
  submitted: "节点已受理",
  unknown: "送达未知",
  rejected: "已拒绝",
};
export function OperationStatus({
  operation,
  pending,
  error,
  recover,
  recovering = false,
}: {
  operation: Operation | null;
  pending?: { client_request_id: string } | null;
  error?: string;
  recover?: () => void;
  recovering?: boolean;
}) {
  if (!operation && !pending && !error) return null;
  const runtime =
    operation?.result && "runtime_state" in operation.result
      ? (operation.result as ExecutionResult)
      : null;
  const inProgress =
    (operation && ["queued", "running"].includes(operation.state)) ||
    runtime?.execution_active === true;
  return (
    <section className="dockge-operation" aria-label="当前操作">
      <h4>
        {operation
          ? (states[operation.state] ?? "结果未知")
          : pending
            ? "正在查询原操作"
            : "操作尚未发出"}
      </h4>
      {operation && (
        <>
          <StatusRail
            tone={
              operation.state === "observed"
                ? "blue"
                : operation.state === "failed"
                  ? "red"
                  : operation.state === "unknown"
                    ? "gray"
                    : "yellow"
            }
            label={stages[operation.stage] ?? operation.stage}
          />
          {["action", "execute"].includes(operation.kind) && (
            <p>指令送达 · {acceptance[operation.acceptance] ?? "未知"}</p>
          )}
          <p className="dockge-source">更新 · {time(operation.updated_at)}</p>
        </>
      )}
      {inProgress && (
        <progress className="dockge-progress" aria-label="后台操作处理中" />
      )}
      {runtime && (
        <div aria-label="操作实际结果">
          <p>
            命令结果 ·{" "}
            {(
              {
                not_sent: "未发送",
                sent: "已发送",
                succeeded: "命令已结束",
                failed: "命令失败",
                unknown: "未知",
              } as Record<string, string>
            )[runtime.command_state] ?? "未知"}
          </p>
          {runtime.execution_active !== false && (
            <p className="infra-warning">
              {runtime.execution_active === true
                ? "节点命令仍在运行，仅查询原操作。"
                : "节点命令是否结束尚未确认，仅查询原操作。"}
            </p>
          )}
          <p>实际运行 · {runtimeLabel(runtime.runtime_state)}</p>
          <p>健康检查 · {healthLabel(runtime.health_state)}</p>
          {runtime.containers?.map((item) => (
            <p
              className="dockge-source"
              key={item.container_id}
              title={item.container_id}
            >
              {item.container_id.slice(0, 12)} · {item.state} ·{" "}
              {healthLabel(item.health)}
            </p>
          ))}
          {runtime.version_id && (
            <p className="dockge-source">配置版本 · {runtime.version_id}</p>
          )}
        </div>
      )}
      {operation?.reason_codes.length ? (
        <p>{controlReasons(operation.reason_codes)}</p>
      ) : null}
      {error && (
        <p role="alert" className="infra-error">
          {error}
        </p>
      )}
      {(!operation || operation.state === "unknown") && pending && (
        <p>送达或执行结果尚未确认。保留原请求，只查询实际结果。</p>
      )}
      {pending && (
        <p className="dockge-source infra-mono">请求 · {pending.client_request_id}</p>
      )}
      {operation && (
        <p className="dockge-source infra-mono">
          操作 · {operation.operation_id}
        </p>
      )}
      {recover && (pending || operation?.can_refresh) && (
        <button className="button" disabled={recovering} onClick={recover}>
          {recovering ? "正在查询" : "查询原操作"}
        </button>
      )}
    </section>
  );
}

function HistoryDetail({ id }: { id: string }) {
  const query = useInfrastructureQuery<{ operation: Operation }>(
    "control/operation",
    { operation_id: id },
  );
  const action = useInfrastructureAction();
  return (
    <OperationStatus
      operation={query.data?.operation ?? null}
      error={query.error || action.error}
      recovering={action.pending}
      recover={async () => {
        await action.send("control/operation", {
          operation_id: id,
          refresh: true,
        });
        await query.reload();
      }}
    />
  );
}

export function OperationHistory({
  nodeId,
  stackId,
  containerId,
}: {
  nodeId: string;
  stackId?: string;
  containerId?: string;
}) {
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [selected, setSelected] = useState("");
  const query = useInfrastructureQuery<OperationList>("control/operations", {
    node_id: nodeId,
    ...(stackId ? { stack_id: stackId } : {}),
    cursor: cursors.at(-1),
    page_size: 10,
  });
  const items = query.data?.items ?? [];
  return (
    <section aria-label="操作记录">
      <p className="dockge-source">平台持久操作记录 · 离开页面仍在后台执行</p>
      {containerId && (
        <p className="dockge-source">
          按该容器所属应用查询记录，容器身份在记录内核对。
        </p>
      )}
      {query.error && (
        <p role="alert" className="infra-error">
          {query.error}
        </p>
      )}
      <div
        className="dockge-history"
        tabIndex={0}
        role="group"
        aria-label="操作记录内容"
      >
        {!items.length && (
          <p>{query.loading ? "正在读取记录…" : "本页没有操作记录。"}</p>
        )}
        {items.map((operation) => (
          <button
            key={operation.operation_id}
            className="infra-app-choice"
            aria-pressed={selected === operation.operation_id}
            onClick={() => setSelected(operation.operation_id)}
          >
            <strong>
              {actionLabels[operation.action] ?? "应用操作"} ·{" "}
              {states[operation.state] ?? "结果未知"}
            </strong>
            <small>{time(operation.created_at)}</small>
            <small className="infra-mono">
              {operation.container_id ??
                operation.stack_id ??
                operation.node_id}
            </small>
          </button>
        ))}
      </div>
      <nav className="infra-pagination" aria-label="操作记录分页">
        <button
          className="button"
          disabled={cursors.length === 1}
          onClick={() => {
            setSelected("");
            setCursors((values) => values.slice(0, -1));
          }}
        >
          上一页记录
        </button>
        <span>第 {cursors.length} 页</span>
        <button
          className="button"
          disabled={!query.data?.page.has_more || !query.data.page.next_cursor}
          onClick={() => {
            setSelected("");
            setCursors((values) => [...values, query.data!.page.next_cursor]);
          }}
        >
          下一页记录
        </button>
      </nav>
      {selected && <HistoryDetail key={selected} id={selected} />}
    </section>
  );
}
