import { DetailCard } from "../../components/DetailCard";
import { useEffect, useRef, useState } from "react";
import {
  integrationPost,
  readFailure,
  writeFailure,
} from "../../app/integrationApi";
import { requestId } from "../../app/requestId";
import { readComfy, manageComfy, compileComfy } from "./comfyApi";
import { ImageDimensions } from "./ImageDimensions";
import { ComfyCompilePreview } from "./ComfyCompilePreview";
import { showState, type LifeAccess } from "./runtimeApi";
import {
  comfySemanticLabels,
  type ComfyActor,
  type ComfyBinding,
  type ComfyBindings,
  type ComfyCompile,
  type ComfyResponse,
  type ComfyStatus,
  type ComfyWorkflow,
  type ComfyWorkflowList,
} from "./comfyTypes";

type CredentialStatus = {
  revision: number | null;
  credential_configured: boolean;
};
type Analysis = ComfyWorkflow & {
  model_receipt?: Record<string, unknown> | null;
  selection_reason?: string;
};

const targetKey = (binding: ComfyBinding) =>
  JSON.stringify([binding.node, binding.class_type, binding.input]);

function BindingEditor({
  workflow,
  bindings,
  onChange,
}: {
  workflow: ComfyWorkflow;
  bindings: ComfyBindings;
  onChange: (bindings: ComfyBindings) => void;
}) {
  return (
    <div className="comfy-bindings">
      {Object.entries(comfySemanticLabels).map(([semantic, label]) => {
        const selected = bindings[semantic];
        const candidates = [...(workflow.candidates?.[semantic] ?? [])];
        if (
          selected &&
          !candidates.some((item) => targetKey(item) === targetKey(selected))
        )
          candidates.unshift(selected);
        const options = candidates.filter(
          (item, index) =>
            candidates.findIndex(
              (row) => targetKey(row) === targetKey(item),
            ) === index,
        );
        return (
          <div className="comfy-binding" key={semantic}>
            <label>
              {label}节点
              <select
                aria-label={`${label}节点`}
                value={selected ? targetKey(selected) : ""}
                onChange={(event) => {
                  const value = options.find(
                    (item) => targetKey(item) === event.target.value,
                  );
                  const next = { ...bindings };
                  if (value) next[semantic] = { ...value };
                  else delete next[semantic];
                  onChange(next);
                }}
              >
                <option value="">未绑定 · 不写入</option>
                {options.map((binding) => {
                  const node = workflow.nodes.find(
                    (item) => item.node_id === binding.node,
                  );
                  return (
                    <option key={targetKey(binding)} value={targetKey(binding)}>
                      {node?.title || binding.class_type} · {binding.node} /{" "}
                      {binding.input}
                    </option>
                  );
                })}
              </select>
            </label>
            {selected &&
              [
                "character",
                "outfit",
                "pose",
                "background",
                "camera",
                "positive",
                "negative",
              ].includes(semantic) && (
                <label>
                  {label}写入方式
                  <select
                    aria-label={`${label}写入方式`}
                    value={selected.mode}
                    onChange={(event) =>
                      onChange({
                        ...bindings,
                        [semantic]: {
                          ...selected,
                          mode: event.target.value as "replace" | "append",
                        },
                      })
                    }
                  >
                    <option value="replace">替换此字段</option>
                    <option value="append">保留原文并追加</option>
                  </select>
                </label>
              )}
          </div>
        );
      })}
    </div>
  );
}

export function ComfyBackendPanel({
  access,
  refreshed,
}: {
  access: LifeAccess;
  refreshed: () => void;
}) {
  const [status, setStatus] = useState<ComfyStatus | null>(null);
  const [actor, setActor] = useState<ComfyActor | null>(null);
  const [credentials, setCredentials] = useState<CredentialStatus | null>(null);
  const [url, setUrl] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [token, setToken] = useState("");
  const [clearCredential, setClearCredential] = useState(false);
  const [workflows, setWorkflows] = useState<ComfyWorkflowList | null>(null);
  const [selection, setSelection] = useState("");
  const [workflow, setWorkflow] = useState<Analysis | null>(null);
  const [bindings, setBindings] = useState<ComfyBindings>({});
  const [character, setCharacter] = useState("");
  const [outfit, setOutfit] = useState("");
  const [pose, setPose] = useState("");
  const [background, setBackground] = useState("");
  const [camera, setCamera] = useState("");
  const [positive, setPositive] = useState("");
  const [negative, setNegative] = useState("");
  const [width, setWidth] = useState(768);
  const [height, setHeight] = useState(1024);
  const [goal, setGoal] = useState("");
  const [assist, setAssist] = useState(false);
  const [preview, setPreview] = useState<ComfyCompile | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const current = useRef<AbortController | null>(null);
  const pending = useRef<{ semantic: string; id: string } | null>(null);
  const previewIdentity = JSON.stringify([
    character,
    outfit,
    pose,
    background,
    camera,
    positive,
    negative,
    width,
    height,
    assist,
  ]);
  const latestPreviewIdentity = useRef(previewIdentity);
  latestPreviewIdentity.current = previewIdentity;

  function applyActor(record: ComfyActor) {
    setActor(record);
    setCharacter(record.character_prompt);
    setOutfit(record.defaults.outfit ?? "");
    setPose(record.defaults.pose ?? "");
    setBackground(record.defaults.background ?? "");
    setCamera(record.defaults.camera ?? "");
    setPositive(record.defaults.positive ?? "");
    setNegative(record.defaults.negative ?? "");
    setWidth(record.defaults.width ?? 768);
    setHeight(record.defaults.height ?? 1024);
    setSelection(record.workflow_id ?? "");
  }
  function applyWorkflow(record: Analysis) {
    setWorkflow(record);
    setSelection(record.workflow_id);
    setBindings(record.bindings);
    setPreview(null);
  }
  async function reload(signal: AbortSignal, initial = false) {
    const results = await Promise.allSettled([
      readComfy<ComfyStatus>(access, "status", signal),
      readComfy<ComfyActor>(access, "actor", signal),
      integrationPost<CredentialStatus>(
        "life/image-backend/status",
        { actor_id: access.actor },
        access.csrf,
        signal,
      ),
    ]);
    if (signal.aborted) return;
    const [connection, profile, catalog] = results;
    if (connection.status === "fulfilled") {
      setStatus(connection.value);
      setUrl(connection.value.base_url ?? "");
      setEnabled(connection.value.enabled);
    }
    if (profile.status === "fulfilled") {
      if (initial) applyActor(profile.value);
      else setActor(profile.value);
    }
    if (catalog.status === "fulfilled") setCredentials(catalog.value);
    const failure = results.find((result) => result.status === "rejected");
    if (failure?.status === "rejected") throw failure.reason;
    return connection.status === "fulfilled" ? connection.value : null;
  }
  useEffect(() => {
    const control = new AbortController();
    current.current = control;
    setBusy(true);
    void reload(control.signal, true)
      .then(async (connection) => {
        if (
          control.signal.aborted ||
          !connection?.base_url ||
          !connection.enabled
        )
          return;
        const discovered = await readComfy<ComfyWorkflowList>(
          access,
          "workflows",
          control.signal,
        );
        if (!control.signal.aborted) setWorkflows(discovered);
        if (!control.signal.aborted && connection.workflow_id) {
          const result = await readComfy<ComfyWorkflow>(
            access,
            "workflow",
            control.signal,
            connection.workflow_id,
          );
          if (!control.signal.aborted) applyWorkflow(result);
        }
      })
      .catch((cause) => {
        if (!control.signal.aborted) setError(readFailure(cause));
      })
      .finally(() => {
        if (!control.signal.aborted) setBusy(false);
      });
    return () => control.abort();
  }, [access.actor, access.csrf]);

  async function perform(
    task: (signal: AbortSignal) => Promise<void>,
    write = false,
  ) {
    if (busy) return;
    const control = new AbortController();
    current.current?.abort();
    current.current = control;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await task(control.signal);
    } catch (cause) {
      if (!control.signal.aborted)
        setError(write ? writeFailure(cause) : readFailure(cause));
    } finally {
      if (!control.signal.aborted) setBusy(false);
    }
  }
  function operationId(semantic: unknown) {
    const text = JSON.stringify(semantic);
    if (pending.current?.semantic !== text)
      pending.current = { semantic: text, id: requestId() };
    return pending.current.id;
  }
  async function manage(operation: string, value: object, signal: AbortSignal) {
    const version = actor?.version ?? 1;
    await manageComfy(
      access,
      operation,
      value,
      version,
      operationId([operation, value, version]),
      signal,
    );
    if (signal.aborted) return;
    pending.current = null;
    const [profile, connection] = await Promise.all([
      readComfy<ComfyActor>(access, "actor", signal),
      readComfy<ComfyStatus>(access, "status", signal),
    ]);
    if (signal.aborted) return;
    // Preserve unsaved character defaults when saving only workflow/node bindings.
    setActor(profile);
    setStatus(connection);
    setPreview(null);
    refreshed();
  }

  return (
    <DetailCard title={"图像供应商与工作流设置"}>
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      <section
        className="panel comfy-backend life-span"
        aria-label="ComfyUI 图像管理"
      >
        <h3>图像供应商与工作流</h3>
        <p className="muted">
          当前角色：{access.actor}。每位角色独立绑定工作流与形象。
        </p>
        <div className="comfy-sections">
          <form
            className="life-form"
            onSubmit={(event) => {
              event.preventDefault();
              void perform(async (signal) => {
                const body = {
                  actor_id: access.actor,
                  value: { base_url: url, enabled },
                  credential: clearCredential
                    ? { action: "clear" }
                    : token
                      ? { action: "replace", value: token }
                      : { action: "keep" },
                  catalog_revision: credentials?.revision ?? 0,
                  expected_revision: status?.version ?? 0,
                };
                await integrationPost<ComfyResponse<unknown>>(
                  "life/image-backend/save",
                  { ...body, client_request_id: operationId(body) },
                  access.csrf,
                  signal,
                );
                if (signal.aborted) return;
                pending.current = null;
                setToken("");
                setClearCredential(false);
                await reload(signal);
                if (!signal.aborted) {
                  setWorkflows(
                    await readComfy<ComfyWorkflowList>(
                      access,
                      "workflows",
                      signal,
                    ),
                  );
                  setMessage("连接配置已保存，已读取实际工作流目录。");
                  refreshed();
                }
              }, true);
            }}
          >
            <h4>服务连接</h4>
            <label>
              图像供应商
              <select value="comfyui" onChange={() => {}}>
                <option value="comfyui">ComfyUI</option>
              </select>
            </label>
            <p className="muted">NAI 与在线图像 API 尚未接入。</p>
            <p>连接状态：{showState(status?.state ?? "not_configured")}</p>
            {status?.checked_at && (
              <p className="muted">
                最近检查：
                {new Date(status.checked_at * 1000).toLocaleString("zh-CN")}
              </p>
            )}
            {status?.error_code && (
              <p className="error-text">{status.error_code}</p>
            )}
            <label>
              ComfyUI 地址
              <input
                type="url"
                required
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                placeholder="http://服务器:8188"
              />
            </label>
            <label>
              访问凭据
              <input
                type="password"
                autoComplete="new-password"
                disabled={clearCredential}
                value={token}
                onChange={(event) => setToken(event.target.value)}
                placeholder={
                  credentials?.credential_configured
                    ? "已保存，留空保留"
                    : "无认证的后端可留空"
                }
              />
            </label>
            <label className="life-check">
              <input
                type="checkbox"
                checked={clearCredential}
                onChange={(event) => setClearCredential(event.target.checked)}
              />
              清除连接访问凭据
            </label>
            <label className="life-check">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(event) => setEnabled(event.target.checked)}
              />
              启用此连接
            </label>
            <div className="life-action-row">
              <button className="button primary" disabled={busy}>
                保存连接并读取工作流
              </button>
              <button
                type="button"
                className="button"
                disabled={busy}
                onClick={() =>
                  void perform(async (signal) => {
                    await reload(signal);
                    refreshed();
                  })
                }
              >
                检查实际连接
              </button>
            </div>
          </form>

          <div className="life-form">
            <h4>工作流发现与适配</h4>
            <button
              className="button"
              disabled={busy}
              onClick={() =>
                void perform(async (signal) => {
                  const result = await readComfy<ComfyWorkflowList>(
                    access,
                    "workflows",
                    signal,
                  );
                  if (!signal.aborted) setWorkflows(result);
                })
              }
            >
              读取工作流目录
            </button>
            {workflows && (
              <p className="muted">
                ComfyUI 工作流目录 · {workflows.items.length} 个模板
              </p>
            )}
            {workflows?.items.length === 0 && (
              <p>目录为空；在 ComfyUI 保存工作流后重新读取。</p>
            )}
            <label>
              选择工作流
              <select
                value={selection}
                disabled={busy}
                onChange={(event) => {
                  const id = event.target.value;
                  setSelection(id);
                  setWorkflow(null);
                  setBindings({});
                  setPreview(null);
                  if (id)
                    void perform(async (signal) => {
                      const result = await readComfy<ComfyWorkflow>(
                        access,
                        "workflow",
                        signal,
                        id,
                      );
                      if (!signal.aborted) applyWorkflow(result);
                    });
                }}
              >
                <option value="">选择已发现模板</option>
                {actor?.workflow_id &&
                  !workflows?.items.some(
                    (item) => item.id === actor.workflow_id,
                  ) && (
                    <option value={actor.workflow_id}>
                      {actor.workflow_id} · 已绑定
                    </option>
                  )}
                {workflows?.items.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            {actor?.workflow_id && <p>当前角色已绑定：{actor.workflow_id}</p>}
            <label>
              选图与适配需求
              <textarea
                value={goal}
                onChange={(event) => setGoal(event.target.value)}
                maxLength={2000}
                placeholder="例如：用于角色竖幅生活照，保持原画风，分别调整穿搭、动作和背景。"
              />
            </label>
            <div className="life-action-row">
              <button
                className="button"
                disabled={busy || !goal.trim()}
                onClick={() =>
                  void perform(async (signal) => {
                    const result = await manageComfy<Analysis>(
                      access,
                      "workflow.analyze",
                      { workflow_id: null, goal, assist_model: true },
                      actor?.version ?? 1,
                      requestId(),
                      signal,
                    );
                    if (!signal.aborted) applyWorkflow(result);
                  })
                }
              >
                按需求帮我选工作流
              </button>
              <button
                className="button"
                disabled={busy || !selection || !goal.trim()}
                onClick={() =>
                  void perform(async (signal) => {
                    const result = await manageComfy<Analysis>(
                      access,
                      "workflow.analyze",
                      { workflow_id: selection, goal, assist_model: true },
                      actor?.version ?? 1,
                      requestId(),
                      signal,
                    );
                    if (!signal.aborted) applyWorkflow(result);
                  })
                }
              >
                模型辅助适配所选工作流
              </button>
            </div>
          </div>
        </div>

        {workflow && (
          <div className="life-record">
            <h4>节点识别与绑定 · {workflow.workflow_id}</h4>
            <p>
              {workflow.format === "ui" ? "ComfyUI 界面工作流" : "API 工作流"} ·{" "}
              {workflow.nodes.length} 个节点 ·{" "}
              {workflow.state === "ready" ? "可以适配" : "需要调整工作流"}
            </p>
            {workflow.selection_reason && <p>{workflow.selection_reason}</p>}
            {workflow.model_receipt && (
              <p>模型辅助分析已完成，请核对下方字段。</p>
            )}
            {workflow.unresolved.length > 0 && (
              <ul className="error-text">
                {workflow.unresolved.map((item, index) => (
                  <li key={index}>
                    {item.node_id ? `节点 ${item.node_id}：` : ""}
                    {item.detail}（{item.code}）
                  </li>
                ))}
              </ul>
            )}
            <BindingEditor
              workflow={workflow}
              bindings={bindings}
              onChange={(value) => {
                setBindings(value);
                setPreview(null);
              }}
            />
            <p className="muted">
              受保护节点：
              {workflow.protected_nodes
                .map((item) => `${item.class_type} · ${item.node_id}`)
                .join("、") || "无"}
            </p>
            <div className="life-action-row">
              <button
                className="button primary"
                disabled={busy || workflow.state !== "ready"}
                onClick={() =>
                  void perform(async (signal) => {
                    await manage(
                      "workflow.select",
                      { workflow_id: workflow.workflow_id, bindings },
                      signal,
                    );
                    if (!signal.aborted)
                      setMessage("此工作流与节点绑定已保存到当前角色。");
                  }, true)
                }
              >
                将工作流绑定到当前角色
              </button>
              {actor?.workflow_id === workflow.workflow_id && (
                <button
                  className="button"
                  disabled={busy || workflow.state !== "ready"}
                  onClick={() =>
                    void perform(async (signal) => {
                      await manage("bindings.update", { bindings }, signal);
                      if (!signal.aborted)
                        setMessage("当前角色的节点修正已保存。");
                    }, true)
                  }
                >
                  保存节点修正
                </button>
              )}
            </div>
            <DetailCard title="查看节点与转换依据">
              <p>{workflow.conversion.strategy}</p>
              <pre>
                {JSON.stringify(
                  {
                    nodes: workflow.nodes,
                    evidence: workflow.conversion.evidence,
                  },
                  null,
                  2,
                )}
              </pre>
            </DetailCard>
          </div>
        )}

        <form
          className="life-form comfy-character"
          onSubmit={(event) => {
            event.preventDefault();
            void perform(async (signal) => {
              await manage(
                "actor.configure",
                {
                  workflow_id: actor?.workflow_id ?? null,
                  character_prompt: character,
                  defaults: {
                    outfit,
                    pose,
                    background,
                    camera,
                    positive,
                    negative,
                    width,
                    height,
                  },
                },
                signal,
              );
              if (!signal.aborted)
                setMessage("当前角色的形象与拍摄默认值已保存。");
            }, true);
          }}
        >
          <h4>角色形象与拍摄默认值</h4>
          <p className="muted">
            形象与拍摄字段留空时，沿用所选工作流原有内容。
          </p>
          <label>
            固定角色形象
            <textarea
              value={character}
              onChange={(event) => {
                setCharacter(event.target.value);
                setPreview(null);
              }}
              maxLength={2000}
              placeholder="外貌、发色、眼睛等固定特征"
            />
          </label>
          <div className="comfy-sections">
            <label>
              默认穿搭
              <input
                value={outfit}
                onChange={(event) => {
                  setOutfit(event.target.value);
                  setPreview(null);
                }}
                maxLength={2000}
              />
            </label>
            <label>
              默认姿态与动作
              <input
                value={pose}
                onChange={(event) => {
                  setPose(event.target.value);
                  setPreview(null);
                }}
                maxLength={2000}
              />
            </label>
            <label>
              默认背景与场景
              <input
                value={background}
                onChange={(event) => {
                  setBackground(event.target.value);
                  setPreview(null);
                }}
                maxLength={2000}
              />
            </label>
            <label>
              默认镜头与构图
              <input
                value={camera}
                onChange={(event) => {
                  setCamera(event.target.value);
                  setPreview(null);
                }}
                maxLength={2000}
                placeholder="半身、全身、远景等"
              />
            </label>
            <label>
              默认补充细节
              <input
                value={positive}
                onChange={(event) => {
                  setPositive(event.target.value);
                  setPreview(null);
                }}
                maxLength={2000}
              />
            </label>
            <label>
              默认排除细节
              <input
                value={negative}
                onChange={(event) => {
                  setNegative(event.target.value);
                  setPreview(null);
                }}
                maxLength={2000}
              />
            </label>
          </div>
          <ImageDimensions
            width={width}
            height={height}
            onChange={(w, h) => {
              setWidth(w);
              setHeight(h);
              setPreview(null);
            }}
          />
          <label className="life-check">
            <input
              type="checkbox"
              checked={assist}
              onChange={(event) => {
                setAssist(event.target.checked);
                setPreview(null);
              }}
            />
            预览时使用角色模型转译提示词
          </label>
          <div className="life-action-row">
            <button className="button primary" disabled={busy}>
              保存角色默认值
            </button>
            <button
              type="button"
              className="button"
              disabled={busy}
              onClick={() =>
                void perform(async (signal) => {
                  const result = await compileComfy(
                    access,
                    {
                      character,
                      outfit,
                      pose,
                      background,
                      camera,
                      positive,
                      negative,
                    },
                    { width, height },
                    assist,
                    signal,
                  );
                  if (
                    !signal.aborted &&
                    latestPreviewIdentity.current === previewIdentity
                  )
                    setPreview(result);
                })
              }
            >
              编译预览角色拍摄
            </button>
          </div>
        </form>
        {busy && <p role="status">正在核对 ComfyUI 与当前角色配置…</p>}
        {message && <p role="status">{message}</p>}
        {error && (
          <p role="alert" className="error-text">
            {error}
          </p>
        )}
        {preview && <ComfyCompilePreview result={preview} />}
      </section>
    </DetailCard>
  );
}
