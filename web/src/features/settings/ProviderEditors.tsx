import { useState } from "react";
import { requestId } from "../../app/requestId";
import { quotaGroupName } from "./providerPresentation";
import { providerPresets } from "./providerPresets";
import {
  protocolNames,
  type Provider,
  type SourceDraft,
  type ModelRoute,
  type ModelCatalog,
  type QuotaGroup,
} from "./providerTypes";

export function SourceEditor({
  source,
  catalog,
  busy,
  save,
  cancel,
}: {
  source: Provider | null;
  catalog: ModelCatalog;
  busy: boolean;
  save: (draft: SourceDraft) => Promise<void>;
  cancel: () => void;
}) {
  const [draft, setDraft] = useState<SourceDraft>({
    provider_id: source?.provider_id ?? requestId(),
    name: source?.name ?? "",
    base_url: source?.base_url ?? "",
    protocol: source?.protocol ?? "openai.chat",
    enabled: source?.enabled ?? true,
  });
  const [key, setKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  return (
    <form
      className="provider-editor"
      onSubmit={async (event) => {
        event.preventDefault();
        await save({
          ...draft,
          ...(key ? { api_key: key } : {}),
          clear_api_key: clearKey,
        });
        setKey("");
      }}
    >
      <fieldset disabled={busy}>
        {!source && (
          <label>
            地址模板
            <select
              defaultValue="custom"
              onChange={(event) => {
                const preset = providerPresets.find(
                  (item) => item.id === event.target.value,
                )!;
                setDraft({
                  ...draft,
                  protocol: preset.protocol,
                  base_url: preset.base_url,
                });
              }}
            >
              {providerPresets.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          名称
          <input
            required
            value={draft.name}
            onChange={(event) =>
              setDraft({ ...draft, name: event.target.value })
            }
          />
        </label>
        <label>
          原生协议
          <select
            value={draft.protocol}
            onChange={(event) =>
              setDraft({
                ...draft,
                protocol: event.target.value as SourceDraft["protocol"],
              })
            }
          >
            {catalog.protocols.map((protocol) => (
              <option key={protocol} value={protocol}>
                {protocolNames[protocol]}
              </option>
            ))}
          </select>
        </label>
        <label>
          API 基础地址
          <input
            required
            type="url"
            value={draft.base_url}
            placeholder="https://api.example.com/v1"
            onChange={(event) =>
              setDraft({ ...draft, base_url: event.target.value })
            }
          />
        </label>
        <label>
          API Key
          <input
            type="password"
            autoComplete="new-password"
            value={key}
            onChange={(event) => setKey(event.target.value)}
            placeholder={
              source?.credential_configured
                ? "已保存，留空保留"
                : "填写连接凭据"
            }
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={clearKey}
            onChange={(event) => setClearKey(event.target.checked)}
          />
          清除已保存密钥
        </label>
        <label>
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(event) =>
              setDraft({ ...draft, enabled: event.target.checked })
            }
          />
          启用供应商
        </label>
        <div className="models-actions">
          <button className="button primary">保存连接</button>
          <button type="button" className="button" onClick={cancel}>
            取消
          </button>
        </div>
      </fieldset>
    </form>
  );
}

export function ModelEditor({
  route,
  providerId,
  catalog,
  busy,
  save,
  cancel,
}: {
  route: ModelRoute | null;
  providerId: string;
  catalog: ModelCatalog;
  busy: boolean;
  save: (body: object) => Promise<void>;
  cancel: () => void;
}) {
  const [draft, setDraft] = useState<ModelRoute>(
    route ?? {
      route_id: requestId(),
      provider_id: providerId,
      model: "",
      quota_group: catalog.quota_groups[0]?.quota_group ?? "",
      enabled: true,
      revision: 0,
      verified_capabilities: [],
      verification: null,
      parameters: {},
    },
  );
  const [parameters, setParameters] = useState(
    JSON.stringify(draft.parameters, null, 2),
  );
  const [capabilities, setCapabilities] = useState(
    draft.verified_capabilities.join(", "),
  );
  const [contextWindow, setContextWindow] = useState(() =>
    typeof draft.verification?.context_window_tokens === "number" ? String(draft.verification.context_window_tokens) : "",
  );
  const [evidence, setEvidence] = useState("");
  const [error, setError] = useState("");
  return (
    <form
      className="provider-editor"
      onSubmit={async (event) => {
        event.preventDefault();
        setError("");
        try {
          const parsed = JSON.parse(parameters);
          if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
            throw new Error("原生参数需要 JSON 对象。");
          const caps = capabilities.split(/[,，\s]+/).filter(Boolean);
          const windowTokens = contextWindow.trim() ? Number(contextWindow) : null;
          if (windowTokens !== null && (!Number.isSafeInteger(windowTokens) || windowTokens < 1))
            throw new Error("上下文窗口需要是正整数。");
          const targetChanged =
            !!route &&
            (draft.model !== route.model ||
              draft.provider_id !== route.provider_id ||
              JSON.stringify(parsed) !== JSON.stringify(route.parameters));
          const changed =
            targetChanged ||
            caps.join(",") !== (route?.verified_capabilities ?? []).join(",") ||
            windowTokens !== (route?.verification?.context_window_tokens ?? null);
          const verified = targetChanged && !evidence.trim() ? [] : caps;
          if (verified.length && changed && !evidence.trim())
            throw new Error("请填写实际能力依据，或留空能力等待真实测试。");
          await save({
            ...draft,
            parameters: parsed,
            verified_capabilities: verified,
            verification: !verified.length
              ? !changed && !targetChanged
                ? (route?.verification ?? null)
                : windowTokens === null || (targetChanged && !evidence.trim())
                  ? null
                  : { source: "operator", note: evidence.trim(), observed_at: new Date().toISOString(), context_window_tokens: windowTokens }
              : changed
                ? {
                    ...(!targetChanged ? route?.verification ?? {} : {}),
                    source: "operator",
                    note: evidence.trim(),
                    observed_at: new Date().toISOString(),
                    context_window_tokens: windowTokens,
                  }
                : (route?.verification ?? null),
          });
        } catch (cause) {
          setError(cause instanceof Error ? cause.message : "请检查表单");
        }
      }}
    >
      <fieldset disabled={busy}>
        <label>
          模型 ID
          <input
            required
            value={draft.model}
            onChange={(event) =>
              setDraft({ ...draft, model: event.target.value })
            }
          />
        </label>
        <label>
          共享容量组
          <select
            required
            value={draft.quota_group}
            onChange={(event) =>
              setDraft({ ...draft, quota_group: event.target.value })
            }
          >
            <option value="">选择已配置容量组</option>
            {catalog.quota_groups.map((group) => (
              <option key={group.quota_group} value={group.quota_group}>{quotaGroupName(catalog, group.quota_group)}</option>
            ))}
          </select>
        </label>
        <p className="muted">
          同一真实账号配额应使用同一容量组；模型 ID 和 API Key
          不会自动建立独立额度。
        </p>
        <label>
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(event) =>
              setDraft({ ...draft, enabled: event.target.checked })
            }
          />
          启用路由
        </label>
        <details>
          <summary>原生参数与能力依据</summary>
          <label>
            供应商原生参数
            <textarea
              rows={6}
              value={parameters}
              onChange={(event) => setParameters(event.target.value)}
            />
          </label>
          <label>
            已核验能力（逗号分隔）
            <input
              value={capabilities}
              placeholder="例如 text, tools, stream, vision"
              onChange={(event) => setCapabilities(event.target.value)}
            />
          </label>
          <label>
            已核验上下文窗口 Token（可留空）
            <input type="number" min={1} value={contextWindow} onChange={(event) => setContextWindow(event.target.value)} />
          </label>
          <label>
            本次修改能力的实际依据
            <textarea
              rows={3}
              value={evidence}
              onChange={(event) => setEvidence(event.target.value)}
            />
          </label>
          <p className="muted">
            配置与实际验证分别记录；文字/流式可用下方真实测试，多模态能力需要对应材料与依据。备用路由还需要可证明的上下文容量；JSON 用途需要已核验 json 能力。
          </p>
        </details>
        {error && <p role="alert">{error}</p>}
        <div className="models-actions">
          <button className="button primary">保存模型路由</button>
          <button type="button" className="button" onClick={cancel}>
            取消
          </button>
        </div>
      </fieldset>
    </form>
  );
}

const initialQuota: QuotaGroup = {
  quota_group: "",
  revision: 0,
  concurrency: 2,
  cloud_concurrency: null,
  interactive_reserve: 1,
  background_max: 1,
  rpm: null,
  tpm: null,
  max_queue: 128,
  max_wait_seconds: null,
  unknown_hold_seconds: 300,
  unit_limits: {},
  interactive_rpm_reserve: 0,
  background_rpm_max: null,
  interactive_unit_reserve: {},
  background_unit_max: {},
};
export function QuotaEditor({
  original,
  busy,
  save,
}: {
  original: QuotaGroup | null;
  busy: boolean;
  save: (body: QuotaGroup) => Promise<void>;
}) {
  const [value, setValue] = useState(original ?? initialQuota);
  const [units, setUnits] = useState(
    JSON.stringify(
      {
        unit_limits: value.unit_limits,
        interactive_unit_reserve: value.interactive_unit_reserve,
        background_unit_max: value.background_unit_max,
      },
      null,
      2,
    ),
  );
  const [error, setError] = useState("");
  const fields: {
    key: keyof QuotaGroup;
    label: string;
    optional?: boolean;
    min: number;
  }[] = [
    { key: "concurrency", label: "外部资源并发", min: 1 },
    { key: "cloud_concurrency", label: "云请求并发上限（可留空）", optional: true, min: 1 },
    { key: "interactive_reserve", label: "即时并发预留", min: 0 },
    { key: "background_max", label: "后台并发上限", min: 0 },
    { key: "rpm", label: "每分钟请求上限", optional: true, min: 1 },
    { key: "tpm", label: "每分钟 token 上限", optional: true, min: 1 },
    { key: "interactive_rpm_reserve", label: "即时请求预留", min: 0 },
    {
      key: "background_rpm_max",
      label: "后台请求上限",
      optional: true,
      min: 0,
    },
    { key: "max_queue", label: "排队上限", min: 1 },
    {
      key: "max_wait_seconds",
      label: "排队过期秒数（可留空）",
      optional: true,
      min: 1,
    },
    { key: "unknown_hold_seconds", label: "未知结果占用保留秒数", min: 1 },
  ];
  return (
    <form
      className="provider-editor"
      onSubmit={async (event) => {
        event.preventDefault();
        setError("");
        try {
          const parsed = JSON.parse(units);
          if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
            throw new Error("分单位额度需要 JSON 对象。");
          const selected: Pick<
            QuotaGroup,
            "unit_limits" | "interactive_unit_reserve" | "background_unit_max"
          > = {
            unit_limits: {},
            interactive_unit_reserve: {},
            background_unit_max: {},
          };
          for (const key of [
            "unit_limits",
            "interactive_unit_reserve",
            "background_unit_max",
          ] as const) {
            const limits = parsed[key] ?? {};
            if (
              !limits ||
              typeof limits !== "object" ||
              Array.isArray(limits) ||
              Object.values(limits).some(
                (number) =>
                  typeof number !== "number" ||
                  !Number.isFinite(number) ||
                  number < 0,
              )
            )
              throw new Error("各单位额度需要非负数字对象。");
            selected[key] = limits;
          }
          await save({ ...value, ...selected });
        } catch (cause) {
          setError(cause instanceof Error ? cause.message : "额度配置格式有误");
        }
      }}
    >
      <fieldset disabled={busy}>
        <label>
          容量组标识
          <input
            required
            readOnly={!!original}
            value={value.quota_group}
            onChange={(event) =>
              setValue({ ...value, quota_group: event.target.value })
            }
          />
        </label>
        <div className="model-quota-fields">
          {fields.map((field) => (
            <label key={field.key}>
              {field.label}
              <input
                type="number"
                required={!field.optional}
                min={field.min}
                value={(value[field.key] as number | null) ?? ""}
                onChange={(event) =>
                  setValue({
                    ...value,
                    [field.key]:
                      event.target.value === "" && field.optional
                        ? null
                        : Number(event.target.value),
                  })
                }
              />
            </label>
          ))}
        </div>
        <p className="muted">
          云请求上限留空时不单独限制该组的云并发；外部资源并发仍单独生效。排队过期留空时不设置等待时限；填写后只限制排队等待。模型执行超时从获得额度并开始执行后计算，任务自身的业务到期仍然有效。
        </p>
        <details>
          <summary>分单位额度与预留</summary>
          <label>
            tokens、utf8_bytes、audio_seconds、requests 等单位
            <textarea
              rows={7}
              value={units}
              onChange={(event) => setUnits(event.target.value)}
            />
          </label>
        </details>
        {error && <p role="alert">{error}</p>}
        <button className="button primary">保存容量组</button>
      </fieldset>
    </form>
  );
}
