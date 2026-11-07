import { useEffect, useState } from "react";
import {
  useDomainCommand,
  useDomainResource,
  type DomainTarget,
} from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";

type Administrator = {
  current: {
    person_name: string;
    account_name: string;
    enabled: boolean;
  } | null;
  accounts: { connection_id: string; external_id: string; namespace: string }[];
  principals: {
    principal_id: string;
    display_name: string;
    username: string;
  }[];
  revision: number;
  is_selected: boolean;
  can_manage: boolean;
};

export function AdministratorPanel({ scope }: { scope: DomainTarget }) {
  const view = useDomainResource<Administrator>(
    "identity/role-administrator",
    scope,
  );
  const command = useDomainCommand("role-administrator", "actor");
  const [principal, setPrincipal] = useState("");
  useEffect(
    () => setPrincipal(view.data?.principals[0]?.principal_id ?? ""),
    [view.data],
  );
  async function save(enabled: boolean) {
    if (
      await command.run(
        "identity/role-administrator.save",
        { ...scope, principal_id: principal, enabled },
        view.data!.revision,
      )
    )
      await view.refresh();
  }
  return (
    <section className="people-config-form">
      <h3>主要用户／管理员</h3>
      <p>
        把已关联的真实渠道账号绑定到后台管理员。该人物在当前角色的私聊和群聊中均按此管理员授权处理。
      </p>
      {view.loading && <p role="status">正在读取管理员绑定…</p>}
      {view.error && <p role="alert">{view.error}</p>}
      {view.data && (
        <>
          <p>
            {view.data.current?.enabled
              ? `当前主要用户：${view.data.current.person_name} · 管理员：${view.data.current.account_name}`
              : "此角色尚未绑定渠道管理员。"}
          </p>
          {view.data.is_selected && (
            <p className="status-text">
              此人物已是当前角色的主要用户／管理员。
            </p>
          )}
          <ul>
            {view.data.accounts.map((account) => (
              <li key={account.connection_id + account.external_id}>
                {account.namespace.toUpperCase()} · {account.external_id}
              </li>
            ))}
          </ul>
          {!view.data.accounts.length && (
            <p>请先在“账号关联”中关联这个人物的真实 QQ 或其他渠道账号。</p>
          )}
          {view.data.can_manage && !!view.data.accounts.length && (
            <>
              <label>
                后台管理员账号
                <select
                  aria-label="后台管理员账号"
                  value={principal}
                  onChange={(event) => setPrincipal(event.target.value)}
                >
                  {view.data.principals.map((account) => (
                    <option
                      key={account.principal_id}
                      value={account.principal_id}
                    >
                      {account.display_name} · {account.username}
                    </option>
                  ))}
                </select>
              </label>
              <div className="domain-actions">
                <button
                  className="button primary"
                  disabled={!principal || command.busy}
                  onClick={() => void save(true)}
                >
                  绑定为当前角色管理员
                </button>
                {view.data.is_selected && (
                  <button
                    className="button"
                    disabled={command.busy}
                    onClick={() => void save(false)}
                  >
                    解除管理员绑定
                  </button>
                )}
              </div>
            </>
          )}
          <p className="muted">
            绑定只作用于当前角色。会话禁用仍在入口过滤，普通主动联系仍由私聊模式决定。
          </p>
        </>
      )}
      <CommandFeedback {...command} />
    </section>
  );
}
