import { useState } from "react";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import {
  useDomainCommand,
  useDomainResource,
  GLOBAL_SCOPE,
} from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import { CommandFeedback } from "../../components/CommandFeedback";
import { useIdentityDirectory } from "./api";
import type { Person, Account } from "./types";

export function AccountLinkPanel({
  person,
  actorId,
}: {
  person?: Person;
  actorId?: string;
}) {
  const workspace = useRoleWorkspace();
  const directory = useIdentityDirectory();
  const [peopleCursor, setPeopleCursor] = useState<string | null>(null);
  const people = useDomainResource<Page<Person>>(
    "identity/people",
    { ...GLOBAL_SCOPE, limit: 100, cursor: peopleCursor },
    directory.manageable && !person,
  );
  const command = useDomainCommand<Account>("identity-account-link", "global");
  const [connectionId, setConnectionId] = useState("");
  const [externalId, setExternalId] = useState("");
  const [personId, setPersonId] = useState(person?.person_id ?? "");
  const [principalId, setPrincipalId] = useState("");
  const [editing, setEditing] = useState<Account | null>(null);
  const connections =
    directory.data?.connections.filter(
      (row) => !actorId || row.actor_id === actorId,
    ) ?? [];
  const selected = connections.find(
    (row) => row.connection_id === connectionId,
  );
  const accounts =
    directory.data?.accounts.filter(
      (row) => !person || row.person_id === person.person_id,
    ) ?? [];
  if (!directory.manageable)
    return (
      <p className="muted">
        人物身份与外部账号由已获身份管理权限的操作者关联；关系类型不会授予账号权限。
      </p>
    );
  return (
    <section>
      <h3>外部账号关联</h3>
      <p className="muted">
        同一人物可关联不同渠道账号；应用登录主体为独立选择，不能通过昵称或关系自动授予。
      </p>
      {directory.error && <p role="alert">{directory.error}</p>}
      <ul>
        {accounts.map((row) => (
          <li key={`${row.connection_id}:${row.external_id}`}>
            <strong>
              {row.connection_id} / {row.external_id}
            </strong>{" "}
            → {row.person_id}
            {row.principal_id && ` · 应用主体 ${row.principal_id}`}
            <button
              className="text-link"
              onClick={() => {
                setEditing(row);
                setConnectionId(row.connection_id);
                setExternalId(row.external_id);
                setPersonId(row.person_id);
                setPrincipalId(row.principal_id ?? "");
              }}
            >
              更正关联
            </button>
          </li>
        ))}
      </ul>
      <form
        className="people-config-form"
        onSubmit={async (event) => {
          event.preventDefault();
          const result = await command.run(
            "identity/accounts.link",
            {
              actor_id: selected?.actor_id ?? actorId ?? workspace.actor,
              person_id: person?.person_id ?? personId,
              conversation_id: null,
              connection_id: connectionId,
              external_id: externalId,
              principal_id: principalId || null,
            },
            editing?.revision ?? 0,
          );
          if (result) {
            setEditing(null);
            setExternalId("");
            setPrincipalId("");
            await directory.refresh();
          }
        }}
      >
        <label>
          已登记渠道
          <select
            required
            disabled={!!editing}
            value={connectionId}
            onChange={(event) => setConnectionId(event.target.value)}
          >
            <option value="">请选择</option>
            {connections.map((row) => (
              <option key={row.connection_id} value={row.connection_id}>
                {row.connection_id} · {row.namespace} · 账号{" "}
                {row.self_external_id}
              </option>
            ))}
          </select>
        </label>
        <label>
          对方的渠道账号 ID
          <input
            required
            readOnly={!!editing}
            value={externalId}
            onChange={(event) => setExternalId(event.target.value)}
          />
        </label>
        {!person && (
          <label>
            真实人物
            <select
              required
              value={personId}
              onChange={(event) => {
                setPersonId(event.target.value);
                setPrincipalId("");
              }}
            >
              <option value="">请选择已登记人物</option>
              {personId &&
                !people.data?.items.some(
                  (row) => row.person_id === personId,
                ) && <option value={personId}>{personId}（当前选择）</option>}
              {people.data?.items.map((row) => (
                <option key={row.person_id} value={row.person_id}>
                  {row.display_name}
                </option>
              ))}
            </select>
          </label>
        )}
        {!person && (
          <div className="models-actions">
            {peopleCursor && (
              <button
                className="button"
                type="button"
                onClick={() => setPeopleCursor(null)}
              >
                人物首批
              </button>
            )}
            {people.data?.next_cursor && (
              <button
                className="button"
                type="button"
                onClick={() => setPeopleCursor(people.data!.next_cursor!)}
              >
                下一批人物
              </button>
            )}
            {people.error && <p role="alert">{people.error}</p>}
          </div>
        )}
        <label>
          应用登录主体（可选）
          <select
            value={principalId}
            onChange={(event) => setPrincipalId(event.target.value)}
          >
            <option value="">不关联应用登录</option>
            {directory.data?.principals
              .filter(
                (row) => row.person_id === (person?.person_id ?? personId),
              )
              .map((row) => (
                <option key={row.principal_id} value={row.principal_id}>
                  {row.display_name} · {row.username}
                </option>
              ))}
          </select>
        </label>
        <div className="models-actions">
          <button className="button primary" disabled={command.busy}>
            保存实际账号关联
          </button>
          {editing && (
            <button
              type="button"
              className="button"
              onClick={() => {
                setEditing(null);
                setExternalId("");
              }}
            >
              取消更正
            </button>
          )}
        </div>
      </form>
      <CommandFeedback {...command} />
    </section>
  );
}
