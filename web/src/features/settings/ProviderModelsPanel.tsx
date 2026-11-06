import { useState } from "react";
import { DetailCard } from "../../components/DetailCard";
import { CommandFeedback } from "../../components/CommandFeedback";
import { ProviderManager } from "./ProviderManager";
import { ModelFunctionsPanel } from "./ModelFunctionsPanel";
import { QuotaEditor } from "./ProviderEditors";
import { useModelCatalog, useModelCommand } from "./providerApi";
import { modelRouteName, quotaGroupName } from "./providerPresentation";
import type { ModelCatalog, QuotaGroup } from "./providerTypes";
import "./models.css";

export function EmbeddingSettings() {
  const catalog = useModelCatalog();
  return (
    <section className="models">
      <h3>唯一语义嵌入配置</h3>
      <p>
        记忆与表情共用固定 1024
        维档案。修改模型版本、归一化或查询指令涉及索引重建，服务会明确拒绝直接切换。
      </p>
      {catalog.error && <p role="alert">{catalog.error}</p>}
      {catalog.data && (
        <EmbeddingEditor
          key={catalog.data.embedding_profile?.revision ?? 0}
          catalog={catalog.data}
          reload={catalog.refresh}
        />
      )}
    </section>
  );
}
function EmbeddingEditor({
  catalog,
  reload,
}: {
  catalog: ModelCatalog;
  reload: () => Promise<void>;
}) {
  const old = catalog.embedding_profile;
  const command = useModelCommand("embedding-profile");
  const [route, setRoute] = useState(old?.route_id ?? "");
  const [version, setVersion] = useState(old?.model_version ?? "");
  const [normalization, setNormalization] = useState(
    old?.normalization ?? "none",
  );
  const [instruction, setInstruction] = useState(old?.query_instruction ?? "");
  const routes = catalog.routes.filter(
    (item) =>
      catalog.providers.find(
        (provider) => provider.provider_id === item.provider_id,
      )?.protocol === "openai.embedding",
  );
  return (
    <form
      className="provider-editor"
      onSubmit={async (event) => {
        event.preventDefault();
        const answer = await command.save(
          "embedding-profile",
          {
            route_id: route,
            dimensions: 1024,
            model_version: version,
            normalization,
            query_instruction: instruction,
          },
          old?.revision ?? 0,
        );
        if (answer) await reload();
      }}
    >
      <label>
        嵌入模型路由
        <select
          required
          value={route}
          onChange={(event) => setRoute(event.target.value)}
        >
          <option value="">请选择嵌入协议路由</option>
          {routes.map((row) => (
            <option key={row.route_id} value={row.route_id}>
              {modelRouteName(catalog, row.route_id)}
            </option>
          ))}
        </select>
      </label>
      <label>
        模型版本
        <input
          required
          value={version}
          onChange={(event) => setVersion(event.target.value)}
        />
      </label>
      <label>
        归一化方式标记
        <input
          required
          value={normalization}
          onChange={(event) => setNormalization(event.target.value)}
        />
      </label>
      <label>
        查询指令
        <textarea
          rows={3}
          value={instruction}
          onChange={(event) => setInstruction(event.target.value)}
        />
      </label>
      <p className="muted">
        维度：1024 · {old ? `修订 ${old.revision}` : "尚未登记"}
      </p>
      {command.manageable && (
        <button className="button primary" disabled={command.busy}>
          保存固定档案
        </button>
      )}
      {command.manageable && old && (
        <button
          type="button"
          className="button"
          disabled={command.busy}
          onClick={async () => {
            if (
              await command.save("embedding-profile/delete", {}, old.revision)
            )
              await reload();
          }}
        >
          解除固定嵌入档案
        </button>
      )}
      <CommandFeedback {...command} />
    </form>
  );
}

export function ProviderModelsPanel({
  mode = "providers",
}: {
  mode?: "providers" | "defaults";
}) {
  const catalog = useModelCatalog();
  const command = useModelCommand("model-quota");
  const [quotaId, setQuotaId] = useState("");
  const selected =
    catalog.data?.quota_groups.find((row) => row.quota_group === quotaId) ??
    null;
  if (mode === "defaults") return <ModelFunctionsPanel />;
  return (
    <div className="models">
      <div className="section-heading">
        <div>
          <h2>模型供应商与路由</h2>
          <p className="muted">
            所有调用共用唯一
            Broker；供应商协议、模型能力、实际容量和功能绑定分别可查。
          </p>
        </div>
        <button className="button" onClick={() => void catalog.refresh()}>
          刷新目录
        </button>
      </div>
      {catalog.error && (
        <p className="models-error" role="alert">
          {catalog.error}
        </p>
      )}
      {catalog.loading && !catalog.data && <p>正在读取模型目录…</p>}
      {catalog.data && (
        <>
          <ProviderManager catalog={catalog.data} reload={catalog.refresh} />
          <DetailCard title="共享账号容量组">
            <p className="muted">
              即时预留和后台上限作用于同一真实额度；不按 Key 自动拆分容量。
            </p>
            <label>
              容量组
              <select
                value={quotaId}
                onChange={(event) => setQuotaId(event.target.value)}
              >
                <option value="">新增容量组</option>
                {catalog.data.quota_groups.map((row) => (
                  <option key={row.quota_group} value={row.quota_group}>{quotaGroupName(catalog.data!, row.quota_group)}</option>
                ))}
              </select>
            </label>
            {command.manageable && (
              <QuotaEditor
                key={`${quotaId}:${selected?.revision ?? 0}`}
                original={selected}
                busy={command.busy}
                save={async (body: QuotaGroup) => {
                  if (
                    await command.save(
                      "quota-groups",
                      body,
                      selected?.revision ?? 0,
                    )
                  ) {
                    setQuotaId(body.quota_group);
                    await catalog.refresh();
                  }
                }}
              />
            )}
            {!command.manageable && selected && (
              <dl className="models-facts">
                <dt>并发</dt>
                <dd>{selected.concurrency}</dd>
                <dt>请求/分钟</dt>
                <dd>{selected.rpm ?? "未设置"}</dd>
                <dt>即时预留</dt>
                <dd>{selected.interactive_reserve}</dd>
              </dl>
            )}
            <CommandFeedback {...command} />
          </DetailCard>
          <ModelFunctionsPanel />
          <DetailCard title="语义嵌入固定档案">
            <EmbeddingEditor
              key={catalog.data.embedding_profile?.revision ?? 0}
              catalog={catalog.data}
              reload={catalog.refresh}
            />
          </DetailCard>
        </>
      )}
    </div>
  );
}
