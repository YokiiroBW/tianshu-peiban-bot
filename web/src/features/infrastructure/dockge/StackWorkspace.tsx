import { Drawer } from "../../../components/Drawer";
import { useEffect, useState } from "react";
import { StatusRail } from "../../../components/StatusRail";
import { useInfrastructureAction, useInfrastructureQuery } from "../api";
import { NodeRail } from "../presentation";
import type { InfrastructureView } from "../types";
import { DeploymentPanel } from "./DeploymentPanel";
import { OperationHistory, OperationStatus } from "./Operations";
import {
  ControlSource,
  controlReasons,
  currentControl,
  healthLabel,
  runtimeLabel,
} from "./presentation";
import { StackInspector } from "./StackInspector";
import type { ControlView } from "./types";
import { useControlRequest } from "./useControlRequest";

function CreateStack({ nodeId, view }: { nodeId: string; view: ControlView }) {
  const command = useControlRequest(`${nodeId}/create`);
  return (
    <aside
      className="panel infra-pane infra-container-detail"
      aria-label="创建 Compose 应用"
    >
      <header className="infra-detail-heading">
        <h3>创建 Compose 应用</h3>
      </header>
      <div
        className="infra-detail-scroll"
        tabIndex={0}
        role="group"
        aria-label="新应用配置内容"
      >
        <OperationStatus
          {...command}
          recover={() => {
            void command.recover();
          }}
        />
        <DeploymentPanel
          nodeId={nodeId}
          connectionRevision={view.connection_revision}
          intent="create"
          available={
            view.capabilities.available &&
            view.capabilities.create.enabled &&
            currentControl(view.observation)
          }
          command={command}
        />
      </div>
    </aside>
  );
}

function NodeStacks({ nodeId }: { nodeId: string }) {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState("");
  const [creating, setCreating] = useState(false);
  const [history, setHistory] = useState(false);
  const query = useInfrastructureQuery<ControlView>("control/view", {
    node_id: nodeId,
    query: search,
    page,
    page_size: 50,
  });
  const refresh = useInfrastructureAction();
  const view = query.data;
  useEffect(() => {
    if (
      !creating &&
      !history &&
      view &&
      !view.stacks.some((stack) => stack.stack_id === selected)
    )
      setSelected(view.stacks[0]?.stack_id ?? "");
  }, [creating, history, selected, view]);
  return (
    <>
      <section
        className="panel infra-pane infra-container-list"
        aria-label="Compose 栈列表"
      >
        <div className="infra-heading">
          <h3>Compose 栈 {view ? `${view.total} 个` : ""}</h3>
          <button
            className="button"
            disabled={refresh.pending}
            aria-label="刷新栈观测"
            onClick={async () => {
              await refresh.send("control/view", {
                node_id: nodeId,
                refresh: true,
              });
              await query.reload();
            }}
          >
            刷新
          </button>
        </div>
        <div className="infra-filters">
          <label>
            搜索应用
            <input
              value={search}
              maxLength={128}
              placeholder="应用项目名"
              onChange={(event) => {
                setSearch(event.target.value);
                setPage(0);
              }}
            />
          </label>
        </div>
        <div className="dockge-actions">
          <button
            className="button"
            disabled={
              !view?.capabilities.available ||
              !view.capabilities.create.enabled ||
              !currentControl(view.observation)
            }
            onClick={() => {
              setCreating(true);
              setHistory(false);
            }}
          >
            创建应用
          </button>
          <button
            className="button"
            aria-pressed={history}
            onClick={() => {
              setCreating(false);
              setHistory(true);
            }}
          >
            节点操作记录
          </button>
        </div>
        <div
          className="infra-container-scroll"
          tabIndex={0}
          role="group"
          aria-label="应用清单内容"
        >
          {(query.error || refresh.error) && (
            <p className="infra-error" role="alert">
              {query.error || refresh.error}
            </p>
          )}
          {view && <ControlSource value={view.observation} />}
          {view?.refresh &&
            ["queued", "running"].includes(view.refresh.state) && (
              <p role="status">正在后台核对 Dockge 栈清单…</p>
            )}
          {!view && (
            <p role="status">
              {query.error ? "无法读取应用清单。" : "正在读取 Dockge 控制能力…"}
            </p>
          )}
          {view && !view.capabilities.available && (
            <p>
              {controlReasons(view.capabilities.reason_codes) ||
                "此节点的应用控制暂不可用。"}
            </p>
          )}
          {view && !view.configured && <p>部署端尚未配置 Dockge 控制。</p>}
          <div className="infra-container-rows">
            {view?.stacks.map((stack) => (
              <button
                key={stack.stack_id}
                className="infra-container-row dockge-stack-row"
                aria-pressed={
                  !creating && !history && selected === stack.stack_id
                }
                aria-label={`查看应用 ${stack.display_name}`}
                onClick={() => {
                  setCreating(false);
                  setHistory(false);
                  setSelected(stack.stack_id);
                }}
              >
                <div className="infra-row-identity">
                  <strong title={stack.display_name}>
                    {stack.display_name}
                  </strong>
                  <div className="infra-row-state">
                    <StatusRail
                      tone={
                        view.observation.freshness !== "current"
                          ? "gray"
                          : stack.runtime_state === "running"
                            ? "blue"
                            : stack.runtime_state === "partial"
                              ? "yellow"
                              : "gray"
                      }
                      label={`${view.observation.freshness === "current" ? "" : "上次 · "}${runtimeLabel(stack.runtime_state)}`}
                    />
                    <span className="infra-health">
                      {healthLabel(stack.health_state)}
                    </span>
                  </div>
                  <small title={stack.project_name}>
                    {stack.project_name} · {stack.containers.length} 个容器
                    {stack.maintenance.required ? " · 专用维护" : ""}
                  </small>
                </div>
              </button>
            ))}
          </div>
          {view?.configured &&
            view.capabilities.available &&
            !view.stacks.length &&
            !view.refresh && (
              <p>
                {view.observation.status === "ok"
                  ? "当前筛选没有应用。"
                  : "尚未取得完整应用清单。"}
              </p>
            )}
        </div>
        {view && (
          <nav className="infra-pagination" aria-label="应用分页">
            <button
              className="button"
              disabled={page === 0}
              onClick={() => setPage((value) => value - 1)}
            >
              上一页应用
            </button>
            <span>第 {page + 1} 页</span>
            <button
              className="button"
              disabled={!view.has_more}
              onClick={() => setPage((value) => value + 1)}
            >
              下一页应用
            </button>
          </nav>
        )}
      </section>
      {creating && view ? (
        <Drawer
          title={"新建应用"}
          variant="card"
          open={true}
          onClose={() => setCreating(false)}
        >
          <CreateStack key={nodeId} nodeId={nodeId} view={view} />
        </Drawer>
      ) : history ? (
        <aside
          className="panel infra-pane infra-container-detail"
          aria-label="节点操作记录"
        >
          <header className="infra-detail-heading">
            <h3>节点操作记录</h3>
          </header>
          <div
            className="infra-detail-scroll"
            tabIndex={0}
            role="group"
            aria-label="节点操作记录内容"
          >
            <OperationHistory nodeId={nodeId} />
          </div>
        </aside>
      ) : selected ? (
        <StackInspector
          key={`${nodeId}/${selected}`}
          nodeId={nodeId}
          stackId={selected}
          listedStack={
            view?.node_id === nodeId
              ? view.stacks.find((stack) => stack.stack_id === selected)
              : undefined
          }
        />
      ) : (
        <aside className="panel infra-pane infra-container-detail infra-empty">
          <h3>应用详情</h3>
          <p>选择应用查看运行、配置、版本与操作记录。</p>
        </aside>
      )}
    </>
  );
}

export function StackWorkspace({ view }: { view: InfrastructureView }) {
  const preferred =
    view.nodes.find((node) => node.enabled && node.kind !== "openwrt")?.id ??
    view.nodes[0]?.id ??
    "";
  const [nodeId, setNodeId] = useState(preferred);
  useEffect(() => {
    if (!view.nodes.some((node) => node.id === nodeId)) setNodeId(preferred);
  }, [nodeId, preferred, view.nodes]);
  return (
    <div className="infra-workspace">
      <div className="infra-heading">
        <div>
          <h2>Compose / Dockge 应用</h2>
          <p>管理已登记应用，核对配置与实际运行结果</p>
        </div>
        <a className="button" href="#/settings/nodes">
          节点连接
        </a>
      </div>
      {!nodeId ? (
        <section className="panel infra-empty">
          <h3>先添加运行节点</h3>
          <a className="button" href="#/settings/nodes">
            添加节点
          </a>
        </section>
      ) : (
        <div className="infra-docker-layout">
          <aside className="panel infra-pane infra-selector infra-docker-selector">
            <h3>运行节点</h3>
            <div
              className="infra-selector-nodes"
              aria-label="运行节点"
              role="group"
              tabIndex={0}
            >
              {view.nodes.map((node) => (
                <button
                  className="infra-node-choice"
                  key={node.id}
                  aria-pressed={nodeId === node.id}
                  onClick={() => setNodeId(node.id)}
                >
                  <strong title={node.label}>{node.label}</strong>
                  <NodeRail node={node} />
                </button>
              ))}
            </div>
            <p className="dockge-source">
              应用和容器身份分别核对。受保护服务显示专用维护归属。
            </p>
            <a href="#/system/nodes">查看节点与网络</a>
          </aside>
          <NodeStacks key={nodeId} nodeId={nodeId} />
        </div>
      )}
    </div>
  );
}
