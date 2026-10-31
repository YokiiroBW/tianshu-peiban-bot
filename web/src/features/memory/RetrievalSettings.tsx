import { DetailCard } from "../../components/DetailCard";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "../../app/Auth";
import { integrationPost, readFailure } from "../../app/integrationApi";
import { requestId } from "../../app/requestId";

import "./retrieval.css";

type Kind = "embed" | "rerank";
type Policy = {
  mode: "keyword" | "vector" | "hybrid";
  rerank: boolean;
  candidates: number;
};
type Connection = {
  base_url: string;
  model_id: string;
  has_key: boolean;
  provider_id?: string;
};
type View = {
  models?: {
    provider_id: string;
    name: string;
    model_id: string;
    kind: Kind;
    available: boolean;
  }[];
  revision: number;
  can_manage: boolean;
  policy: Policy;
  connections: Record<Kind, Connection>;
};
const names: Record<Kind, string> = { embed: "嵌入", rerank: "重排序" };

export function RetrievalSettings() {
  const { session } = useAuth();
  const csrf = session?.csrf ?? "";
  const [view, setView] = useState<View | null>(null);
  const [policy, setPolicy] = useState<Policy>({
    mode: "keyword",
    rerank: false,
    candidates: 40,
  });
  const [connections, setConnections] = useState<Record<Kind, Connection>>({
    embed: { base_url: "", model_id: "", has_key: false },
    rerank: { base_url: "", model_id: "", has_key: false },
  });
  const [selected, setSelected] = useState({ embed: "", rerank: "" });
  const [keys, setKeys] = useState({ embed: "", rerank: "" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);

  function apply(next: View) {
    setView(next);
    setPolicy(next.policy);
    setConnections(next.connections);
    setSelected({
      embed: next.connections.embed.provider_id ?? "",
      rerank: next.connections.rerank.provider_id ?? "",
    });
  }
  useEffect(() => {
    const controller = new AbortController();
    active.current = controller;
    setView(null);
    setKeys({ embed: "", rerank: "" });
    integrationPost<View>("memory-retrieval/state", {}, csrf, controller.signal)
      .then((next) => {
        if (!controller.signal.aborted) apply(next);
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) setError(readFailure(cause));
      })
      .finally(() => {
        if (active.current === controller) active.current = null;
      });
    return () => {
      controller.abort();
      active.current?.abort();
    };
  }, [csrf]);

  async function run(
    kind: Kind | "policy",
    test = false,
    clear = false,
    reference = false,
  ) {
    if (!view || active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      if (test) {
        await integrationPost(
          "memory-retrieval/test",
          { kind },
          csrf,
          controller.signal,
        );
        if (!controller.signal.aborted)
          setMessage(`${names[kind as Kind]}连接测试通过。`);
      } else {
        const value =
          kind === "policy"
            ? policy
            : reference
              ? { provider_id: selected[kind] }
              : {
                  base_url: connections[kind].base_url.trim(),
                  model_id: connections[kind].model_id.trim(),
                };
        const credential =
          kind === "policy" || reference
            ? { action: "keep" }
            : clear
              ? { action: "clear" }
              : keys[kind]
                ? { action: "replace", value: keys[kind] }
                : { action: "keep" };
        const next = await integrationPost<View>(
          "memory-retrieval/save",
          {
            kind,
            value,
            credential,
            expected_revision: view.revision,
            client_request_id: requestId(),
          },
          csrf,
          controller.signal,
        );
        if (!controller.signal.aborted) {
          setView(next);
          if (kind === "policy") setPolicy(next.policy);
          else {
            setConnections((current) => ({
              ...current,
              [kind]: next.connections[kind],
            }));
            setKeys((current) => ({ ...current, [kind]: "" }));
            setSelected((current) => ({
              ...current,
              [kind]: next.connections[kind].provider_id ?? "",
            }));
          }
          setMessage("已保存。");
        }
      }
    } catch (cause) {
      if (!controller.signal.aborted) setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
      if (active.current === controller) active.current = null;
    }
  }

  return (
    <div className="retrieval-settings">
      <section className="panel" aria-label="记忆检索设置">
        <h2>记忆检索</h2>
        <p>为所有角色选择记忆查找方式。人物范围、来源和遗忘规则始终生效。</p>
        {error && <p role="alert">{error}</p>}
        {message && <p role="status">{message}</p>}
        {!view ? (
          <p>{error ? "请刷新页面重试。" : "正在读取设置…"}</p>
        ) : (
          <DetailCard title={"检索模式设置"}>
            {error && <p role="alert">{error}</p>}
            {message && <p role="status">{message}</p>}
            <fieldset disabled={busy || !view.can_manage}>
              <label>
                检索模式{" "}
                <select
                  aria-label="检索模式"
                  value={policy.mode}
                  onChange={(e) =>
                    setPolicy({
                      ...policy,
                      mode: e.target.value as Policy["mode"],
                    })
                  }
                >
                  <option value="keyword">关键词</option>
                  <option value="vector">向量语义</option>
                  <option value="hybrid">混合：关键词＋向量</option>
                </select>
              </label>
              <p className="muted">
                关键词适合准确名称；向量适合理解不同说法；混合同时使用两种方式。
              </p>
              <label>
                <input
                  type="checkbox"
                  checked={policy.rerank}
                  onChange={(e) =>
                    setPolicy({ ...policy, rerank: e.target.checked })
                  }
                />
                启用重排序
              </label>
              <p className="muted">
                重排序对已找到的候选再次筛选，可搭配任一检索模式。
              </p>
              <label>
                候选记忆数量{" "}
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={policy.candidates}
                  onChange={(e) =>
                    setPolicy({ ...policy, candidates: Number(e.target.value) })
                  }
                />
              </label>
              <button className="button" onClick={() => void run("policy")}>
                保存检索模式
              </button>
            </fieldset>
          </DetailCard>
        )}
      </section>
      {view &&
        (["embed", "rerank"] as const).map((kind) => (
          <section
            className="panel"
            key={kind}
            aria-label={`${names[kind]}模型连接`}
          >
            <h3>{names[kind]}模型</h3>
            <DetailCard title={`${names[kind]}模型设置`}>
              {error && <p role="alert">{error}</p>}
              {message && <p role="status">{message}</p>}
              <fieldset disabled={busy || !view.can_manage}>
                <p className="muted">
                  从 <a href="#/settings/models">模型供应商</a> 已添加的{names[kind]}
                  模型中选择，共用供应商的地址和密钥。
                </p>
                <label>
                  {names[kind]}模型选择
                  <select
                    aria-label={`${names[kind]}模型选择`}
                    value={selected[kind]}
                    onChange={(e) =>
                      setSelected({ ...selected, [kind]: e.target.value })
                    }
                  >
                    <option value="">请选择模型</option>
                    {selected[kind] &&
                      !(view.models ?? []).some(
                        (m) => m.provider_id === selected[kind],
                      ) && (
                        <option value={selected[kind]} disabled>
                          原模型已不可用
                        </option>
                      )}
                    {(view.models ?? [])
                      .filter((m) => m.kind === kind)
                      .map((m) => (
                        <option
                          key={m.provider_id}
                          value={m.provider_id}
                          disabled={!m.available}
                        >
                          {m.name}
                          {m.available ? "" : "（不可用）"}
                        </option>
                      ))}
                  </select>
                </label>
                <p className="muted">
                  当前：
                  {view.connections[kind].provider_id
                    ? ((view.models ?? []).find(
                        (m) =>
                          m.provider_id === view.connections[kind].provider_id,
                      )?.name ?? "原模型已不可用")
                    : view.connections[kind].model_id
                      ? `${view.connections[kind].model_id}（原独立连接）`
                      : "尚未配置"}
                </p>
                <div className="models-actions">
                  <button
                    className="button"
                    disabled={!selected[kind]}
                    onClick={() => void run(kind, false, false, true)}
                  >
                    保存{names[kind]}模型
                  </button>
                  <button
                    className="button"
                    disabled={!view.connections[kind].has_key}
                    onClick={() => void run(kind, true)}
                  >
                    测试已保存模型
                  </button>
                </div>
                <p className="muted">
                  测试使用短样本，可能产生费用。更换嵌入模型后，记忆向量会按新模型重新计算。
                </p>
              </fieldset>
            </DetailCard>
            {!view.connections[kind].provider_id && (
              <DetailCard title="原独立连接设置">
                {error && <p role="alert">{error}</p>}
                {message && <p role="status">{message}</p>}
                <fieldset disabled={busy || !view.can_manage}>
                  <label>
                    API 基础地址{" "}
                    <input
                      aria-label={`${names[kind]} API 基础地址`}
                      type="url"
                      value={connections[kind].base_url}
                      placeholder="https://api.example.com/v1"
                      onChange={(e) =>
                        setConnections({
                          ...connections,
                          [kind]: {
                            ...connections[kind],
                            base_url: e.target.value,
                          },
                        })
                      }
                    />
                  </label>
                  <label>
                    模型名称{" "}
                    <input
                      aria-label={`${names[kind]}模型名称`}
                      value={connections[kind].model_id}
                      onChange={(e) =>
                        setConnections({
                          ...connections,
                          [kind]: {
                            ...connections[kind],
                            model_id: e.target.value,
                          },
                        })
                      }
                    />
                  </label>
                  <label>
                    API Key{" "}
                    <input
                      aria-label={`${names[kind]} API Key`}
                      type="password"
                      autoComplete="new-password"
                      value={keys[kind]}
                      placeholder={
                        connections[kind].has_key
                          ? "已保存，留空保留"
                          : "填写 API Key"
                      }
                      onChange={(e) =>
                        setKeys({ ...keys, [kind]: e.target.value })
                      }
                    />
                  </label>
                  <p className="muted">
                    {kind === "embed"
                      ? "使用兼容 /embeddings 的浮点向量接口。"
                      : "使用兼容 /rerank 的重排序接口。"}
                    保存后可测试连接。
                  </p>
                  <div className="models-actions">
                    <button className="button" onClick={() => void run(kind)}>
                      保存{names[kind]}连接
                    </button>
                    <button
                      className="button"
                      disabled={!connections[kind].has_key}
                      onClick={() => void run(kind, true)}
                    >
                      测试{names[kind]}连接
                    </button>
                    <button
                      className="button"
                      disabled={!connections[kind].has_key}
                      onClick={() => void run(kind, false, true)}
                    >
                      清除{names[kind]}密钥
                    </button>
                  </div>
                </fieldset>
              </DetailCard>
            )}
          </section>
        ))}
    </div>
  );
}
