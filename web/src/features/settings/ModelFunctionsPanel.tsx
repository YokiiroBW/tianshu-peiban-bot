import { useState } from "react";
import { CommandFeedback } from "../../components/CommandFeedback";
import { useModelCatalog, useModelCommand } from "./providerApi";
import {
  functionNames,
  serviceNames,
  type ModelCatalog,
  type ServiceClass,
} from "./providerTypes";

function Binding({
  catalog,
  actorId,
  functionId,
  serviceClass,
  reload,
}: {
  catalog: ModelCatalog;
  actorId?: string;
  functionId: string;
  serviceClass: ServiceClass;
  reload: () => Promise<void>;
}) {
  const own = catalog.bindings.find(
    (row) =>
      row.function === functionId &&
      row.service_class === serviceClass &&
      row.actor_id === (actorId ?? ""),
  );
  const inherited =
    catalog.bindings.find(
      (row) =>
        row.function === functionId &&
        row.service_class === serviceClass &&
        !row.actor_id,
    ) ??
    catalog.bindings.find(
      (row) =>
        row.function === "*" &&
        row.service_class === serviceClass &&
        !row.actor_id,
    );
  const [selected, setSelected] = useState(own?.route_id ?? "");
  const command = useModelCommand(
    `model-binding:${actorId ?? "system"}:${functionId}:${serviceClass}`,
    actorId,
  );
  const routes = catalog.routes.filter(
    (route) =>
      route.enabled &&
      catalog.providers.some(
        (provider) =>
          provider.provider_id === route.provider_id && provider.enabled,
      ),
  );
  return (
    <article className="model-function">
      <h4>{functionNames[functionId] ?? functionId}</h4>
      <p className="muted">
        {own
          ? `当前：${own.route_id} · 修订 ${own.revision}`
          : inherited
            ? `继承：${inherited.route_id}`
            : "尚无绑定或系统默认"}
      </p>
      <label>
        模型路由
        <select
          value={selected}
          onChange={(event) => setSelected(event.target.value)}
        >
          <option value="">
            {functionId === "*" ? "请选择系统默认路由" : "使用系统继承"}
          </option>
          {own && !routes.some((row) => row.route_id === own.route_id) && (
            <option value={own.route_id}>{own.route_id}（不可用）</option>
          )}
          {routes.map((route) => (
            <option key={route.route_id} value={route.route_id}>
              {
                catalog.providers.find(
                  (provider) => provider.provider_id === route.provider_id,
                )?.name
              }{" "}
              / {route.model} · {route.route_id}
            </option>
          ))}
        </select>
      </label>
      {command.manageable && (
        <button
          className="button"
          disabled={command.busy || (!selected && !own)}
          onClick={async () => {
            const answer = await command.save(
              selected ? "bindings" : "bindings/delete",
              {
                function: functionId,
                service_class: serviceClass,
                actor_id: actorId ?? null,
                ...(selected ? { route_id: selected } : {}),
              },
              own?.revision ?? 0,
            );
            if (answer) await reload();
          }}
        >
          保存{selected ? "绑定" : "继承"}
        </button>
      )}
      <CommandFeedback {...command} />
    </article>
  );
}

export function ModelFunctionsPanel({ actorId }: { actorId?: string } = {}) {
  const catalog = useModelCatalog(actorId);
  const [serviceClass, setServiceClass] = useState<ServiceClass>("interactive");
  return (
    <section
      className="model-functions"
      aria-label={actorId ? "角色模型功能绑定" : "系统模型功能分工"}
    >
      <div className="section-heading">
        <div>
          <h3>{actorId ? "此角色的模型功能" : "系统默认与功能分工"}</h3>
          <p className="muted">
            即时、到期和后台各自选择默认路由；角色仅覆盖明确功能，嵌入始终使用唯一固定配置。
          </p>
        </div>
        <button className="button" onClick={() => void catalog.refresh()}>
          刷新
        </button>
      </div>
      <label>
        执行类别
        <select
          value={serviceClass}
          onChange={(event) =>
            setServiceClass(event.target.value as ServiceClass)
          }
        >
          {Object.entries(serviceNames).map(([id, name]) => (
            <option key={id} value={id}>
              {name}
            </option>
          ))}
        </select>
      </label>
      {catalog.error && <p role="alert">{catalog.error}</p>}
      {catalog.loading && !catalog.data && <p>正在读取实际模型目录…</p>}
      {catalog.data && (
        <div className="model-functions-grid">
          {[
            ...(!actorId ? ["*"] : []),
            ...catalog.data.functions.filter((id) => id !== "embed"),
          ].map((id) => (
            <Binding
              key={`${actorId ?? "system"}:${serviceClass}:${id}:${catalog.data?.bindings.find((row) => row.function === id && row.actor_id === (actorId ?? "") && row.service_class === serviceClass)?.revision ?? 0}`}
              catalog={catalog.data!}
              actorId={actorId}
              functionId={id}
              serviceClass={serviceClass}
              reload={catalog.refresh}
            />
          ))}
        </div>
      )}
    </section>
  );
}
