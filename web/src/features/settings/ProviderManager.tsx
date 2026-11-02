import { useAuth } from "../../app/Auth";
import { requestId } from "../../app/requestId";
import { apiRead } from "../../api/client";
import { commandIntent } from "../../api/intents";
import type { CommandResult, Coverage } from "../../api/contracts";
import { GLOBAL_SCOPE } from "../../api/useDomainResource";
import { useEffect, useRef, useState } from "react";
import { Drawer } from "../../components/Drawer";
import { CommandFeedback } from "../../components/CommandFeedback";
import { StatusRail } from "../../components/StatusRail";
import { useDomainCommand } from "../../api/useDomainResource";
import { SourceEditor, ModelEditor } from "./ProviderEditors";
import { useModelCommand, type ProbeResult } from "./providerApi";
import {
  protocolNames,
  type ModelCatalog,
  type Provider,
  type ModelRoute,
  type ModelReceipt,
} from "./providerTypes";

type Enumeration = {
  provider_id: string;
  revision: number | null;
  models: string[] | null;
  next_cursor: string | null;
  coverage: Coverage;
  state: string;
  model_receipt: ModelReceipt;
};
export function ProviderManager({
  catalog,
  reload,
}: {
  catalog: ModelCatalog;
  reload: () => Promise<void>;
}) {
  const { session } = useAuth();
  const [directory, setDirectory] = useState<Enumeration | null>(null);
  const [directoryRequest, setDirectoryRequest] = useState<{
    providerId: string;
    clientId: string;
    revision: number;
  } | null>(null);
  const [readingResult, setReadingResult] = useState(false);
  const [readError, setReadError] = useState("");
  useEffect(() => {
    setDirectory(null);
    setDirectoryRequest(null);
    setReadError("");
  }, [session?.csrf]);
  const command = useModelCommand("model-connections");
  const probe = useDomainCommand<ProbeResult>("model-probe", "global");
  const enumerate = useDomainCommand<Enumeration>("model-directory", "global");
  const [selected, setSelected] = useState("");
  const source =
    catalog.providers.find((row) => row.provider_id === selected) ??
    catalog.providers[0];
  const [sourceEditor, setSourceEditor] = useState<Provider | "new" | null>(
    null,
  );
  const [routeEditor, setRouteEditor] = useState<ModelRoute | "new" | null>(
    null,
  );
  const [query, setQuery] = useState("");
  const [quota, setQuota] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [batchError, setBatchError] = useState("");
  const batchIds = useRef(new Map<string, string>());
  const group = quota || catalog.quota_groups[0]?.quota_group || "";
  const models = catalog.routes.filter(
    (row) =>
      row.provider_id === source?.provider_id &&
      `${row.model} ${row.route_id}`
        .toLocaleLowerCase()
        .includes(query.toLocaleLowerCase()),
  );
  const busy = command.busy || probe.busy || enumerate.busy;
  const listed =
    directory &&
    source &&
    directory.provider_id === source.provider_id &&
    directory.revision === source.revision
      ? (directory.models ?? [])
      : [];
  function acceptDirectory(value: Enumeration, append: boolean) {
    setDirectory((old) => ({
      ...value,
      models:
        append &&
        old?.provider_id === value.provider_id &&
        old.revision === value.revision
          ? [...new Set([...(old.models ?? []), ...(value.models ?? [])])]
          : value.models,
    }));
  }
  async function enumeratePage(cursor?: string) {
    if (!source || !session) return;
    const path = `models/providers/${encodeURIComponent(source.provider_id)}/models`;
    const body = {
      ...GLOBAL_SCOPE,
      quota_group: group,
      ...(cursor ? { cursor } : {}),
    };
    const intent = commandIntent(
      session.csrf,
      "model-directory",
      path,
      body,
      source.revision,
    );
    setDirectoryRequest({
      providerId: source.provider_id,
      clientId: intent.id,
      revision: source.revision,
    });
    setReadError("");
    const result = await enumerate.run(path, body, source.revision);
    if (result?.data) acceptDirectory(result.data, !!cursor);
  }
  async function readDirectoryResult() {
    if (!directoryRequest || !session) return;
    setReadingResult(true);
    setReadError("");
    try {
      const result = await apiRead<{
        found: boolean;
        command: CommandResult<Enumeration> | null;
      }>(
        `models/providers/${encodeURIComponent(directoryRequest.providerId)}/models/result`,
        { ...GLOBAL_SCOPE, client_request_id: directoryRequest.clientId },
        session.csrf,
        AbortSignal.timeout(15000),
      );
      if (result.command?.data) acceptDirectory(result.command.data, true);
      else setReadError("尚未找到本次枚举的原始账本结果，不会重新调用供应商。");
    } catch (cause) {
      setReadError(cause instanceof Error ? cause.message : "原结果读取未完成");
    } finally {
      setReadingResult(false);
    }
  }
  async function addModels() {
    if (!source || !group) return;
    setBatchError("");
    try {
      for (const model of picked) {
        if (
          catalog.routes.some(
            (route) =>
              route.provider_id === source.provider_id && route.model === model,
          )
        )
          continue;
        const key = `${source.provider_id}:${model}`;
        const id = batchIds.current.get(key) ?? requestId();
        batchIds.current.set(key, id);
        const saved = await command.save(
          "routes",
          {
            route_id: id,
            provider_id: source.provider_id,
            model,
            quota_group: group,
            enabled: true,
            verified_capabilities: [],
            verification: null,
            parameters: {},
          },
          0,
        );
        if (!saved) return;
      }
      setPicked([]);
    } catch (cause) {
      setBatchError(cause instanceof Error ? cause.message : "批量添加未完成");
    } finally {
      await reload();
    }
  }
  return (
    <section className="providers" aria-label="供应商与模型路由">
      <div className="section-heading">
        <div>
          <h3>模型供应商</h3>
          <p className="muted">
            一份原生协议连接可登记多个模型路由，并复用真实账号容量组。
          </p>
        </div>
        {command.manageable && (
          <button
            className="button primary"
            disabled={busy}
            onClick={() => setSourceEditor("new")}
          >
            添加供应商
          </button>
        )}
      </div>
      <CommandFeedback {...command} />
      <CommandFeedback {...probe} />
      <CommandFeedback {...enumerate} />
      {sourceEditor && (
        <Drawer
          title={sourceEditor === "new" ? "添加供应商" : "编辑供应商"}
          variant="card"
          open
          onClose={() => setSourceEditor(null)}
        >
          <SourceEditor
            key={sourceEditor === "new" ? "new" : sourceEditor.provider_id}
            source={sourceEditor === "new" ? null : sourceEditor}
            catalog={catalog}
            busy={busy}
            cancel={() => setSourceEditor(null)}
            save={async (draft) => {
              const saved = await command.save(
                "providers",
                draft,
                sourceEditor === "new" ? 0 : sourceEditor.revision,
              );
              if (saved) {
                setSelected(draft.provider_id);
                setSourceEditor(null);
                await reload();
              }
            }}
          />
        </Drawer>
      )}
      {!source && (
        <p className="muted">尚无模型供应商，先添加连接和共享容量组。</p>
      )}
      {source && (
        <div className="provider-workspace">
          <nav className="provider-source-list" aria-label="供应商列表">
            {catalog.providers.map((row) => (
              <button
                key={row.provider_id}
                className={`provider-source-choice ${row.provider_id === source.provider_id ? "selected" : ""}`}
                onClick={() => {
                  setSelected(row.provider_id);
                  setPicked([]);
                  setQuery("");
                }}
              >
                <strong>{row.name}</strong>
                <small>{protocolNames[row.protocol]}</small>
                <span>
                  {
                    catalog.routes.filter(
                      (route) => route.provider_id === row.provider_id,
                    ).length
                  }{" "}
                  个模型 · {row.enabled ? "已启用" : "已停用"}
                </span>
              </button>
            ))}
          </nav>
          <section className="provider-source-detail">
            <div className="section-heading">
              <div>
                <h3>{source.name}</h3>
                <p className="muted">{source.base_url}</p>
              </div>
              <StatusRail
                tone={source.enabled ? "blue" : "gray"}
                label={source.enabled ? "已启用" : "已停用"}
              />
            </div>
            <p>
              原生协议：{protocolNames[source.protocol]} · 连接修订{" "}
              {source.revision} · 密钥
              {source.credential_configured === true
                ? "已配置"
                : source.credential_configured === false
                  ? "未配置"
                  : "状态尚未核验"}
            </p>
            {command.manageable && (
              <div className="models-actions">
                <button
                  className="button"
                  disabled={busy}
                  onClick={() => setSourceEditor(source)}
                >
                  编辑连接
                </button>
                <button
                  className="button"
                  disabled={busy}
                  onClick={async () => {
                    if (
                      await command.save(
                        "providers",
                        { ...source, enabled: !source.enabled },
                        source.revision,
                      )
                    )
                      await reload();
                  }}
                >
                  {source.enabled ? "停用" : "启用"}
                </button>
                <button
                  className="button"
                  disabled={busy}
                  onClick={async () => {
                    if (
                      await command.save(
                        "providers",
                        { ...source, clear_api_key: true },
                        source.revision,
                      )
                    )
                      await reload();
                  }}
                >
                  清除密钥
                </button>
                <button
                  className="button"
                  disabled={busy}
                  onClick={async () => {
                    if (
                      await command.save(
                        `providers/${encodeURIComponent(source.provider_id)}/delete`,
                        {},
                        source.revision,
                      )
                    )
                      await reload();
                  }}
                >
                  删除供应商及其模型配置
                </button>
              </div>
            )}
            <div className="models-toolbar">
              <label>
                搜索已登记模型
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
              </label>
              {command.manageable && (
                <button
                  className="button"
                  disabled={busy}
                  onClick={() => setRouteEditor("new")}
                >
                  手动添加模型路由
                </button>
              )}
            </div>
            <div className="provider-model-rows">
              {models.map((route) => (
                <article className="provider-model-row" key={route.route_id}>
                  <div className="provider-model-heading">
                    <strong>{route.model}</strong>
                    <span>
                      {route.enabled ? "启用" : "停用"} · 容量组{" "}
                      {route.quota_group}
                    </span>
                  </div>
                  <p className="muted">
                    {route.route_id} · 修订 {route.revision} ·{" "}
                    {route.verified_capabilities.length
                      ? `已登记能力：${route.verified_capabilities.join("、")}`
                      : "能力尚未验证"}
                  </p>
                  {route.verification && (
                    <details>
                      <summary>能力依据</summary>
                      <pre>{JSON.stringify(route.verification, null, 2)}</pre>
                    </details>
                  )}
                  {command.manageable && (
                    <div className="models-actions">
                      <button
                        className="button"
                        disabled={busy}
                        onClick={() => setRouteEditor(route)}
                      >
                        编辑路由
                      </button>
                      {["text", "stream"].map((capability) => (
                        <button
                          className="button"
                          key={capability}
                          disabled={busy}
                          onClick={async () => {
                            const result = await probe.run(
                              `models/routes/${encodeURIComponent(route.route_id)}/probe`,
                              { capability },
                              route.revision,
                            );
                            if (result) await reload();
                          }}
                        >
                          实际测试{capability === "text" ? "文字" : "流式"}
                        </button>
                      ))}
                      <button
                        className="button"
                        disabled={busy}
                        onClick={async () => {
                          if (
                            await command.save(
                              `routes/${encodeURIComponent(route.route_id)}/delete`,
                              {},
                              route.revision,
                            )
                          )
                            await reload();
                        }}
                      >
                        删除路由
                      </button>
                    </div>
                  )}
                </article>
              ))}
            </div>
            {probe.result?.data.receipt && (
              <p role="status">
                最近测试：{probe.result.data.receipt.status} ·{" "}
                {probe.result.data.receipt.error_code ??
                  probe.result.data.verification}{" "}
                · 尝试 {probe.result.data.receipt.attempt_id}
              </p>
            )}
            {command.manageable && (
              <details>
                <summary>获取供应商模型目录并批量添加</summary>
                <label>
                  请求使用的真实容量组
                  <select
                    value={group}
                    onChange={(event) => setQuota(event.target.value)}
                  >
                    <option value="">选择容量组</option>
                    {catalog.quota_groups.map((row) => (
                      <option key={row.quota_group}>{row.quota_group}</option>
                    ))}
                  </select>
                </label>
                <button
                  className="button"
                  disabled={busy || !group}
                  onClick={() => void enumeratePage()}
                >
                  获取实际模型列表
                </button>
                <p className="muted">
                  列表不代表这些模型已验证可用；上游不支持或拒绝时显示实际失败，不自动填充预设列表。
                </p>
                {directoryRequest?.providerId === source.provider_id && (
                  <button
                    className="button"
                    disabled={readingResult}
                    onClick={() => void readDirectoryResult()}
                  >
                    只读查询本次原结果
                  </button>
                )}
                {readError && <p role="alert">{readError}</p>}
                {directory?.provider_id === source.provider_id && (
                  <p role="status">
                    枚举状态：{directory.state} ·{" "}
                    {directory.coverage.complete
                      ? "已取得完整目录"
                      : `覆盖缺口：${directory.coverage.missing.join("、")}`}{" "}
                    ·{" "}
                    {directory.model_receipt.error_code ??
                      directory.model_receipt.attempt_id}
                  </p>
                )}
                <div className="provider-model-directory">
                  {listed.map((id) => (
                    <label key={id}>
                      <input
                        type="checkbox"
                        checked={picked.includes(id)}
                        onChange={(event) =>
                          setPicked((old) =>
                            event.target.checked
                              ? [...old, id]
                              : old.filter((value) => value !== id),
                          )
                        }
                      />
                      {id}
                    </label>
                  ))}
                </div>
                {directory?.provider_id === source.provider_id &&
                  directory.next_cursor && (
                    <button
                      className="button"
                      disabled={busy}
                      onClick={() => void enumeratePage(directory.next_cursor!)}
                    >
                      读取供应商下一页模型
                    </button>
                  )}
                <button
                  className="button"
                  disabled={busy || !picked.length || !group}
                  onClick={() => void addModels()}
                >
                  添加所选 {picked.length} 个模型
                </button>
                {batchError && <p role="alert">{batchError}</p>}
              </details>
            )}
          </section>
        </div>
      )}
      {routeEditor && source && (
        <Drawer
          title={routeEditor === "new" ? "添加模型路由" : "编辑模型路由"}
          variant="card"
          open
          onClose={() => setRouteEditor(null)}
        >
          <ModelEditor
            key={
              routeEditor === "new" ? source.provider_id : routeEditor.route_id
            }
            route={routeEditor === "new" ? null : routeEditor}
            providerId={source.provider_id}
            catalog={catalog}
            busy={busy}
            cancel={() => setRouteEditor(null)}
            save={async (body) => {
              if (
                await command.save(
                  "routes",
                  body,
                  routeEditor === "new" ? 0 : routeEditor.revision,
                )
              ) {
                setRouteEditor(null);
                await reload();
              }
            }}
          />
        </Drawer>
      )}
    </section>
  );
}
