import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { StatePanel } from "../../components/StatePanel";
import { useInfrastructureAction, useInfrastructureQuery } from "./api";
import {
  bytes,
  duration,
  explanation,
  Freshness,
  kinds,
  NodeRail,
  percent,
  rate,
  stageLabel,
  UnconfiguredInfrastructure,
} from "./presentation";
import type { InfrastructureView, NodeDetail, Snapshot } from "./types";

function Router({ snapshot }: { snapshot: Snapshot }) {
  const router = snapshot.openwrt;
  if (!router) return <p>尚未取得 OpenWrt 系统与网络接口观测。</p>;
  return (
    <section className="infra-section" aria-labelledby="router-title">
      <h3 id="router-title">OpenWrt 与网络</h3>
      <dl className="infra-metrics">
        <div>
          <dt>设备型号</dt>
          <dd>{router.board?.model ?? "—"}</dd>
        </div>
        <div>
          <dt>固件版本</dt>
          <dd>
            {router.board?.release?.description ??
              router.board?.release?.version ??
              "—"}
          </dd>
        </div>
      </dl>
      {router.reason_codes.length > 0 && (
        <p className="infra-muted">{explanation(router.reason_codes)}</p>
      )}
      <div className="infra-network-grid">
        {router.interfaces.map((item, index) => {
          const counter = snapshot.interfaces.find(
            (row) => row.name === (item.l3_device ?? item.device),
          );
          const addresses = [...item.ipv4_addresses, ...item.ipv6_addresses];
          return (
            <article className="infra-card" key={`${item.name}-${index}`}>
              <h4>{item.name}</h4>
              <p>
                {item.pending
                  ? "连接处理中"
                  : item.up === true
                    ? "接口已连接"
                    : item.up === false
                      ? "接口未连接"
                      : "接口状态未知"}{" "}
                · {item.protocol ?? "协议未知"}
              </p>
              <p className="infra-mono">
                {addresses.length
                  ? addresses
                      .map(
                        (address) =>
                          `${address.address}${address.mask == null ? "" : `/${address.mask}`}`,
                      )
                      .join(" · ")
                  : "暂无地址观测"}
              </p>
              <p>
                设备 {item.l3_device ?? item.device ?? "—"} · 运行{" "}
                {duration(item.uptime_seconds)}
              </p>
              <p>
                接收 {rate(counter?.receive_bytes_per_second)} · 发送{" "}
                {rate(counter?.transmit_bytes_per_second)}
              </p>
              <small>这是接口总流量，包含该接口承载的通信。</small>
            </article>
          );
        })}
      </div>
      {!router.interfaces.length && <p>尚无可用的 WAN／LAN 接口观测。</p>}
      <p className="infra-muted">
        逐终端流量尚未接入；接口流量不能作为每台终端的流量或在线证明。
      </p>
    </section>
  );
}

function Host({ snapshot }: { snapshot: Snapshot }) {
  return (
    <>
      <dl className="infra-metrics">
        <div>
          <dt>CPU · 整机</dt>
          <dd>{percent(snapshot.cpu.usage_ratio)}</dd>
          <dd className="infra-metric-note">
            <small>
              {snapshot.cpu.state === "ok"
                ? "占全部主机 CPU"
                : explanation(snapshot.cpu.reason_codes)}
            </small>
          </dd>
        </div>
        <div>
          <dt>已用内存</dt>
          <dd>{bytes(snapshot.memory.used_bytes)}</dd>
          <dd className="infra-metric-note">
            <small>
              总计 {bytes(snapshot.memory.total_bytes)} · 可用{" "}
              {bytes(snapshot.memory.available_bytes)}
            </small>
          </dd>
        </div>
        <div>
          <dt>运行时长</dt>
          <dd>{duration(snapshot.system.uptime_seconds)}</dd>
        </div>
        <div>
          <dt>负载均值 · 1 / 5 / 15 分</dt>
          <dd>
            {snapshot.system.load_average
              ?.map((item) => item.toFixed(2))
              .join(" / ") ?? "—"}
          </dd>
          <dd className="infra-metric-note">
            <small>负载均值独立于 CPU 使用率</small>
          </dd>
        </div>
      </dl>
      <section className="infra-section">
        <h3>磁盘与文件系统</h3>
        {snapshot.filesystems.length ? (
          <div className="infra-table-scroll">
            <table className="infra-table">
              <thead>
                <tr>
                  <th>挂载点</th>
                  <th>容量</th>
                  <th>已用</th>
                  <th>可用</th>
                </tr>
              </thead>
              <tbody>
                {snapshot.filesystems.map((item) => (
                  <tr key={item.id}>
                    <th>
                      <span className="infra-mono">{item.mountpoint}</span>
                      <small>{item.device}</small>
                    </th>
                    <td>{bytes(item.total_bytes)}</td>
                    <td>{bytes(item.used_bytes)}</td>
                    <td>{bytes(item.available_bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p>尚未取得文件系统容量，不能据此判断磁盘为空。</p>
        )}
      </section>
      <section className="infra-section">
        <h3>网络接口</h3>
        {snapshot.interfaces.length ? (
          <div className="infra-table-scroll">
            <table className="infra-table">
              <thead>
                <tr>
                  <th>接口</th>
                  <th>接收 / 发送速率</th>
                  <th>累计接收 / 发送</th>
                  <th>错误 / 丢包</th>
                </tr>
              </thead>
              <tbody>
                {snapshot.interfaces.map((item) => (
                  <tr key={item.id}>
                    <th>
                      {item.name}
                      <small>
                        {item.state !== "ok"
                          ? explanation(item.reason_codes)
                          : "连续采样"}
                      </small>
                    </th>
                    <td>
                      {rate(item.receive_bytes_per_second)} /{" "}
                      {rate(item.transmit_bytes_per_second)}
                    </td>
                    <td>
                      {bytes(item.receive_bytes)} / {bytes(item.transmit_bytes)}
                    </td>
                    <td>
                      {item.receive_errors + item.transmit_errors} /{" "}
                      {item.receive_drops + item.transmit_drops}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p>尚未取得接口计数器。</p>
        )}
      </section>
    </>
  );
}

function Detail({ identity }: { identity: string }) {
  const query = useInfrastructureQuery<NodeDetail>(
    "node",
    { node_id: identity },
    Boolean(identity),
  );
  const action = useInfrastructureAction();
  const node = query.data;
  if (query.loading && !node)
    return (
      <StatePanel kind="loading" title="正在读取节点观测">
        <p>正在读取后台缓存。</p>
      </StatePanel>
    );
  if (!node)
    return (
      <StatePanel kind="error" title="节点观测无法读取">
        <p>{query.error}</p>
      </StatePanel>
    );
  const snapshot = node.snapshot.data;
  const pending =
    node.refresh && ["queued", "in_progress"].includes(node.refresh.state);
  return (
    <section
      className="panel infra-pane infra-node-detail"
      aria-label={`${node.label}节点详情`}
    >
      <div className="infra-heading">
        <div>
          <p className="infra-eyebrow">{kinds[node.kind] ?? node.kind}</p>
          <h2>{node.label}</h2>
          <NodeRail node={node} />
        </div>
        <button
          className="button"
          disabled={action.pending || !node.enabled || Boolean(pending)}
          onClick={async () => {
            if (await action.send("refresh", { node_id: node.id }))
              await query.reload();
          }}
        >
          <RefreshCw size={16} aria-hidden="true" />
          刷新观测
        </button>
      </div>
      {(action.error || query.error) && (
        <p role="alert" className="infra-error">
          {action.error || query.error}
        </p>
      )}
      {pending && (
        <p role="status">后台采集中 · {stageLabel(node.refresh?.stage)}</p>
      )}
      <Freshness observation={node.snapshot} />
      {snapshot ? (
        <>
          <p className="infra-muted">
            {snapshot.identity.hostname ?? "主机名未知"} ·{" "}
            {snapshot.identity.kernel ?? "系统版本未知"}
          </p>
          <Host snapshot={snapshot} />
          {node.kind === "openwrt" && <Router snapshot={snapshot} />}
        </>
      ) : (
        <div className="infra-empty">
          <h3>等待节点读数</h3>
          <p>{explanation(node.snapshot.reason_codes)}</p>
        </div>
      )}
      {node.kind !== "openwrt" && (
        <section className="infra-section">
          <h3>Compose 期望与实际</h3>
          <Freshness observation={node.compose} />
          {node.compose.data?.stacks.map((stack) => (
            <article className="infra-card" key={stack.id}>
              <h4>
                {stack.name}
                <span className="infra-tag">
                  {{
                    in_sync: "已核对一致",
                    drifted: "存在差异",
                    unavailable: "尚无法核对",
                  }[stack.drift_status] ?? stack.drift_status}
                </span>
              </h4>
              {stack.services.map((service) => (
                <p key={service.name}>
                  {service.name} · 期望 {service.desired_instances ?? "未知"}{" "}
                  个实例 / 观测 {service.observed_container_ids.length} 个容器 ·{" "}
                  {{
                    in_sync: "一致",
                    drifted: "有差异",
                    unavailable: "未确认",
                  }[service.drift_status] ?? service.drift_status}
                  {service.findings.length
                    ? ` · ${explanation(service.findings)}`
                    : ""}
                </p>
              ))}
              {stack.runtime_only_services.length > 0 && (
                <p>
                  只在运行清单出现：{stack.runtime_only_services.join("、")}
                </p>
              )}
              {stack.reason_codes.length > 0 && (
                <p>{explanation(stack.reason_codes)}</p>
              )}
            </article>
          ))}
          {!node.compose.data?.stacks.length && (
            <p className="infra-muted">当前没有可展示的登记栈对照。</p>
          )}
        </section>
      )}
      <p className="infra-muted">
        读数由服务端持续采集；当前尚未接入历史趋势。
      </p>
    </section>
  );
}

export default function NodePage() {
  const query = useInfrastructureQuery<InfrastructureView>("view", {});
  const [selected, setSelected] = useState("");
  const view = query.data;
  useEffect(() => {
    if (view && !view.nodes.some((node) => node.id === selected))
      setSelected(view.nodes[0]?.id ?? "");
  }, [selected, view]);
  if (query.loading && !view)
    return (
      <StatePanel kind="loading" title="正在读取节点目录">
        <p>正在读取已添加的节点。</p>
      </StatePanel>
    );
  if (!view)
    return (
      <StatePanel kind="error" title="节点目录无法读取">
        <p>{query.error}</p>
      </StatePanel>
    );
  if (!view.configured) return <UnconfiguredInfrastructure />;
  return (
    <div className="infra-workspace scroll-page">
      <div className="infra-heading">
        <div>
          <h2>节点与网络</h2>
          <p>NAS、Linux 与 OpenWrt 的主机观测</p>
        </div>
        <a className="button" href="#/settings/nodes">
          管理节点连接
        </a>
      </div>
      {query.error && <p role="alert">{query.error}</p>}
      {view.nodes.length ? (
        <div className="infra-node-layout scroll-layout">
          <aside
            className="panel infra-pane infra-selector scroll-pane"
            aria-label="运行节点"
            tabIndex={0}
          >
            {view.nodes.map((node) => (
              <button
                key={node.id}
                className="infra-node-choice"
                aria-pressed={selected === node.id}
                onClick={() => setSelected(node.id)}
              >
                <strong>{node.label}</strong>
                <small>{kinds[node.kind]}</small>
                <NodeRail node={node} />
              </button>
            ))}
          </aside>
          {selected && (
            <div
              key={selected}
              className="scroll-pane"
              role="region"
              aria-label="节点详情"
              tabIndex={0}
            >
              <Detail identity={selected} />
            </div>
          )}
        </div>
      ) : (
        <section className="panel infra-empty">
          <h3>还没有添加节点</h3>
          <p>到节点连接设置，从部署端目录选择 NAS 或路由器。</p>
          <a className="button" href="#/settings/nodes">
            添加节点
          </a>
        </section>
      )}
      <section className="panel infra-pane">
        <h3>游戏服</h3>
        <p className="infra-muted">
          游戏服状态与管理尚未接入。节点读数不代表游戏服在线。
        </p>
      </section>
    </div>
  );
}
