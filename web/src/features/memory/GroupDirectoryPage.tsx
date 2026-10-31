import { useMemoryCursor } from "./pagination";
import { useState } from "react";
import { RefreshCw, UsersRound } from "lucide-react";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { actorScope, useDomainResource } from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import { StatePanel } from "../../components/StatePanel";
import type { Conversation } from "../people/types";
import { GroupOverview } from "./GroupOverview";
import GroupInsights from "./GroupInsights";
import "../people/people.css";
import "./memory.css";
import "./groups.css";

export default function GroupDirectoryPage() {
  const workspace = useRoleWorkspace();
  const pages = useMemoryCursor({ actor_id: workspace.actor }, "groups");
  const cursor = pages.cursor;
  const [selected, setSelected] = useState("");
  const [query, setQuery] = useState("");
  const page = useDomainResource<Page<Conversation>>(
    "identity/conversations",
    {
      ...actorScope(workspace.actor),
      cursor,
      limit: 100,
      kind: "group",
    },
    !!workspace.actor,
  );
  const groups = page.data?.items.filter((row) => row.kind === "group") ?? [];
  const chosen =
    groups.find((row) => row.conversation_id === selected) ?? groups[0];
  if (!workspace.actor)
    return (
      <StatePanel kind="empty" title="请选择角色">
        <p>群会话和群内认识按实际角色授权读取。</p>
      </StatePanel>
    );
  return (
    <div className="group-directory-page scroll-page" key={workspace.actor}>
      <section className="panel">
        <div className="section-heading">
          <div>
            <h2>群会话与共同话题</h2>
            <p className="muted">
              读取已登记的群会话和有来源的认识。群内的一次发言保留原作者，不推及所有成员。
            </p>
          </div>
          <button className="button" onClick={() => void page.refresh()}>
            <RefreshCw size={16} />
            刷新群会话
          </button>
        </div>
        {page.error && <p role="alert">{page.error}</p>}
        {page.loading && <p>正在读取授权群会话…</p>}
      </section>
      <div className="group-directory-layout scroll-layout">
        <section className="panel group-directory-list">
          <label>
            查找本批群会话
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <ul>
            {groups
              .filter((row) =>
                `${row.label} ${row.target_ref} ${row.conversation_id}`
                  .toLocaleLowerCase()
                  .includes(query.toLocaleLowerCase()),
              )
              .map((row) => (
                <li key={row.conversation_id}>
                  <button
                    aria-pressed={
                      chosen?.conversation_id === row.conversation_id
                    }
                    onClick={() => setSelected(row.conversation_id)}
                  >
                    <UsersRound size={18} />
                    <span>
                      <strong>{row.label}</strong>
                      <small>
                        {row.adapter} · {row.target_ref}
                      </small>
                    </span>
                  </button>
                </li>
              ))}
          </ul>
          {!page.loading && !groups.length && <p>本批没有已授权的群会话。</p>}
          <div className="models-actions">
            {cursor && (
              <button
                className="button"
                onClick={() => {
                  pages.first();
                  setSelected("");
                }}
              >
                回到首批
              </button>
            )}
            {page.data?.next_cursor && (
              <button
                className="button"
                disabled={page.loading}
                onClick={() => {
                  pages.next(page.data!.next_cursor!);
                  setSelected("");
                }}
              >
                下一批会话
              </button>
            )}
          </div>
        </section>
        {chosen && (
          <div
            className="group-directory-content scroll-pane"
            key={chosen.conversation_id}
          >
            <GroupOverview group={chosen} />
            <GroupInsights group={chosen} />
          </div>
        )}
      </div>
    </div>
  );
}
