import { DetailCard } from "../../components/DetailCard";
import { useEffect, useState, type FormEvent } from "react";
import { RefreshCw, Server } from "lucide-react";
import { requestId } from "../../app/requestId";
import { StatePanel } from "../../components/StatePanel";
import { useInfrastructureAction, useInfrastructureQuery } from "./api";
import {
  explanation,
  Freshness,
  kinds,
  NodeRail,
  stageLabel,
  time,
  UnconfiguredInfrastructure,
} from "./presentation";
import type { InfrastructureView } from "./types";
import "./infrastructure.css";

const capabilityNames: Record<string, string> = {
  "node.snapshot": "主机",
  "docker.inventory": "容器清单",
  "docker.stats": "容器资源",
  "docker.logs": "限定日志",
  "compose.inventory": "Compose 对照",
};
function describeCapabilities(items: string[]) {
  return (
    items.map((item) => capabilityNames[item] ?? item).join("、") ||
    "能力待采样"
  );
}

export function NodeConnections() {
  const query = useInfrastructureQuery<InfrastructureView>("view", {});
  const action = useInfrastructureAction();
  const [selected, setSelected] = useState("");
  const [label, setLabel] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [notice, setNotice] = useState("");
  const [draftRevision, setDraftRevision] = useState<number | null>(null);
  const view = query.data;
  const catalog = view?.catalog?.data?.nodes ?? [];
  const registered = view?.nodes.find((node) => node.id === selected);
  const options = [
    ...catalog,
    ...(view?.nodes.filter(
      (node) => !catalog.some((item) => item.id === node.id),
    ) ?? []),
  ];
  function choose(id: string) {
    setSelected(id);
    const row = view?.nodes.find((node) => node.id === id);
    const source = catalog.find((node) => node.id === id);
    setLabel(row?.label ?? source?.label ?? "");
    setEnabled(row?.enabled ?? true);
    setDraftRevision(view?.revision ?? null);
    setNotice("");
  }
  useEffect(() => {
    if (!selected && options.length) {
      const first = options[0];
      setSelected(first.id);
      setLabel(first.label);
      setDraftRevision(view?.revision ?? null);
      setEnabled(
        view?.nodes.find((node) => node.id === first.id)?.enabled ?? true,
      );
    }
  }, [options, selected, view]);
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!view || draftRevision === null) return;
    const result = await action.send<{ revision: number }>("save", {
      node_id: selected,
      label,
      enabled,
      expected_revision: draftRevision,
      client_request_id: requestId(),
    });
    if (result) {
      setDraftRevision(result.revision);
      setNotice(
        enabled ? "节点已保存，后台正在取得读数。" : "已暂停这个节点的采集。",
      );
      await query.reload();
    }
  }
  async function test() {
    if (await action.send("test", { node_id: selected })) {
      setNotice("已安排连接测试，请查看下方采集状态与检测时间。");
      await query.reload();
    }
  }
  if (query.loading && !view)
    return (
      <StatePanel kind="loading" title="正在读取节点连接">
        <p>正在读取已登记的节点目录。</p>
      </StatePanel>
    );
  if (!view)
    return (
      <StatePanel kind="error" title="节点连接无法读取">
        <p>{query.error}</p>
      </StatePanel>
    );
  if (!view.configured) return <UnconfiguredInfrastructure />;
  const source = catalog.find((node) => node.id === selected);
  const inProgress =
    registered?.refresh &&
    ["queued", "in_progress"].includes(registered.refresh.state);
  return (
    <div className="infra-workspace">
      <section className="panel infra-pane">
        <div className="infra-heading">
          <div>
            <h2>节点连接</h2>
            <p>选择已由部署端登记的节点，管理名称与后台采集。</p>
          </div>
          <Server aria-hidden="true" />
        </div>
        {(query.error || action.error) && (
          <p role="alert" className="infra-error">
            {action.error || query.error}
          </p>
        )}
        {notice && (
          <p role="status" className="infra-notice">
            {notice}
          </p>
        )}
        <div className="infra-toolbar">
          <button
            className="button"
            disabled={action.pending}
            onClick={async () => {
              if (await action.send("catalog/refresh", {}))
                await query.reload();
            }}
          >
            <RefreshCw size={16} aria-hidden="true" />
            刷新节点目录
          </button>
          {view.catalog?.refreshing && <span role="status">目录读取中</span>}
        </div>
        {view.catalog && <Freshness observation={view.catalog} />}
        {!options.length ? (
          <div className="infra-empty">
            <h3>还没有可选择的节点</h3>
            <p>
              {view.catalog?.status === "unavailable"
                ? "节点执行器暂不可用。请部署管理员核对执行器安装与固定运行配置，再刷新目录。"
                : "部署端尚未登记 SSH 节点。登记完成后，刷新目录即可选择。"}
            </p>
          </div>
        ) : (
          <DetailCard title={"节点连接设置"}>
            {action.error && <p role="alert">{action.error}</p>}
            {notice && <p role="status">{notice}</p>}
            <form className="infra-form" onSubmit={(event) => void save(event)}>
              {draftRevision !== null && draftRevision !== view.revision && (
                <p className="infra-warning">
                  连接设置已在其他页面改变，请重新读取后核对再保存。
                </p>
              )}
              <label>
                已配置的 SSH 节点
                <select
                  value={selected}
                  onChange={(event) => choose(event.target.value)}
                >
                  {options.map((node) => (
                    <option value={node.id} key={node.id}>
                      {node.label} · {kinds[node.kind] ?? node.kind}
                      {" · "}
                      {describeCapabilities(node.capabilities)}
                      {view.nodes.some((item) => item.id === node.id)
                        ? " · 已添加"
                        : ""}
                    </option>
                  ))}
                </select>
              </label>
              <p className="infra-muted">
                {source
                  ? `可用能力：${describeCapabilities(source.capabilities)}`
                  : "此节点已不在当前目录中，可暂停采集；重新启用需由部署端恢复登记。"}
              </p>
              <label>
                显示名称
                <input
                  value={label}
                  maxLength={128}
                  required
                  onChange={(event) => setLabel(event.target.value)}
                />
              </label>
              <label className="infra-checkbox">
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(event) => setEnabled(event.target.checked)}
                />
                启用后台采集
              </label>
              <div className="infra-toolbar">
                <button
                  className="button primary"
                  type="submit"
                  disabled={
                    action.pending ||
                    !label.trim() ||
                    !selected ||
                    (enabled && !source)
                  }
                >
                  保存节点
                </button>
                <button
                  className="button"
                  type="button"
                  disabled={action.pending}
                  onClick={async () => {
                    const next = await query.reload();
                    if (!next) return;
                    const row = next.nodes.find((node) => node.id === selected);
                    const source = next.catalog?.data?.nodes.find(
                      (node) => node.id === selected,
                    );
                    setLabel(row?.label ?? source?.label ?? "");
                    setEnabled(row?.enabled ?? true);
                    setDraftRevision(next.revision);
                    setNotice("已重新读取节点设置，请核对后保存。");
                  }}
                >
                  重新读取设置
                </button>
                <button
                  className="button"
                  type="button"
                  disabled={action.pending || !registered?.enabled}
                  onClick={() => void test()}
                >
                  测试连接
                </button>
                <a className="button" href="#/system/nodes">
                  查看节点
                </a>
              </div>
            </form>
          </DetailCard>
        )}
        {registered && (
          <section className="infra-receipt" aria-label="节点连接测试结果">
            <NodeRail node={registered} />
            {inProgress && (
              <p role="status">
                后台采集中 · {stageLabel(registered.refresh?.stage)}
              </p>
            )}
            <p>
              最近测试：{time(registered.last_test?.checked_at)}
              {registered.last_test
                ? ` · ${registered.last_test.state === "connected" ? "已取得节点观测" : "未能取得节点观测"}`
                : ""}
            </p>
            {registered.last_test && (
              <p className="infra-muted">
                {explanation([registered.last_test.code])}
              </p>
            )}
            <p>最近观测：{time(registered.observed_at)}</p>
          </section>
        )}
      </section>
    </div>
  );
}
