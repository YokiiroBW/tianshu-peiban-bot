import { DetailCard } from "../../components/DetailCard";
import { Drawer } from "../../components/Drawer";
import { useEffect, useRef, useState } from "react";
import { StatusRail } from "../../components/StatusRail";
import { compatibleModels } from "./providerPresets";
import { SourceEditor, ModelEditor } from "./ProviderEditors";
import {
  modelKindNames,
  type Provider,
  type ProviderSource,
  type ProviderActions,
  type ModelKind,
} from "./providerTypes";

export function ProviderManager({
  providers,
  sources,
  editable,
  actions,
  onError,
}: {
  providers: Provider[];
  sources: ProviderSource[];
  editable: boolean;
  actions: ProviderActions;
  onError: (cause: unknown) => void;
}) {
  const [selected, setSelected] = useState("");
  const source = sources.find((s) => s.sourceId === selected) ?? sources[0];
  const [editor, setEditor] = useState<"new" | "edit" | null>(null);
  const [modelEditor, setModelEditor] = useState("");
  const [directory, setDirectory] = useState<{
    sourceId: string;
    revision: number;
    items: string[];
  } | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [manual, setManual] = useState("");
  const [kind, setKind] = useState<ModelKind>("text");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState("");
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  useEffect(() => {
    setDirectory(null);
    setPicked([]);
    setConfirm("");
    setModelEditor("");
    setSearch("");
    setManual("");
  }, [source?.sourceId, source?.revision]);
  const models = providers.filter((p) => p.sourceId === source?.sourceId);
  const listed =
    directory &&
    source &&
    directory.sourceId === source.sourceId &&
    directory?.revision === source?.revision
      ? directory.items
      : [];
  const available = (
    kind === "text" ? compatibleModels(source?.baseUrl ?? "", listed) : listed
  ).filter(
    (id) =>
      !models.some((m) => m.modelId === id && (m.kind ?? "text") === kind) &&
      id.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
  );
  const shown = models.filter((m) =>
    `${m.name} ${m.modelId}`
      .toLocaleLowerCase()
      .includes(search.toLocaleLowerCase()),
  );

  async function run(
    task: (signal: AbortSignal) => Promise<void>,
    success: string,
  ) {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await task(controller.signal);
      if (!controller.signal.aborted) {
        setMessage(success);
        setConfirm("");
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(
          cause instanceof Error ? cause.message : "操作未完成，请重试。",
        );
        onError(cause);
      }
    } finally {
      if (!controller.signal.aborted) setBusy(false);
      if (active.current === controller) active.current = null;
    }
  }
  function add(ids: string[]) {
    if (!source || !ids.length) return;
    void run(async (signal) => {
      await actions.addModels(source, ids, kind, signal);
      if (!signal.aborted) {
        setPicked([]);
        setManual("");
      }
    }, `已添加 ${ids.length} 个模型，可以分别测试并分配给功能。`);
  }

  return (
    <section className="providers" aria-label="供应商与模型">
      <div className="section-heading">
        <div>
          <h3>模型供应商</h3>
          <p className="muted">
            一份连接，多个模型。添加后在下方的功能分工中选择。
          </p>
        </div>
        <button
          className="button primary"
          disabled={!editable || busy}
          onClick={() => {
            setEditor("new");
            setMessage("");
          }}
        >
          添加供应商
        </button>
      </div>
      {location.protocol === "http:" && (
        <p className="models-transport muted">
          当前使用局域网 HTTP：请在可信网络中输入密钥。
        </p>
      )}
      {message && (
        <p className="models-notice" role="status">
          {message}
        </p>
      )}
      {editor && (
        <Drawer
          title={editor === "new" ? "添加供应商" : "编辑供应商"}
          variant="card"
          open={true}
          onClose={() => setEditor(null)}
        >
          {error && <p role="alert">{error}</p>}
          <SourceEditor
            key={editor === "new" ? "new" : source?.sourceId}
            source={editor === "edit" ? (source ?? null) : null}
            busy={busy}
            cancel={() => setEditor(null)}
            save={(draft) =>
              void run(async (signal) => {
                const id = await actions.saveSource(
                  draft,
                  editor === "edit" ? (source ?? null) : null,
                  signal,
                );
                if (!signal.aborted) {
                  setSelected(id);
                  setEditor(null);
                }
              }, "供应商已保存，请添加需要的模型。")
            }
          />
        </Drawer>
      )}
      {!sources.length && !editor && (
        <p className="muted">
          尚未添加供应商。先保存地址与 Key，再获取或手动添加模型。
        </p>
      )}
      {source && (
        <div className="provider-workspace">
          <nav className="provider-source-list" aria-label="供应商列表">
            {sources.map((item) => (
              <button
                key={item.sourceId}
                className={`provider-source-choice ${source.sourceId === item.sourceId ? "selected" : ""}`}
                aria-pressed={source.sourceId === item.sourceId}
                disabled={busy}
                onClick={() => {
                  setSelected(item.sourceId);
                  setEditor(null);
                  setMessage("");
                }}
              >
                <strong>{item.name}</strong>
                <small>{item.baseUrl}</small>
                <span>
                  {providers.filter((p) => p.sourceId === item.sourceId).length}{" "}
                  个模型 · {item.enabled ? "已启用" : "已停用"}
                </span>
              </button>
            ))}
          </nav>
          <section
            className="provider-source-detail"
            aria-label={`${source.name}供应商详情`}
          >
            <div className="section-heading">
              <div>
                <h3>{source.name}</h3>
                <p className="muted">{source.baseUrl}</p>
              </div>
              <StatusRail
                tone={source.enabled ? "blue" : "gray"}
                label={source.enabled ? "已启用" : "已停用"}
              />
            </div>
            <p className="muted">
              密钥：{source.hasKey ? "已保存，不会回显" : "尚未配置"}
            </p>
            <div className="models-actions">
              <button
                className="button"
                disabled={busy || !editable}
                onClick={() => setEditor("edit")}
              >
                编辑连接
              </button>
              <button
                className="button"
                disabled={busy || !editable}
                onClick={() =>
                  void run(
                    async (signal) => {
                      await actions.saveSource(
                        {
                          name: source.name,
                          baseUrl: source.baseUrl,
                          apiKey: "",
                          enabled: !source.enabled,
                        },
                        source,
                        signal,
                      );
                    },
                    source.enabled
                      ? "供应商已停用。"
                      : "供应商已启用，请测试需要的模型。",
                  )
                }
              >
                {source.enabled ? "停用供应商" : "启用供应商"}
              </button>
              <button
                className="button"
                disabled={busy || !editable || !source.hasKey}
                onClick={() => setConfirm("key")}
              >
                清除密钥
              </button>
              <button
                className="button"
                disabled={busy || !editable}
                onClick={() => setConfirm("source")}
              >
                删除供应商
              </button>
            </div>
            {(confirm === "source" || confirm === "key") && (
              <div className="provider-confirm">
                <p>
                  {confirm === "source"
                    ? `删除“${source.name}”及其 ${models.length} 个模型？`
                    : "清除共用密钥后，该供应商的所有模型将不可用。"}{" "}
                  已选择这些模型的功能需要重新分配。
                </p>
                <button
                  className="button"
                  disabled={busy}
                  onClick={() =>
                    void run(
                      (signal) =>
                        confirm === "source"
                          ? actions.removeSource(source, signal)
                          : actions.clearKey(source, signal),
                      "已完成。",
                    )
                  }
                >
                  确认{confirm === "source" ? "删除供应商" : "清除密钥"}
                </button>
                <button
                  className="button"
                  disabled={busy}
                  onClick={() => setConfirm("")}
                >
                  取消
                </button>
              </div>
            )}
            <div className="models-toolbar">
              <label>
                搜索模型
                <input
                  type="search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="名称或模型 ID"
                />
              </label>
            </div>
            <h4>已添加的模型（{models.length}）</h4>
            <div className="provider-model-rows">
              {shown.map((model) => {
                const current = model.test.revision === model.revision;
                const passed = current && model.test.state === "passed";
                return (
                  <article
                    className="provider-model-row"
                    key={model.providerId}
                    aria-label={`${model.modelId}模型`}
                  >
                    <div className="provider-model-heading">
                      <div>
                        <strong>{model.name}</strong>
                        <p className="muted">
                          {model.modelId} ·{" "}
                          {modelKindNames[model.kind ?? "text"]}
                        </p>
                        <p className="muted">
                          {passed
                            ? "测试通过"
                            : current && model.test.code === "upstream_rejected"
                              ? "服务拒绝请求"
                              : current && model.test.state === "failed"
                                ? "测试失败"
                                : current && model.test.state === "unknown"
                                  ? "测试结果未知"
                                  : "尚未测试"}
                          {model.test.at && current
                            ? ` · ${new Date(model.test.at).toLocaleString("zh-CN", { hour12: false })}`
                            : ""}
                        </p>
                      </div>
                      <div className="provider-badges">
                        {model.isDefault && (
                          <StatusRail tone="blue" label="默认对话模型" />
                        )}
                        <StatusRail
                          tone={
                            model.enabled && source.enabled ? "blue" : "gray"
                          }
                          label={
                            !source.enabled
                              ? "供应商已停用"
                              : model.enabled
                                ? "已启用"
                                : "已停用"
                          }
                        />
                      </div>
                    </div>
                    <p className="muted">
                      {current && model.test.code
                        ? ((
                            {
                              authentication_failed: "请检查供应商 API Key。",
                              model_not_found: "请检查模型 ID。",
                              endpoint_failed: "请检查供应商地址与模型类型。",
                              upstream_rejected:
                                "服务已返回拒绝响应，请检查账号权限和模型支持范围。",
                              connection_failed: "连接失败，请检查服务状态。",
                              timed_out: "请求超时，结果可能未知。",
                            } as Record<string, string>
                          )[model.test.code] ??
                          "测试未成功，请检查连接和模型类型。")
                        : ""}
                    </p>
                    <div className="models-actions">
                      <button
                        className="button"
                        disabled={
                          busy ||
                          !source.enabled ||
                          !source.hasKey ||
                          !model.enabled
                        }
                        onClick={() =>
                          void run(
                            (signal) => actions.test(model, signal),
                            `${model.modelId} 测试通过。`,
                          )
                        }
                      >
                        测试连接
                      </button>
                      <button
                        className="button"
                        disabled={busy || !editable}
                        onClick={() =>
                          setModelEditor(
                            modelEditor === model.providerId
                              ? ""
                              : model.providerId,
                          )
                        }
                      >
                        模型设置
                      </button>
                      <button
                        className="button"
                        disabled={busy || !editable}
                        onClick={() =>
                          void run(
                            (signal) =>
                              actions.setEnabled(model, !model.enabled, signal),
                            model.enabled
                              ? "模型已停用。"
                              : "模型已启用，请重新测试。",
                          )
                        }
                      >
                        {model.enabled ? "停用" : "启用"}
                      </button>
                      {(model.kind ?? "text") === "text" && (
                        <button
                          className="button"
                          disabled={
                            busy ||
                            model.isDefault ||
                            !passed ||
                            !model.enabled ||
                            !source.enabled ||
                            !source.hasKey
                          }
                          onClick={() =>
                            void run(
                              (signal) => actions.setDefault(model, signal),
                              "已设为默认对话模型。",
                            )
                          }
                        >
                          设为默认
                        </button>
                      )}
                      <button
                        className="button"
                        disabled={busy || !editable}
                        onClick={() => setConfirm(model.providerId)}
                      >
                        删除模型
                      </button>
                    </div>
                    {confirm === model.providerId && (
                      <div className="provider-confirm">
                        <p>
                          删除此模型后，使用它的功能需要重新选择；供应商连接和其他模型会保留。
                        </p>
                        <button
                          className="button"
                          disabled={busy}
                          onClick={() =>
                            void run(
                              (signal) => actions.remove(model, signal),
                              "模型已删除。",
                            )
                          }
                        >
                          确认删除模型
                        </button>
                        <button
                          className="button"
                          disabled={busy}
                          onClick={() => setConfirm("")}
                        >
                          取消
                        </button>
                      </div>
                    )}
                    {modelEditor === model.providerId && (
                      <Drawer
                        title={`模型设置 · ${model.name}`}
                        variant="card"
                        open={true}
                        onClose={() => setModelEditor("")}
                      >
                        {error && <p role="alert">{error}</p>}
                        <ModelEditor
                          model={model}
                          busy={busy}
                          cancel={() => setModelEditor("")}
                          save={(draft) =>
                            void run(async (signal) => {
                              await actions.saveModel(model, draft, signal);
                              if (!signal.aborted) setModelEditor("");
                            }, "模型设置已保存。")
                          }
                        />
                      </Drawer>
                    )}
                  </article>
                );
              })}
              {!shown.length && (
                <p className="muted">
                  {models.length
                    ? "没有匹配的模型。"
                    : "尚未添加模型。可从列表多选，也可手动填写模型 ID。"}
                </p>
              )}
            </div>
            <DetailCard title={"添加模型"}>
              {error && <p role="alert">{error}</p>}
              {message && <p role="status">{message}</p>}
              <fieldset
                className="provider-model-add"
                disabled={busy || !editable}
              >
                <legend>添加模型</legend>
                <label>
                  添加模型类型
                  <select
                    value={kind}
                    onChange={(e) => {
                      setKind(e.target.value as ModelKind);
                      setPicked([]);
                    }}
                  >
                    {Object.entries(modelKindNames).map(([id, name]) => (
                      <option key={id} value={id}>
                        {name}
                      </option>
                    ))}
                  </select>
                </label>
                <p className="muted">
                  类型决定可分配的功能与测试接口。目录不会自动验证模型能力。
                </p>
                <button
                  type="button"
                  className="button"
                  disabled={busy || !source.hasKey || !source.enabled}
                  onClick={() =>
                    void run(async (signal) => {
                      const items = await actions.listModels(source, signal);
                      if (!signal.aborted)
                        setDirectory({
                          sourceId: source.sourceId,
                          revision: source.revision,
                          items: [...new Set(items)],
                        });
                    }, "模型列表已获取。勾选后可一次添加多个；获取列表不等于测试通过。")
                  }
                >
                  获取模型列表
                </button>
                {directory && (
                  <>
                    <div className="models-actions">
                      <h4>可用模型（{available.length}）</h4>
                      <button
                        type="button"
                        className="button"
                        disabled={!available.length}
                        onClick={() => setPicked(available.slice(0, 256))}
                      >
                        选择当前结果
                      </button>
                      <button
                        type="button"
                        className="button"
                        disabled={!picked.length}
                        onClick={() => add(picked)}
                      >
                        添加所选模型（{picked.length}）
                      </button>
                    </div>
                    <ul className="provider-available-models">
                      {available.map((id) => (
                        <li key={id}>
                          <label>
                            <input
                              type="checkbox"
                              checked={picked.includes(id)}
                              onChange={(e) =>
                                setPicked((items) =>
                                  e.target.checked
                                    ? [...items, id].slice(0, 256)
                                    : items.filter((i) => i !== id),
                                )
                              }
                            />
                            <span>{id}</span>
                          </label>
                          <button
                            type="button"
                            className="button"
                            aria-label={`添加 ${id}`}
                            onClick={() => add([id])}
                          >
                            ＋
                          </button>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
                <form
                  className="models-toolbar"
                  onSubmit={(e) => {
                    e.preventDefault();
                    add([manual.trim()]);
                  }}
                >
                  <label>
                    手动填写模型 ID
                    <input
                      required
                      maxLength={256}
                      value={manual}
                      onChange={(e) => setManual(e.target.value)}
                      placeholder="填写供应商提供的准确模型 ID"
                    />
                  </label>
                  <button
                    className="button"
                    type="submit"
                    disabled={!manual.trim()}
                  >
                    添加模型
                  </button>
                </form>
              </fieldset>
            </DetailCard>
            <p className="models-test-disclosure muted">
              测试使用短样本，可能产生费用；不会发送聊天历史。每个模型分别测试，不会自动批量调用。
            </p>
          </section>
        </div>
      )}
    </section>
  );
}
