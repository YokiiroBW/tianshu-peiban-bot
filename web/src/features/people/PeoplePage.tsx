import { useAuth } from "../../app/Auth";
import { useState } from "react";
import { UserRound, UsersRound, RefreshCw, Search } from "lucide-react";
import { RolePicker, useRoleWorkspace } from "../../app/RoleWorkspace";
import {
  useDomainCommand,
  useDomainResource,
  actorScope,
} from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import { CommandFeedback } from "../../components/CommandFeedback";
import { AccountLinkPanel } from "./AccountLinkPanel";
import { PersonDetail } from "./PersonDetail";
import { personName, type Person } from "./types";
import "./people.css";

export default function PeoplePage({ section }: { section: number }) {
  const workspace = useRoleWorkspace();
  return <PeopleDirectory key={workspace.actor} section={section} />;
}

function PeopleDirectory({ section }: { section: number }) {
  const { session } = useAuth();
  const manageable = session?.principal?.permissions.some(
    (value) => value === "*" || value === "identity.manage",
  );
  const workspace = useRoleWorkspace();
  const [cursor, setCursor] = useState<string | null>(null);
  const [chosen, setChosen] = useState("");
  const [query, setQuery] = useState("");
  const [newName, setNewName] = useState("");
  const page = useDomainResource<Page<Person>>(
    "identity/people",
    {
      ...actorScope(workspace.actor),
      cursor,
      limit: 100,
    },
    !!workspace.actor,
  );
  const command = useDomainCommand<Person>("person-create", "actor");
  const people =
    page.data?.items.filter((row) =>
      `${personName(row)} ${row.person_id}`
        .toLocaleLowerCase()
        .includes(query.toLocaleLowerCase()),
    ) ?? [];
  const person = people.find((row) => row.person_id === chosen) ?? people[0];
  if (section === 2)
    return (
      <div className="people-page">
        <h2>人物与账号关联</h2>
        <AccountLinkPanel actorId={workspace.actor} />
      </div>
    );
  return (
    <div className="people-page">
      <header className="page-top-bar people-page-heading">
        <div className="page-identity">
          <span className="role-seal-avatar">友</span>
          <div className="page-title-wrap">
            <h2>用户档案</h2>
            <p className="page-title-meta">
              {workspace.roles.find((role) => role.id === workspace.actor)
                ?.name ?? "当前角色"}
              认识的人。切换顶部角色，查看各自的记忆与相处设置。
            </p>
          </div>
        </div>
        <RolePicker compact />
        <button
          className="button curio-btn"
          onClick={() => void page.refresh()}
        >
          <RefreshCw size={15} />
          刷新
        </button>
      </header>
      {page.error && <p role="alert">{page.error}</p>}
      {!workspace.actor && <p>请先在顶部选择角色。</p>}
      <div className="people-workspace scroll-layout">
        <aside className="curio-card people-directory scroll-pane">
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
            {people.map((row) => (
              <li key={row.person_id}>
                <button
                  className="people-person-choice"
                  aria-pressed={person?.person_id === row.person_id}
                  onClick={() => setChosen(row.person_id)}
                >
                  <span className="people-avatar">
                    <UserRound size={17} />
                  </span>
                  <span>
                    <strong>{personName(row)}</strong>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {!page.loading && !people.length && (
            <p>
              {query
                ? "本批没有匹配的人物。"
                : "这个角色还没有可读取的人物档案。"}
            </p>
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
          {manageable && !!workspace.actor && (
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
        <div className="curio-card people-detail-area scroll-pane">
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
