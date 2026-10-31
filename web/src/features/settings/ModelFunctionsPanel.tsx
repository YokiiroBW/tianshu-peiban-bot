import { DetailCard } from "../../components/DetailCard";
import { useEffect, useId, useRef, useState } from "react";
import { modelLabel, type Provider } from "./providerTypes";
import type { ModelFunction } from "./providerApi";

type Props = {
  functions: ModelFunction[];
  providers: Provider[];
  save: (
    item: ModelFunction,
    provider: Provider | null,
    signal: AbortSignal,
  ) => Promise<void>;
  onError: (cause: unknown) => void;
};

function FunctionChoice({
  item,
  providers,
  save,
  onError,
}: Omit<Props, "functions"> & { item: ModelFunction }) {
  const selectId = useId();
  const [selected, setSelected] = useState(item.provider_id ?? "");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  useEffect(() => {
    setSelected(item.provider_id ?? "");
  }, [item.revision, item.provider_id]);
  const eligible = providers.filter(
    (provider) =>
      (provider.kind ?? "text") === "text" &&
      provider.sourceEnabled !== false &&
      provider.enabled &&
      provider.hasKey &&
      provider.test.state === "passed" &&
      provider.test.revision === provider.revision,
  );
  const effective = providers.find(
    (provider) => provider.providerId === item.effective_provider_id,
  );
  const chosen = eligible.find((provider) => provider.providerId === selected);
  const changed =
    selected !== (item.provider_id ?? "") ||
    (!!chosen && chosen.revision !== item.provider_revision);
  const invalidChoice = selected !== "" && !chosen;

  async function submit() {
    if (active.current || invalidChoice) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      await save(item, chosen ?? null, controller.signal);
      if (!controller.signal.aborted) setSaved(true);
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(
          cause instanceof Error ? cause.message : "保存未完成，请重试。",
        );
        onError(cause);
      }
    } finally {
      if (!controller.signal.aborted) setBusy(false);
      if (active.current === controller) active.current = null;
    }
  }

  return (
    <article className="model-function-card">
      <div>
        <h4>{item.name}</h4>
        <p className="muted">{item.description}</p>
      </div>

      <p className="model-function-effective">
        {item.configured && effective
          ? `当前：${modelLabel(effective)}`
          : item.provider_id
            ? "原模型已变更或不可用，请重新选择。"
            : "尚无可用的默认模型。"}
      </p>
      <p className="muted">
        {item.connected
          ? "已接入此功能的模型调用"
          : "可预先配置，自动调用尚未接入"}
      </p>
      <DetailCard title={`${item.name}模型设置`}>
        {error && <p role="alert">{error}</p>}
        <label htmlFor={selectId}>{item.name}模型</label>
        <select
          id={selectId}
          value={selected}
          disabled={busy}
          onChange={(event) => {
            setSelected(event.target.value);
            setSaved(false);
          }}
        >
          <option value="" disabled={item.function_id === "chat"}>
            {item.function_id === "chat" ? "请选择主对话模型" : "继承默认模型"}
          </option>
          {invalidChoice && (
            <option value={selected} disabled>
              原选择已不可用，请重新选择
            </option>
          )}
          {eligible.map((provider) => (
            <option key={provider.providerId} value={provider.providerId}>
              {modelLabel(provider)}
            </option>
          ))}
        </select>
        <div className="models-actions">
          <button
            className="button"
            disabled={busy || !changed || invalidChoice}
            onClick={() => void submit()}
          >
            {busy ? "保存中…" : `保存${item.name}模型`}
          </button>
          {saved && <span role="status">已保存</span>}
        </div>
      </DetailCard>
    </article>
  );
}

export function ModelFunctionsPanel(props: Props) {
  if (!props.functions.length) return null;
  return (
    <section className="model-functions" aria-label="模型功能分工">
      <div>
        <h3>模型功能分工</h3>
        <p className="muted">
          为不同功能选择模型，也可以共用默认模型。先在上方添加并测试所需模型，再在这里分配。
        </p>
        <p className="muted">
          角色单独选择的聊天模型仍优先生效；其他功能未单独指定时沿用角色模型或系统默认。
        </p>
      </div>
      <p className="muted">
        嵌入与重排序可在 <a href="#/settings/retrieval">记忆检索设置</a>{" "}
        选择同一供应商下的专用模型。
      </p>
      <div className="model-functions-grid">
        {props.functions.map((item) => (
          <FunctionChoice
            key={item.function_id}
            item={item}
            providers={props.providers}
            save={props.save}
            onError={props.onError}
          />
        ))}
      </div>
    </section>
  );
}
