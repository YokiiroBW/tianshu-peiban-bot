import { useState, type FormEvent } from "react";
import { providerPresets } from "./providerPresets";
import {
  modelKindNames,
  type Provider,
  type ProviderSource,
  type SourceDraft,
  type ModelDraft,
  type ModelKind,
} from "./providerTypes";

export function SourceEditor({
  source,
  busy,
  save,
  cancel,
}: {
  source: ProviderSource | null;
  busy: boolean;
  save: (draft: SourceDraft) => void;
  cancel: () => void;
}) {
  const [draft, setDraft] = useState<SourceDraft>({
    name: source?.name ?? "",
    baseUrl: source?.baseUrl ?? "",
    apiKey: "",
    enabled: source?.enabled ?? true,
  });
  return (
    <form
      className="provider-editor"
      aria-label="供应商连接设置"
      onSubmit={(e) => {
        e.preventDefault();
        save(draft);
      }}
    >
      <h4>{source ? "编辑供应商" : "添加供应商"}</h4>
      <fieldset disabled={busy}>
        {!source && (
          <label>
            服务预设
            <select
              defaultValue="custom"
              onChange={(e) => {
                const preset = providerPresets.find(
                  (p) => p.id === e.target.value,
                )!;
                setDraft({
                  ...draft,
                  name: preset.id === "custom" ? "" : preset.name,
                  baseUrl: preset.baseUrl,
                });
              }}
            >
              {providerPresets.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          供应商名称
          <input
            required
            maxLength={120}
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            placeholder="例如：我的中转站"
          />
        </label>
        <label>
          API 基础地址
          <input
            required
            type="url"
            pattern="https://.*"
            value={draft.baseUrl}
            onChange={(e) => setDraft({ ...draft, baseUrl: e.target.value })}
            placeholder="https://api.example.com/v1"
          />
        </label>
        <label>
          API Key
          <input
            type="password"
            autoComplete="new-password"
            value={draft.apiKey}
            onChange={(e) => setDraft({ ...draft, apiKey: e.target.value })}
            placeholder={source?.hasKey ? "已保存，留空保留" : "填写 API Key"}
          />
        </label>
        <p className="muted">
          此供应商下的所有模型共用这份地址和密钥。密钥只在后端加密保存。
        </p>
        {source && (
          <p className="muted">
            修改地址或密钥后，所含模型需要重新测试和选择；仅改名称不影响使用。
          </p>
        )}
        <div className="models-actions">
          <button className="button primary" type="submit">
            保存供应商
          </button>
          <button className="button" type="button" onClick={cancel}>
            取消
          </button>
        </div>
      </fieldset>
    </form>
  );
}

export function ModelEditor({
  model,
  busy,
  save,
  cancel,
}: {
  model: Provider;
  busy: boolean;
  save: (draft: ModelDraft) => void;
  cancel: () => void;
}) {
  const [draft, setDraft] = useState<ModelDraft>({
    name: model.name,
    modelId: model.modelId,
    kind: model.kind ?? "text",
    enabled: model.enabled,
  });
  return (
    <form
      className="provider-editor"
      aria-label="模型设置"
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        save(draft);
      }}
    >
      <fieldset disabled={busy}>
        <label>
          显示名称
          <input
            required
            maxLength={120}
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          />
        </label>
        <label>
          模型 ID
          <input
            required
            maxLength={256}
            value={draft.modelId}
            onChange={(e) => setDraft({ ...draft, modelId: e.target.value })}
          />
        </label>
        <label>
          模型类型
          <select
            value={draft.kind}
            onChange={(e) =>
              setDraft({ ...draft, kind: e.target.value as ModelKind })
            }
          >
            {Object.entries(modelKindNames).map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <div className="models-actions">
          <button className="button primary" type="submit">
            保存模型设置
          </button>
          <button className="button" type="button" onClick={cancel}>
            取消
          </button>
        </div>
      </fieldset>
    </form>
  );
}
