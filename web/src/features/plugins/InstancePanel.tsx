import { requestId } from "../../app/requestId";
import { useEffect, useState } from "react";
import type { Action, Instance, Invocation } from "./types";
import { SchemaFields } from "./SchemaFields";
import { PluginViewFrame } from "./PluginViewFrame";
import { BindingsPanel } from "./BindingsPanel";

export const states: Record<string, string> = {
  ready: "运行中",
  stopped: "已停用",
  error: "需要处理",
  managed_elsewhere: "由其他入口管理",
  blocked: "缺少运行能力",
  pending_configuration: "待配置",
  accepted: "已受理",
  running: "执行中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
};
const effects: Record<string, string> = {
  none: "无外部写入",
  applied: "操作已发生",
  not_applied: "操作未发生",
  unknown: "外部结果未知",
};
type Mutate = (operation: string, body: object) => Promise<unknown>;

function ActionForm({
  action,
  instance,
  mutate,
  busy,
  bindingId,
}: {
  action: Action;
  instance: Instance;
  mutate: Mutate;
  busy: boolean;
  bindingId: string;
}) {
  const [value, setValue] = useState<Record<string, unknown>>({});
  return (
    <form
      className="plugins-action"
      onSubmit={(event) => {
        event.preventDefault();
        void mutate("invoke", {
          instance_id: instance.id,
          action: action.id,
          input: value,
          client_request_id: requestId(),
          ...(bindingId ? { binding_id: bindingId } : {}),
        });
      }}
    >
      <div className="plugins-inline">
        <strong>{action.title}</strong>
        <small>{action.effect === "write" ? "写入操作" : "只读查询"}</small>
      </div>
      <SchemaFields
        schema={action.input_schema}
        value={value}
        onChange={setValue}
      />
      {action.availability.missing_capabilities.length > 0 && (
        <p>缺少：{action.availability.missing_capabilities.join("、")}</p>
      )}
      <button
        className="button primary"
        disabled={busy || !action.availability.available}
      >
        执行
      </button>
    </form>
  );
}

export function InstancePanel({
  instance,
  calls,
  logs,
  schedules,
  mutate,
  busy,
  csrf,
  versions,
}: {
  instance: Instance;
  calls: Invocation[];
  logs: unknown[];
  schedules: unknown[];
  mutate: Mutate;
  busy: boolean;
  csrf: string;
  versions: string[];
}) {
  const [tab, setTab] = useState("actions");
  const [bindingId, setBindingId] = useState("");
  const [config, setConfig] = useState(instance.config);
  const [secrets, setSecrets] = useState<Record<string, string | null>>({});
  const [version, setVersion] = useState(instance.version);
  useEffect(() => {
    setConfig(instance.config);
    setSecrets({});
    setVersion(instance.version);
    if (
      !instance.bindings.some(
        (binding) => binding.binding_id === bindingId && binding.enabled,
      )
    )
      setBindingId("");
  }, [instance.id, instance.revision]);
  return (
    <section className="panel plugins-detail" aria-label="插件实例">
      <div className="plugins-inline">
        <div>
          <h2>{instance.name}</h2>
          <small>
            {instance.package_id} · {instance.version}
          </small>
        </div>
        <span className={`plugins-status ${instance.state}`}>
          {states[instance.state] ?? instance.state}
        </span>
        <button
          disabled={busy}
          onClick={() =>
            void mutate(
              instance.desired_state === "enabled" ? "disable" : "enable",
              {
                instance_id: instance.id,
                expected_revision: instance.revision,
              },
            )
          }
        >
          {instance.desired_state === "enabled" ? "停用" : "启用"}
        </button>
      </div>
      {instance.problem && (
        <p className="plugins-note">请完成“配置”中的必填项。</p>
      )}
      {instance.missing_capabilities.length > 0 && (
        <p className="plugins-note">
          尚无提供者：{instance.missing_capabilities.join("、")}
        </p>
      )}
      <nav className="plugins-tabs" aria-label="实例内容">
        {[
          ["actions", "功能"],
          ["config", "配置"],
          ["bindings", "作用范围"],
          ["tasks", "任务"],
          ["logs", "日志"],
          ["version", "版本"],
        ].map(([id, label]) => (
          <button key={id} aria-pressed={tab === id} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </nav>
      {tab === "actions" && (
        <div className="plugins-actions">
          {instance.bindings.some(
            (binding) => binding.enabled && binding.scope.kind === "workspace",
          ) && (
            <label className="plugins-field">
              使用范围
              <select
                value={bindingId}
                onChange={(event) => setBindingId(event.target.value)}
              >
                <option value="">实例默认配置</option>
                {instance.bindings
                  .filter(
                    (binding) =>
                      binding.enabled && binding.scope.kind === "workspace",
                  )
                  .map((binding) => (
                    <option key={binding.binding_id} value={binding.binding_id}>
                      {binding.scope.workspace_id}
                    </option>
                  ))}
              </select>
            </label>
          )}
          {instance.contributions.views?.map((view) => (
            <PluginViewFrame
              key={view.id}
              instance={instance}
              view={view}
              csrf={csrf}
              bindingId={bindingId}
            />
          ))}
          {instance.contributions.actions.map((action) => (
            <ActionForm
              key={instance.id + action.id}
              action={action}
              instance={instance}
              mutate={mutate}
              busy={busy}
              bindingId={bindingId}
            />
          ))}
          {(instance.contributions.commands?.length ?? 0) > 0 && (
            <p className="plugins-note">
              已声明命令：
              {instance.contributions
                .commands!.flatMap((item) => item.names)
                .join("、")}
              。机器人命令入口尚未接入。
            </p>
          )}
        </div>
      )}
      {tab === "config" && (
        <form
          className="plugins-config"
          onSubmit={(event) => {
            event.preventDefault();
            void mutate("configure", {
              instance_id: instance.id,
              config,
              secrets,
              expected_revision: instance.revision,
            });
          }}
        >
          <SchemaFields
            schema={instance.config_schema}
            value={config}
            onChange={setConfig}
          />
          {Object.entries(instance.config_schema.properties ?? {})
            .filter(([, field]) => field["x-secret"])
            .map(([name, field]) => (
              <label className="plugins-field" key={name}>
                <span>
                  {field.title || name} ·{" "}
                  {instance.secret_configured[name] ? "已填写" : "未填写"}
                </span>
                <input
                  type="password"
                  autoComplete="new-password"
                  value={secrets[name] ?? ""}
                  placeholder={
                    instance.secret_configured[name]
                      ? "留空保留，输入新值替换"
                      : "填写访问密钥"
                  }
                  onChange={(event) =>
                    setSecrets((current) => {
                      const result = { ...current };
                      if (event.target.value) result[name] = event.target.value;
                      else delete result[name];
                      return result;
                    })
                  }
                />
                {instance.secret_configured[name] && (
                  <button
                    type="button"
                    onClick={() =>
                      setSecrets((current) => ({ ...current, [name]: null }))
                    }
                  >
                    {secrets[name] === null ? "保存时清除" : "清除密钥"}
                  </button>
                )}
              </label>
            ))}
          <button className="button primary" disabled={busy}>
            保存配置
          </button>
        </form>
      )}
      {tab === "bindings" && (
        <BindingsPanel
          instance={instance}
          busy={busy}
          csrf={csrf}
          save={(bindings) =>
            void mutate("bindings", {
              instance_id: instance.id,
              bindings,
              expected_revision: instance.revision,
            })
          }
        />
      )}
      {tab === "tasks" && (
        <div className="plugins-records">
          {calls.length === 0 && <p>还没有动作记录。</p>}
          {calls.map((call) => {
            const action = instance.contributions.actions.find(
              (item) => item.id === call.action,
            );
            return (
              <article className="plugins-record" key={call.id}>
                <div className="plugins-inline">
                  <strong>{action?.title ?? call.action}</strong>
                  <span>{states[call.execution] ?? call.execution}</span>
                </div>
                <p>
                  {effects[call.effect] ?? call.effect}
                  {call.external?.status &&
                    ` · 外部任务：${call.external.status}`}{" "}
                  · 通知：{call.delivery}
                </p>
                <small>
                  {new Date(call.created_at * 1000).toLocaleString()} · 配置版本{" "}
                  {call.config_revision}
                </small>
                {call.progress && (
                  <p>
                    {call.progress.message} {call.progress.completed}
                    {call.progress.total !== null
                      ? ` / ${call.progress.total}`
                      : ""}
                  </p>
                )}
                {call.error && <p role="status">{call.error.code}</p>}
                {call.result !== null && (
                  <pre>{JSON.stringify(call.result, null, 2)}</pre>
                )}
                <div className="plugins-inline">
                  {["accepted", "running"].includes(call.execution) && (
                    <button
                      disabled={busy}
                      onClick={() => void mutate("cancel", { id: call.id })}
                    >
                      取消本次执行
                    </button>
                  )}
                  {call.effect === "unknown" && action?.reconcile_handler && (
                    <button
                      disabled={busy}
                      onClick={() => void mutate("reconcile", { id: call.id })}
                    >
                      核对原操作结果
                    </button>
                  )}
                  {["failed", "interrupted", "cancelled"].includes(
                    call.execution,
                  ) &&
                    action?.resume_handler && (
                      <button
                        disabled={busy}
                        onClick={() => void mutate("resume", { id: call.id })}
                      >
                        继续原任务
                      </button>
                    )}
                </div>
              </article>
            );
          })}
          <details>
            <summary>周期任务（{schedules.length}）</summary>
            <pre>{JSON.stringify(schedules, null, 2)}</pre>
          </details>
        </div>
      )}
      {tab === "logs" && (
        <div className="plugins-records">
          {logs.length ? (
            logs.map((item, index) => (
              <pre key={index}>{JSON.stringify(item, null, 2)}</pre>
            ))
          ) : (
            <p>暂无插件日志。</p>
          )}
        </div>
      )}
      {tab === "version" && (
        <div className="plugins-config">
          <label className="plugins-field">
            已安装版本
            <select
              value={version}
              onChange={(event) => setVersion(event.target.value)}
            >
              {versions.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </label>
          <button
            disabled={busy || version === instance.version}
            onClick={() =>
              void mutate("upgrade", {
                instance_id: instance.id,
                version,
                expected_revision: instance.revision,
              })
            }
          >
            切换版本
          </button>
          <p className="plugins-note">
            切换失败会恢复迁移前的实例状态；卸载保留实例数据和外部任务。
          </p>
          <button
            disabled={busy}
            onClick={() =>
              void mutate("uninstall", { package_id: instance.package_id })
            }
          >
            卸载此插件包
          </button>
        </div>
      )}
    </section>
  );
}
