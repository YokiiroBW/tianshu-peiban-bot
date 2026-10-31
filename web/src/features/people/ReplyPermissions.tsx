import { useEffect, useRef, useState } from "react";
import { ShieldCheck, RefreshCw } from "lucide-react";
import {
  integrationPost,
  IntegrationError,
  readFailure,
} from "../../app/integrationApi";
import { requestId } from "../../app/requestId";
import {
  type Person,
  type Role,
  type Observations,
  type Connection,
} from "./types";
const failures: Record<string, string> = {
  reply_role_required: "首次开启私聊回复，需要选择该机器人账号的默认回复角色。",
  reply_role_conflict:
    "该账号已有其他默认回复角色，请刷新核对；这里不会修改整个账号的角色。",
  private_contact_not_found: "当前授权版本中还没有发现与这位用户的私聊。",
  version_conflict: "账号策略已改变，请刷新权限后重试。",
};
function failure(cause: unknown) {
  return cause instanceof IntegrationError && failures[cause.code]
    ? failures[cause.code]
    : readFailure(cause);
}
export function ReplyPermissions({
  person,
  roles,
  csrf,
  observations,
  onLocked,
}: {
  person: Person;
  roles: Role[];
  csrf: string;
  observations: Observations | null;
  onLocked: () => void;
}) {
  const [view, setView] = useState(observations);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [initialRoles, setInitialRoles] = useState<Record<string, string>>({});
  const active = useRef<AbortController | null>(null);
  const sourceIds = new Set(
    person.sources
      .filter((source) => source.conversation === `private:${person.qqId}`)
      .map((source) => source.connection.id),
  );
  const connections =
    view?.connections.filter((connection) => sourceIds.has(connection.id)) ??
    [];
  useEffect(() => () => active.current?.abort(), []);
  function start() {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    setNotice("");
    return controller;
  }
  async function refresh() {
    const controller = start();
    try {
      const result = await integrationPost<Observations>(
        "bot-observation/view",
        {},
        csrf,
        controller.signal,
      );
      if (!controller.signal.aborted) {
        setView(result);
        if (!result.unlocked) onLocked();
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        setView(null);
        setError(failure(cause));
      }
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  async function setPermission(connection: Connection, enabled: boolean) {
    const controller = start();
    try {
      const result = await integrationPost<{ connection: Connection }>(
        "bot-observation/private-access",
        {
          id: connection.id,
          qq_id: person.qqId,
          expected_revision: connection.revision,
          enabled,
          actor_id:
            enabled && !connection.private_policy.actor_id
              ? initialRoles[connection.id] || null
              : null,
          client_request_id: requestId(),
        },
        csrf,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setView(
        (before) =>
          before && {
            ...before,
            connections: before.connections.map((item) =>
              item.id === result.connection.id ? result.connection : item,
            ),
          },
      );
      setNotice(
        result.connection.state === "ready"
          ? "这位用户的私聊回复权限已保存。"
          : "保存已提交，插件状态仍待确认；请刷新核对实际生效情况。",
      );
    } catch (cause) {
      if (!controller.signal.aborted) {
        if (
          cause instanceof IntegrationError &&
          ["management_required", "forbidden", "unauthorized"].includes(
            cause.code,
          )
        ) {
          setView((before) => before && { ...before, unlocked: false });
          onLocked();
        }
        setError(failure(cause));
      }
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  return (
    <section aria-busy={busy}>
      <div className="people-content-heading">
        <div>
          <h3>
            <ShieldCheck size={19} aria-hidden="true" />
            私聊回复权限
          </h3>
          <p>
            按机器人账号管理这位用户的私聊回复。关系类型与好感不会授予回复权限。
          </p>
        </div>
        <button
          className="button"
          disabled={busy}
          onClick={() => void refresh()}
        >
          <RefreshCw size={15} aria-hidden="true" />
          刷新权限
        </button>
      </div>
      {error && (
        <p className="people-warning" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="people-notice" role="status">
          {notice}
        </p>
      )}
      {!view?.available && (
        <p className="people-empty">机器人观察与回复管理尚不可用。</p>
      )}
      {view?.available && connections.length === 0 && (
        <p className="people-empty">
          当前已加载来源中尚未发现与这位用户的私聊。群成员身份不会自动授予私聊回复权限。
        </p>
      )}
      {view?.available && !view.unlocked && (
        <p className="people-empty">
          请先在用户目录上方解锁会话来源与回复管理。
        </p>
      )}
      <div className="people-permissions">
        {connections.map((connection) => {
          const policy = connection.private_policy;
          const allowed =
            policy.mode === "whitelist"
              ? policy.list.includes(person.qqId)
              : policy.mode === "blacklist"
                ? !policy.list.includes(person.qqId)
                : false;
          const defaultRole =
            roles.find((item) => item.id === policy.actor_id)?.label ||
            (policy.actor_id ? "已指定其他角色" : null);
          return (
            <article className="people-info-card" key={connection.id}>
              <div className="people-permission-heading">
                <div>
                  <h4>{connection.name}</h4>
                  <small>机器人 QQ {connection.account_id}</small>
                </div>
                <span
                  className="people-chip"
                  data-allowed={allowed || undefined}
                >
                  {allowed ? "允许回复" : "不回复"}
                </span>
              </div>
              <dl>
                <div>
                  <dt>账号默认角色</dt>
                  <dd>{defaultRole || "尚未指定"}</dd>
                </div>
                <div>
                  <dt>账号运行状态</dt>
                  <dd>{connection.state === "ready" ? "已确认" : "待核对"}</dd>
                </div>
              </dl>
              {!connection.enabled && (
                <p className="people-warning">
                  此账号连接已停用，允许名单不代表正在回复。
                </p>
              )}
              {(!policy.observe || !connection.read_enabled) && (
                <p className="people-warning">
                  此账号的私聊观察已暂停，允许名单本身不会恢复观察。
                </p>
              )}
              {!policy.actor_id && !allowed && (
                <label className="people-field">
                  首次启用的账号默认回复角色
                  <select
                    aria-label={`${connection.name}默认回复角色`}
                    value={initialRoles[connection.id] || ""}
                    onChange={(event) =>
                      setInitialRoles((before) => ({
                        ...before,
                        [connection.id]: event.target.value,
                      }))
                    }
                    disabled={busy || !view?.unlocked}
                  >
                    <option value="">请选择角色</option>
                    {roles.map((role) => (
                      <option key={role.id} value={role.id}>
                        {role.label}
                      </option>
                    ))}
                  </select>
                  <small>
                    首次开启会为这个机器人账号设置默认角色，不是仅为当前用户选择角色。
                  </small>
                </label>
              )}
              <button
                className="button"
                disabled={
                  busy ||
                  !view?.unlocked ||
                  (!allowed && !policy.actor_id && !initialRoles[connection.id])
                }
                onClick={() => void setPermission(connection, !allowed)}
              >
                {allowed ? "停止回复此用户" : "允许回复此用户"}
              </button>
            </article>
          );
        })}
      </div>
      <p className="people-boundary">
        这里仅修改所选用户的私聊名单，保留其他用户和群聊策略。群聊仍按群级策略及触发条件处理。
      </p>
    </section>
  );
}
