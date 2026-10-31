import { useAuth } from "../../app/Auth";
import { useState } from "react";
import { UserRound, UsersRound, RefreshCw, Search } from "lucide-react";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import {
  useDomainCommand,
  useDomainResource,
  GLOBAL_SCOPE,
} from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import { CommandFeedback } from "../../components/CommandFeedback";
import { AccountLinkPanel } from "./AccountLinkPanel";
import { PersonDetail } from "./PersonDetail";
import { personName, type Person } from "./types";
import "./people.css";

export default function PeoplePage({ section }: { section: number }) {
  const { session } = useAuth();
  const manageable = session?.principal?.permissions.some(
    (value) => value === "*" || value === "identity.manage",
  );
  const workspace = useRoleWorkspace();
  const [cursor, setCursor] = useState<string | null>(null);
  const [chosen, setChosen] = useState("");
  const [query, setQuery] = useState("");
  const [newName, setNewName] = useState("");
  const page = useDomainResource<Page<Person>>("identity/people", {
    ...GLOBAL_SCOPE,
    cursor,
    limit: 100,
  });
  const command = useDomainCommand<Person>("person-create", "actor");
  const person = page.data?.items.find((row) => row.person_id === chosen);
  if (section === 2)
    return (
      <div className="people-page">
        <h2>人物与账号关联</h2>
        <AccountLinkPanel actorId={workspace.actor} />
      </div>
    );
  return (
    <div className="people-page">
      <header className="people-page-heading">
        <div>
          <h2>人物目录</h2>
          <p>只显示当前身份有权访问的人物；渠道账号和应用登录独立关联。</p>
        </div>
        <button className="button" onClick={() => void page.refresh()}>
          <RefreshCw size={16} />
          刷新
        </button>
      </header>
      {page.error && <p role="alert">{page.error}</p>}
      <div className="people-workspace scroll-layout">
        <aside className="people-directory scroll-pane">
          <label className="people-search">
            <Search size={16} />
            <input
              placeholder="搜索本批人物"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          {page.loading && <p>正在读取授权目录…</p>}
          <ul className="people-directory-list">
            {page.data?.items
              .filter((row) =>
                `${personName(row)} ${row.person_id}`
                  .toLocaleLowerCase()
                  .includes(query.toLocaleLowerCase()),
              )
              .map((row) => (
                <li key={row.person_id}>
                  <button
                    className="people-person-choice"
                    aria-pressed={chosen === row.person_id}
                    onClick={() => setChosen(row.person_id)}
                  >
                    <span className="people-avatar">
                      <UserRound size={18} />
                    </span>
                    <span>
                      <strong>{personName(row)}</strong>
                      <small>{row.person_id}</small>
                    </span>
                  </button>
                </li>
              ))}
          </ul>
          {!page.loading && page.data?.items.length === 0 && (
            <p>没有可读取的人物。</p>
          )}
          <div className="models-actions">
            {cursor && (
              <button
                className="button"
                onClick={() => {
                  setCursor(null);
                  setChosen("");
                }}
              >
                回到首批
              </button>
            )}
            {page.data?.next_cursor && (
              <button
                className="button"
                onClick={() => {
                  setCursor(page.data!.next_cursor!);
                  setChosen("");
                }}
              >
                下一批人物
              </button>
            )}
          </div>
          {manageable && (
            <details>
              <summary>登记新人物</summary>
              <form
                className="people-config-form"
                onSubmit={async (event) => {
                  event.preventDefault();
                  const result = await command.run(
                    "identity/people.save",
                    {
                      display_name: newName,
                      person_id: null,
                      conversation_id: null,
                    },
                    0,
                  );
                  if (result) {
                    setNewName("");
                    setCursor(null);
                    await page.refresh();
                    setChosen(result.data.person_id);
                  }
                }}
              >
                <label>
                  显示名称
                  <input
                    required
                    value={newName}
                    onChange={(event) => setNewName(event.target.value)}
                  />
                </label>
                <button className="button" disabled={command.busy}>
                  登记身份
                </button>
                <CommandFeedback {...command} />
              </form>
            </details>
          )}
        </aside>
        <div className="people-detail-area scroll-pane">
          {person ? (
            <PersonDetail
              key={`${person.person_id}:${person.revision}:${workspace.actor}`}
              person={person}
              actorId={workspace.actor}
              renamed={page.refresh}
            />
          ) : (
            <div className="people-welcome">
              <UsersRound size={36} />
              <h3>每次相遇，从这里开始</h3>
              <p>选择人物，查看当前角色授权范围内的认识、经历、关系和账号。</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
