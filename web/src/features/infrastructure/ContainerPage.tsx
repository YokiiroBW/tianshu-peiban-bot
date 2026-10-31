import { useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { StatePanel } from "../../components/StatePanel";
import { StatusRail } from "../../components/StatusRail";
import { useInfrastructureAction, useInfrastructureQuery } from "./api";
import { ContainerInspector } from "./ContainerInspector";
import { ContainerResourceMeter } from "./ContainerResourceMeter";
import { StackWorkspace } from "./dockge/StackWorkspace";
import {
  containerTone,
  explanation,
  Freshness,
  health,
  NodeRail,
  states,
  stageLabel,
  time,
  UnconfiguredInfrastructure,
} from "./presentation";
import type { ContainerList, InfrastructureView } from "./types";

function Workspace({ view }: { view: InfrastructureView }) {
  const preferred =
    view.nodes.find(
      (node) =>
        node.enabled &&
        (node.capabilities.includes("docker.inventory") ||
          view.catalog?.data?.nodes.some(
            (source) =>
              source.id === node.id &&
              source.capabilities.includes("docker.inventory"),
          )),
    )?.id ??
    view.nodes[0]?.id ??
    "";
  const [nodeId, setNodeId] = useState(preferred);
  const [application, setApplication] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [state, setState] = useState("");
  const [healthFilter, setHealthFilter] = useState("");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState("");
  const listScroll = useRef<HTMLDivElement>(null);
  const query = useInfrastructureQuery<ContainerList>(
    "containers",
    {
      node_id: nodeId,
      query: search,
      application,
      state: state || null,
      health: healthFilter || null,
      page,
    },
    Boolean(nodeId),
  );
  const action = useInfrastructureAction();
  const result = query.data;
  useEffect(() => {
    if (!view.nodes.some((node) => node.id === nodeId)) setNodeId(preferred);
  }, [nodeId, preferred, view.nodes]);
  useEffect(() => {
    setSelected("");
    setPage(0);
    setApplication(null);
  }, [nodeId]);
  useEffect(() => {
    if (
      result &&
      !result.containers.some((container) => container.id === selected)
    )
      setSelected(result.containers[0]?.id ?? "");
  }, [result, selected]);
  useEffect(() => {
    listScroll.current?.scrollTo({ top: 0 });
  }, [nodeId, application, search, state, healthFilter, page]);
  const current = result?.inventory.freshness === "current";
  if (!view.nodes.length)
    return (
      <section className="panel infra-empty">
        <h2>先添加运行节点</h2>
        <p>从部署端目录选择 NAS 或 Linux 节点，后台会持续读取容器。</p>
        <a className="button" href="#/settings/nodes">
          添加节点
        </a>
      </section>
    );
  return (
    <div className="infra-workspace scroll-page scroll-layout">
      <div className="infra-heading">
        <div>
          <h2>Docker 容器</h2>
          <p>按节点与应用查看运行、健康和资源读数</p>
        </div>
        <a className="button" href="#/settings/nodes">
          节点连接
        </a>
      </div>
      <div className="infra-docker-layout scroll-layout">
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
          <h3>Compose 应用</h3>
          <div
            className="infra-selector-apps"
            aria-label="Compose 应用"
            role="group"
            tabIndex={0}
          >
            <button
              className="infra-app-choice"
              aria-pressed={application === null}
              onClick={() => {
                setApplication(null);
                setPage(0);
              }}
            >
              全部容器
            </button>
            {result?.applications.map((app) => (
              <button
                key={app.name}
                className="infra-app-choice"
                aria-pressed={application === app.name}
                onClick={() => {
                  setApplication(app.name);
                  setPage(0);
                }}
              >
                <span title={app.name || "独立容器"}>
                  {app.name || "独立容器"}
                </span>
                <small>{app.containers} 个容器</small>
              </button>
            ))}
          </div>
          <a href="#/system/nodes">查看节点与网络</a>
        </aside>
        <section
          className="panel infra-pane infra-container-list"
          aria-label="容器列表"
        >
          <div className="infra-heading">
            <h3>
              容器清单{" "}
              {result ? (
                <span className="infra-muted">{result.total} 个</span>
              ) : (
                ""
              )}
            </h3>
            <button
              className="button"
              disabled={
                action.pending ||
                !nodeId ||
                !view.nodes.find((node) => node.id === nodeId)?.enabled
              }
              onClick={async () => {
                if (await action.send("refresh", { node_id: nodeId }))
                  await query.reload();
              }}
              aria-label="刷新容器观测"
            >
              <RefreshCw size={16} aria-hidden="true" />
            </button>
          </div>
          <div className="infra-filters">
            <label>
              搜索容器
              <input
                value={search}
                placeholder="名称、镜像或容器 ID"
                onChange={(event) => {
                  setSearch(event.target.value);
                  setPage(0);
                }}
                maxLength={128}
              />
            </label>
            <label>
              运行状态
              <select
                value={state}
                onChange={(event) => {
                  setState(event.target.value);
                  setPage(0);
                }}
              >
                <option value="">全部运行状态</option>
                {Object.entries(states).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              健康检查
              <select
                value={healthFilter}
                onChange={(event) => {
                  setHealthFilter(event.target.value);
                  setPage(0);
                }}
              >
                <option value="">全部健康状态</option>
                {Object.entries(health).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div
            className="infra-container-scroll"
            ref={listScroll}
            tabIndex={0}
            role="group"
            aria-label="容器清单内容"
          >
            {(action.error || query.error) && (
              <p className="infra-error" role="alert">
                {action.error || query.error}
              </p>
            )}
            {query.loading && !result && <p role="status">正在读取容器清单…</p>}
            {result && (
              <>
                <Freshness observation={result.inventory} />
                {result.metrics.freshness !== "current" &&
                  result.metrics.observed_at && (
                    <p className="infra-muted">
                      资源为上次读数 · {time(result.metrics.observed_at)}
                    </p>
                  )}
                {result.node.refresh &&
                  ["queued", "in_progress"].includes(
                    result.node.refresh.state,
                  ) && (
                    <p role="status">
                      后台采集中 · {stageLabel(result.node.refresh.stage)}
                    </p>
                  )}
                <div className="infra-container-rows">
                  {result.containers.map((container) => (
                    <button
                      className="infra-container-row"
                      key={`${nodeId}/${container.id}`}
                      aria-pressed={selected === container.id}
                      aria-label={`查看容器 ${container.name ?? container.id}`}
                      onClick={() => setSelected(container.id)}
                    >
                      <div className="infra-row-identity">
                        <strong title={container.name ?? container.id}>
                          {container.name ?? container.id.slice(0, 12)}
                        </strong>
                        <div className="infra-row-state">
                          <StatusRail
                            tone={containerTone(container, Boolean(current))}
                            label={`${current ? "" : "上次 · "}${states[container.state]}`}
                            selected={selected === container.id}
                          />
                          <span
                            className={`infra-health tone-${current && container.health === "unhealthy" ? "red" : "gray"}`}
                          >
                            {health[container.health]}
                          </span>
                        </div>
                        <small title={container.image_reference ?? "镜像未知"}>
                          {container.image_reference ?? "镜像未知"}
                        </small>
                      </div>
                      <div className="infra-row-resources">
                        <ContainerResourceMeter
                          kind="cpu"
                          stats={container.stats}
                        />
                        <ContainerResourceMeter
                          kind="memory"
                          stats={container.stats}
                        />
                      </div>
                    </button>
                  ))}
                </div>
                {!result.containers.length && (
                  <div className="infra-empty">
                    <h4>
                      {result.inventory.freshness === "missing"
                        ? "等待容器观测"
                        : "当前筛选没有容器"}
                    </h4>
                    <p>
                      {result.inventory.freshness === "missing"
                        ? explanation(result.inventory.reason_codes)
                        : "可以调整节点、应用或状态筛选。"}
                    </p>
                  </div>
                )}
              </>
            )}
          </div>
          {result && result.total > result.page_size && (
            <nav className="infra-pagination" aria-label="容器分页">
              <button
                className="button"
                disabled={page === 0}
                onClick={() => setPage((value) => value - 1)}
              >
                上一页
              </button>
              <span>
                第 {page + 1} 页 · 共 {result.total} 个容器
              </span>
              <button
                className="button"
                disabled={!result.has_more}
                onClick={() => setPage((value) => value + 1)}
              >
                下一页
              </button>
            </nav>
          )}
        </section>
        {selected ? (
          <ContainerInspector
            key={`${nodeId}/${selected}`}
            nodeId={nodeId}
            containerId={selected}
          />
        ) : (
          <aside className="panel infra-pane infra-container-detail infra-empty">
            <h3>容器详情</h3>
            <p>选择容器查看端口、挂载与限定日志。</p>
          </aside>
        )}
      </div>
    </div>
  );
}

export default function ContainerPage() {
  const query = useInfrastructureQuery<InfrastructureView>("view", {});
  const [mode, setMode] = useState("containers");
  if (query.loading && !query.data)
    return (
      <StatePanel kind="loading" title="正在读取运行节点">
        <p>正在读取已添加的运行节点。</p>
      </StatePanel>
    );
  if (!query.data)
    return (
      <StatePanel kind="error" title="运行节点无法读取">
        <p>{query.error}</p>
      </StatePanel>
    );
  if (!query.data.configured) return <UnconfiguredInfrastructure />;
  return (
    <div className={mode === "containers" ? "scroll-page" : undefined}>
      {query.error && (
        <p className="infra-error" role="alert">
          {query.error}
        </p>
      )}
      <nav className="infra-mode-switch" aria-label="容器管理视图">
        <button
          className="button"
          aria-pressed={mode === "containers"}
          onClick={() => setMode("containers")}
        >
          容器
        </button>
        <button
          className="button"
          aria-pressed={mode === "stacks"}
          onClick={() => setMode("stacks")}
        >
          Compose 栈
        </button>
      </nav>
      {mode === "containers" ? (
        <Workspace view={query.data} />
      ) : (
        <StackWorkspace view={query.data} />
      )}
    </div>
  );
}
