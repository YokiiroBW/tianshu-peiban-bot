import { requestId } from "../../app/requestId";
import { useEffect, useState } from "react";
import type { ActorOption, Binding, Instance } from "./types";
import { SchemaFields } from "./SchemaFields";
import { pluginCall } from "./api";

export function BindingsPanel({
  instance,
  busy,
  save,
  csrf,
}: {
  instance: Instance;
  busy: boolean;
  save: (bindings: Binding[]) => void;
  csrf: string;
}) {
  const [bindings, setBindings] = useState(instance.bindings);
  const [actors, setActors] = useState<ActorOption[]>([]);
  const [actorError, setActorError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setActorError("");
    void pluginCall<{ items: ActorOption[] }>(
      "actors",
      {},
      csrf,
      controller.signal,
    )
      .then((result) => {
        if (!controller.signal.aborted) setActors(result.items);
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setActorError("暂时无法读取角色，请重新打开此页面。");
      });
    return () => controller.abort();
  }, [csrf, instance.id]);
  useEffect(
    () => setBindings(instance.bindings),
    [instance.id, instance.revision],
  );
  const overrideSchema = {
    ...instance.config_schema,
    required: [],
    properties: Object.fromEntries(
      Object.entries(instance.config_schema.properties ?? {}).filter(([key]) =>
        instance.binding_overrides.includes(key),
      ),
    ),
  };
  function update(index: number, value: Partial<Binding>) {
    setBindings((current) =>
      current.map((binding, at) =>
        at === index ? { ...binding, ...value } : binding,
      ),
    );
  }
  return (
    <form
      className="plugins-config"
      onSubmit={(event) => {
        event.preventDefault();
        save(bindings);
      }}
    >
      <p className="plugins-note">
        工作区可设置独立参数。角色只可调用这里勾选的动作，并使用此实例已配置的账号；账号和凭据仍在“配置”中管理。
      </p>
      {actorError && <p role="alert">{actorError}</p>}
      {bindings.map((binding, index) => (
        <fieldset className="plugins-fields" key={binding.binding_id}>
          <legend>
            {binding.scope.kind === "workspace"
              ? "网页工作区"
              : binding.scope.kind === "character"
                ? "角色能力"
                : "已有绑定"}
          </legend>
          <label className="plugins-check">
            <input
              type="checkbox"
              checked={binding.enabled}
              onChange={(event) =>
                update(index, { enabled: event.target.checked })
              }
            />
            启用此范围
          </label>
          {binding.scope.kind === "workspace" ? (
            <label className="plugins-field">
              工作区名称
              <input
                required
                value={binding.scope.workspace_id ?? ""}
                onChange={(event) =>
                  update(index, {
                    scope: {
                      kind: "workspace",
                      workspace_id: event.target.value,
                    },
                  })
                }
              />
            </label>
          ) : binding.scope.kind === "character" ? (
            <>
              <label className="plugins-field">
                角色
                <select
                  required
                  value={String(binding.scope.character_id ?? "")}
                  onChange={(event) =>
                    update(index, {
                      scope: {
                        kind: "character",
                        character_id: event.target.value,
                      },
                    })
                  }
                >
                  {!actors.some(
                    (actor) => actor.actor_id === binding.scope.character_id,
                  ) && (
                    <option value={String(binding.scope.character_id ?? "")}>
                      角色不可用（
                      {String(binding.scope.character_id ?? "未选择")}）
                    </option>
                  )}
                  {actors.map((actor) => (
                    <option key={actor.actor_id} value={actor.actor_id}>
                      {actor.name}
                    </option>
                  ))}
                </select>
              </label>
              <fieldset className="plugins-fields">
                <legend>允许调用的动作</legend>
                {instance.contributions.actions.map((action) => (
                  <label className="plugins-check" key={action.id}>
                    <input
                      type="checkbox"
                      checked={(binding.entries ?? []).some(
                        (entry) =>
                          entry.kind === "action" && entry.id === action.id,
                      )}
                      onChange={(event) => {
                        const entries = (binding.entries ?? []).filter(
                          (entry) =>
                            entry.kind !== "action" || entry.id !== action.id,
                        );
                        if (event.target.checked)
                          entries.push({ kind: "action", id: action.id });
                        update(index, { entries });
                      }}
                    />
                    {action.title}（
                    {action.effect === "write" ? "写入操作" : "只读查询"}）
                  </label>
                ))}
                {(binding.entries ?? []).filter(
                  (entry) => entry.kind === "action",
                ).length === 0 && (
                  <p className="plugins-note">尚未允许任何动作。</p>
                )}
              </fieldset>
            </>
          ) : (
            <p>{JSON.stringify(binding.scope)}</p>
          )}
          <label className="plugins-field">
            优先级
            <input
              type="number"
              step={1}
              value={binding.priority}
              onChange={(event) =>
                update(index, { priority: Number(event.target.value) })
              }
            />
          </label>
          {instance.binding_overrides.length > 0 && (
            <fieldset className="plugins-fields">
              <legend>此范围的配置</legend>
              <SchemaFields
                schema={overrideSchema}
                value={binding.overrides}
                onChange={(overrides) => update(index, { overrides })}
              />
            </fieldset>
          )}
          <button
            type="button"
            onClick={() =>
              setBindings((current) => current.filter((_, at) => at !== index))
            }
          >
            移除此范围
          </button>
        </fieldset>
      ))}
      <button
        type="button"
        onClick={() =>
          setBindings((current) => [
            ...current,
            {
              kind: "binding",
              schema_version: 1,
              binding_id: requestId(),
              instance_id: instance.id,
              revision: 1,
              enabled: true,
              scope: { kind: "workspace", workspace_id: "main" },
              overrides: {},
              priority: 0,
            },
          ])
        }
      >
        添加网页工作区
      </button>
      <button
        type="button"
        disabled={busy || actors.length === 0}
        onClick={() =>
          setBindings((current) => [
            ...current,
            {
              kind: "binding",
              schema_version: 1,
              binding_id: requestId(),
              instance_id: instance.id,
              revision: 1,
              enabled: true,
              scope: { kind: "character", character_id: actors[0].actor_id },
              entries: [],
              overrides: {},
              priority: 0,
            },
          ])
        }
      >
        添加角色能力
      </button>
      {!actorError && actors.length === 0 && (
        <p className="plugins-note">暂无可绑定的已启用角色。</p>
      )}
      <button className="button primary" disabled={busy}>
        保存范围
      </button>
    </form>
  );
}
