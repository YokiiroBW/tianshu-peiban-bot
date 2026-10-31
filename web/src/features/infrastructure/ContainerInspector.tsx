import { Boxes } from "lucide-react";
import { StatePanel } from "../../components/StatePanel";
import { StatusRail } from "../../components/StatusRail";
import { useInfrastructureAction, useInfrastructureQuery } from "./api";
import { ContainerResourceMeter } from "./ContainerResourceMeter";
import {
  bytes,
  containerTone,
  explanation,
  Freshness,
  health,
  rate,
  states,
  time,
} from "./presentation";
import type { ContainerDetail, LogView } from "./types";
import { ContainerControls } from "./dockge/ContainerControls";
function Logs({ node, container }: { node: string; container: string }) {
  const query = useInfrastructureQuery<LogView>("logs", {
    node_id: node,
    container_id: container,
  });
  const action = useInfrastructureAction();
  const logs = query.data;
  const pending = logs && ["queued", "in_progress"].includes(logs.state);
  return (
    <section className="infra-section">
      <div className="infra-heading">
        <h3>最近日志</h3>
        <button
          className="button"
          disabled={action.pending || Boolean(pending)}
          onClick={async () => {
            if (
              await action.send("logs/refresh", {
                node_id: node,
                container_id: container,
              })
            )
              await query.reload();
          }}
        >
          读取最近日志
        </button>
      </div>
      <p className="infra-muted">
        最近 1 小时 · 最多 200 行 / 64
        KiB。此处是预览，完整日志以已登记的日志服务为准。
      </p>
      {(query.error || action.error) && (
        <p role="alert" className="infra-error">
          {action.error || query.error}
        </p>
      )}
      {pending && <p role="status">日志读取中</p>}
      {logs?.state === "failed" && (
        <p role="alert">{explanation(logs.reason_codes)}</p>
      )}
      {logs?.data && (
        <>
          <p>
            读取时间 · {time(logs.observed_at)}
            {logs.data.truncated && (
              <strong className="infra-warning"> · 已截断预览</strong>
            )}
          </p>
          <pre className="infra-logs" tabIndex={0} aria-label="容器日志预览">
            {logs.data.stdout || logs.data.stderr
              ? `${logs.data.stdout}${logs.data.stderr ? `\n[stderr]\n${logs.data.stderr}` : ""}`
              : "本次时间范围内没有日志行。"}
          </pre>
        </>
      )}
      {logs?.state === "not_requested" && (
        <p className="infra-muted">点击读取此容器的限定日志。</p>
      )}
    </section>
  );
}

export function ContainerInspector({
  nodeId,
  containerId,
}: {
  nodeId: string;
  containerId: string;
}) {
  const query = useInfrastructureQuery<ContainerDetail>("container", {
    node_id: nodeId,
    container_id: containerId,
  });
  const result = query.data;
  if (query.loading && !result)
    return (
      <section className="panel infra-pane infra-container-detail infra-detail-state">
        <StatePanel kind="loading" title="正在读取容器详情">
          <p>正在读取这个容器的后台观测。</p>
        </StatePanel>
      </section>
    );
  if (!result)
    return (
      <section className="panel infra-pane infra-container-detail infra-detail-state">
        <StatePanel kind="error" title="容器详情无法读取">
          <p>{query.error}</p>
          <p>容器可能已重建或移除，请从最新清单重新选择。</p>
        </StatePanel>
      </section>
    );
  const { container, stats } = result;
  const readableStats = stats?.availability === "ok" ? stats : null;
  const current = result.inventory.freshness === "current";
  return (
    <aside
      className="panel infra-pane infra-container-detail"
      aria-label="容器详情"
    >
      <header className="infra-detail-heading">
        <div className="infra-heading">
          <div>
            <p className="infra-eyebrow">容器详情</p>
            <h3 title={container.name ?? container.id}>
              {container.name ?? container.id.slice(0, 12)}
            </h3>
          </div>
          <Boxes size={22} aria-hidden="true" />
        </div>
        <StatusRail
          tone={containerTone(container, current)}
          label={`${current ? "" : "上次状态 · "}${states[container.state] ?? "未知"}`}
        />
        <p
          className={`infra-health tone-${current && container.health === "unhealthy" ? "red" : "gray"}`}
        >
          健康检查 · {health[container.health]}
        </p>
      </header>
      <div
        className="infra-detail-scroll"
        tabIndex={0}
        role="group"
        aria-label="容器详情内容"
      >
        <ContainerControls
          nodeId={nodeId}
          containerId={containerId}
          revision={result.node.connection_revision}
          current={current}
        />
        {query.error && (
          <p role="alert" className="infra-error">
            {query.error}
          </p>
        )}
        <Freshness observation={result.inventory} />
        <dl className="infra-detail-list">
          <div>
            <dt>名称</dt>
            <dd>{container.name ?? "—"}</dd>
          </div>
          <div>
            <dt>节点 / 应用</dt>
            <dd>
              {result.node.label} / {container.compose?.project ?? "独立容器"}
            </dd>
          </div>
          <div>
            <dt>服务</dt>
            <dd>{container.compose?.service ?? "—"}</dd>
          </div>
          <div>
            <dt>镜像</dt>
            <dd className="infra-mono">{container.image_reference ?? "—"}</dd>
          </div>
          <div>
            <dt>容器身份</dt>
            <dd className="infra-mono">{container.id}</dd>
          </div>
          <div>
            <dt>启动时间</dt>
            <dd>{time(container.started_at)}</dd>
          </div>
          <div>
            <dt>网络模式</dt>
            <dd>{container.network_mode ?? "未知"}</dd>
          </div>
        </dl>
        <section className="infra-section">
          <h3>资源读数</h3>
          <Freshness observation={result.metrics} />
          <div className="infra-resource-readings">
            <ContainerResourceMeter kind="cpu" stats={stats} detailed />
            <ContainerResourceMeter kind="memory" stats={stats} detailed />
          </div>
          <dl className="infra-metrics infra-small-metrics">
            <div>
              <dt>网络接收 / 发送</dt>
              <dd>
                {rate(readableStats?.rates?.receive_bytes_per_second)} /{" "}
                {rate(readableStats?.rates?.transmit_bytes_per_second)}
              </dd>
              <dd className="infra-metric-note">
                <small>
                  {readableStats?.network?.attribution === "not_attributable"
                    ? "共享网络命名空间，不能归属单个容器"
                    : `累计 ${bytes(readableStats?.network?.receive_bytes)} / ${bytes(readableStats?.network?.transmit_bytes)}`}
                </small>
              </dd>
            </div>
            <div>
              <dt>磁盘读取 / 写入</dt>
              <dd>
                {rate(readableStats?.rates?.read_bytes_per_second)} /{" "}
                {rate(readableStats?.rates?.write_bytes_per_second)}
              </dd>
              <dd className="infra-metric-note">
                <small>
                  累计 {bytes(readableStats?.block_io?.read_bytes)} /{" "}
                  {bytes(readableStats?.block_io?.write_bytes)}
                </small>
              </dd>
            </div>
          </dl>
          {readableStats?.rates?.state &&
            readableStats.rates.state !== "ok" && (
              <p className="infra-muted">
                {explanation([readableStats.rates.state])}
              </p>
            )}
        </section>
        <section className="infra-section">
          <h3>端口</h3>
          {container.ports.length ? (
            <ul className="infra-compact-list">
              {container.ports.map((port, index) => (
                <li key={index} className="infra-mono">
                  {port.host_port == null
                    ? "未发布"
                    : `${port.host_ip ?? "所有地址"}:${port.host_port}`}{" "}
                  → {port.container_port}/{port.protocol}
                </li>
              ))}
            </ul>
          ) : (
            <p className="infra-muted">本次观测未列出发布端口。</p>
          )}
        </section>
        <section className="infra-section">
          <h3>挂载与持久数据</h3>
          {container.mounts.length ? (
            <ul className="infra-compact-list">
              {container.mounts.map((mount, index) => (
                <li key={index}>
                  <strong>
                    {mount.kind} ·{" "}
                    {mount.read_only == null
                      ? "读写权限未知"
                      : mount.read_only
                        ? "只读"
                        : "可写"}
                  </strong>
                  <span className="infra-mono">
                    {mount.source ?? "来源未知"} → {mount.target}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="infra-muted">本次观测未列出挂载。</p>
          )}
        </section>
        <Logs
          key={`${nodeId}/${containerId}`}
          node={nodeId}
          container={containerId}
        />
      </div>
    </aside>
  );
}
