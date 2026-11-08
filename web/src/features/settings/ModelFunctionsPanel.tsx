import { useState } from "react";
import { CommandFeedback } from "../../components/CommandFeedback";
import { useModelCatalog, useModelCommand } from "./providerApi";
import { modelRouteName } from "./providerPresentation";
import { functionNames, type ModelCatalog } from "./providerTypes";

function Binding({
  catalog,
  actorId,
  functionId,
  reload,
}: {
  catalog: ModelCatalog;
  actorId?: string;
  functionId: string;
  reload: () => Promise<void>;
}) {
  const own = catalog.bindings.find(
    (row) => row.function === functionId && row.actor_id === (actorId ?? ""),
  );
  const inherited =
    catalog.bindings.find(
      (row) => row.function === functionId && !row.actor_id,
    ) ?? catalog.bindings.find((row) => row.function === "*" && !row.actor_id);
  const [selected, setSelected] = useState(own?.route_id ?? "");
  const [fallbacks, setFallbacks] = useState<string[]>(
    own?.fallback_routes ?? [],
  );
  const command = useModelCommand(
    `model-binding:${actorId ?? "system"}:${functionId}`,
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
      <h4>{functionNames[functionId] ?? "其他模型功能"}</h4>
      <p className="muted">
        {own
          ? `当前：${modelRouteName(catalog, own.route_id)}`
          : inherited
            ? `继承：${modelRouteName(catalog, inherited.route_id)}`
            : "尚无绑定或系统默认"}
      </p>
      <label>
        模型路由
        <select
          value={selected}
          onChange={(event) => {
            const next = event.target.value;
            setSelected(next);
            setFallbacks((current) => current.filter((id) => id !== next));
          }}
        >
          <option value="">
            {functionId === "*" ? "请选择系统默认路由" : "使用系统继承"}
          </option>
          {own && !routes.some((row) => row.route_id === own.route_id) && (
            <option value={own.route_id}>原模型配置（当前不可用）</option>
          )}
          {routes.map((route) => (
            <option key={route.route_id} value={route.route_id}>
              {modelRouteName(catalog, route.route_id)}
            </option>
          ))}
        </select>
      </label>
      {selected && (
        <fieldset>
          <legend>按顺序尝试的备用路由</legend>
          <p className="muted">
            只有原路线不可用且候选通过能力、容量及协议检查时才会切换；结果未知的请求不会自动重发。
          </p>
          {fallbacks.map((id, index) => (
            <div className="models-actions" key={`${index}:${id}`}>
              <label>
                备用路由 {index + 1}
                <select
                  value={id}
                  onChange={(event) => {
                    const next = event.target.value;
                    setFallbacks((current) =>
                      current.map((value, position) =>
                        position === index ? next : value,
                      ),
                    );
                  }}
                >
                  {!routes.some((route) => route.route_id === id) && (
                    <option value={id}>原备用模型（当前不可用）</option>
                  )}
                  {routes
                    .filter(
                      (route) =>
                        route.route_id !== selected &&
                        (route.route_id === id ||
                          !fallbacks.includes(route.route_id)),
                    )
                    .map((route) => (
                      <option key={route.route_id} value={route.route_id}>
                        {modelRouteName(catalog, route.route_id)}
                      </option>
                    ))}
                </select>
              </label>
              <button
                type="button"
                className="button"
                disabled={index === 0}
                onClick={() =>
                  setFallbacks((current) => {
                    const next = [...current];
                    [next[index - 1], next[index]] = [
                      next[index],
                      next[index - 1],
                    ];
                    return next;
                  })
                }
              >
                上移
              </button>
              <button
                type="button"
                className="button"
                onClick={() =>
                  setFallbacks((current) =>
                    current.filter((_, position) => position !== index),
                  )
                }
              >
                移除
              </button>
            </div>
          ))}
          {routes.some(
            (route) =>
              route.route_id !== selected &&
              !fallbacks.includes(route.route_id),
          ) && (
            <button
              type="button"
              className="button"
              onClick={() => {
                const next = routes.find(
                  (route) =>
                    route.route_id !== selected &&
                    !fallbacks.includes(route.route_id),
                );
                if (next)
                  setFallbacks((current) => [...current, next.route_id]);
              }}
            >
              添加备用路由
            </button>
          )}
        </fieldset>
      )}
      {command.manageable && (
        <button
          className="button"
          disabled={command.busy || (!selected && !own)}
          onClick={async () => {
            const answer = await command.save(
              selected ? "bindings" : "bindings/delete",
              {
                function: functionId,
                actor_id: actorId ?? null,
                ...(selected
                  ? { route_id: selected, fallback_routes: fallbacks }
                  : {}),
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
  return (
    <section
      className="model-functions"
      aria-label={actorId ? "角色模型功能绑定" : "系统模型功能分工"}
    >
      <div className="section-heading">
        <div>
          <h3>{actorId ? "此角色的模型功能" : "系统默认与功能分工"}</h3>
          <p className="muted">
            每项功能统一选择模型，角色可覆盖具体功能；排队优先级由系统管理，嵌入使用唯一固定配置。
          </p>
        </div>
        <button className="button" onClick={() => void catalog.refresh()}>
          刷新
        </button>
      </div>
      {catalog.error && <p role="alert">{catalog.error}</p>}
      {catalog.loading && !catalog.data && <p>正在读取实际模型目录…</p>}
      {catalog.data && (
        <div className="model-functions-grid">
          {[
            ...(!actorId ? ["*"] : []),
            ...catalog.data.functions.filter((id) => id !== "embed"),
          ].map((id) => (
            <Binding
              key={`${actorId ?? "system"}:${id}:${catalog.data?.bindings.find((row) => row.function === id && row.actor_id === (actorId ?? ""))?.revision ?? 0}`}
              catalog={catalog.data!}
              actorId={actorId}
              functionId={id}
              reload={catalog.refresh}
            />
          ))}
        </div>
      )}
    </section>
  );
}
