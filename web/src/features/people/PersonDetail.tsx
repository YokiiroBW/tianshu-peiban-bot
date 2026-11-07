import { useState } from "react";
import { useAuth } from "../../app/Auth";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { useDomainCommand } from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";
import RelationshipPanel from "../companion/relationships/RelationshipPanel";
import { SharedPortrait } from "./SharedPortrait";
import { Interactions } from "./Interactions";
import { ReplyPermissions } from "./ReplyPermissions";
import { ProactivePanel } from "./ProactivePanel";
import { AccountLinkPanel } from "./AccountLinkPanel";
import { personName, type Person } from "./types";

export function PersonDetail({
  person,
  actorId,
  renamed,
}: {
  person: Person;
  actorId: string;
  renamed: () => Promise<void>;
}) {
  const { session } = useAuth();
  const workspace = useRoleWorkspace();
  const manageable = session?.principal?.permissions.some(
    (value) => value === "*" || value === "identity.manage",
  );
  const [tab, setTab] = useState("portrait");
  const [name, setName] = useState(person.display_name);
  const command = useDomainCommand<Person>("person-name", "actor");
  const scope = {
    actor_id: actorId,
    person_id: person.person_id,
    conversation_id: null,
  };
  return (
    <section className="people-detail">
      <header className="people-person-header">
        <span className="role-seal-avatar people-avatar large">
          {personName(person).slice(0, 1)}
        </span>
        <div>
          <h2>{personName(person)}</h2>
          <p className="muted">
            {workspace.roles.find((role) => role.id === actorId)?.name}
            的用户档案
          </p>
        </div>
      </header>
      <nav
        className="people-detail-tabs workspace-tabs-bar"
        aria-label="人物详情"
      >
        {[
          ["portrait", "记住了什么"],
          ["messages", "相遇与反思"],
          ["permissions", "会话权限"],
          ["relationship", "相处关系"],
          ["proactive", "主动联系"],
          ["accounts", "账号关联"],
          ["overview", "基本资料"],
        ].map(([id, label]) => (
          <button
            key={id}
            className={`ws-tab${tab === id ? " active" : ""}`}
            aria-pressed={tab === id}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>
      <div
        className="people-tab-content"
        key={`${person.person_id}:${actorId}:${tab}`}
      >
        {tab === "overview" && (
          <>
            <p>
              人物身份、外部账号、应用登录和访问权限分别登记，不由昵称或关系推断。
            </p>
            {manageable && (
              <form
                className="people-config-form"
                onSubmit={async (event) => {
                  event.preventDefault();
                  if (
                    await command.run(
                      "identity/people.save",
                      { ...scope, display_name: name },
                      person.revision,
                    )
                  )
                    await renamed();
                }}
              >
                <label>
                  显示名称
                  <input
                    required
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                  />
                </label>
                <button className="button" disabled={command.busy}>
                  保存名称
                </button>
                <CommandFeedback {...command} />
              </form>
            )}
            {person.aliases && (
              <p>已登记别名：{person.aliases.join("、") || "无"}</p>
            )}
          </>
        )}
        {!actorId && tab !== "overview" && (
          <p>选择角色后可查看该角色已授权的人物范围。</p>
        )}
        {!!actorId && tab === "portrait" && (
          <SharedPortrait
            scope={scope}
            personId={person.person_id}
            displayName={person.display_name}
          />
        )}
        {!!actorId && tab === "messages" && (
          <Interactions scope={scope} personId={person.person_id} />
        )}
        {!!actorId && tab === "relationship" && (
          <RelationshipPanel
            csrf={session?.csrf ?? ""}
            selection={{ roleId: actorId, personId: person.person_id }}
          />
        )}
        {!!actorId && tab === "permissions" && (
          <ReplyPermissions actorId={actorId} personId={person.person_id} />
        )}
        {!!actorId && tab === "proactive" && <ProactivePanel scope={scope} />}
        {tab === "accounts" && (
          <AccountLinkPanel person={person} actorId={actorId} />
        )}
      </div>
    </section>
  );
}
