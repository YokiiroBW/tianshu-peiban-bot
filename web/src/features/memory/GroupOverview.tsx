import type { Conversation } from "../people/types";
import { ReplyPermissions } from "../people/ReplyPermissions";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { humanizeCode } from "../../design/humanize";

export function GroupOverview({ group }: { group: Conversation }) {
  const workspace = useRoleWorkspace();
  return (
    <section className="curio-card panel group-overview">
      <h2>{group.label}</h2>
      <p className="muted">
        <span className="celadon-tag">{humanizeCode(group.adapter, "聊天渠道")}</span> ·{" "}
        <span className="meta-tag">{group.target_ref}</span> · 修订{" "}
        {group.revision}
      </p>
      <dl className="kv-list" style={{ margin: "12px 0" }}>
        <div className="kv-row">
          <dt className="k">登记会话</dt>
          <dd className="v">
            {group.label || "未命名会话"}
          </dd>
        </div>
        <div className="kv-row">
          <dt className="k">实际角色</dt>
          <dd className="v">
            {workspace.roles.find((role) => role.id === group.actor_id)?.name || "关联角色"}
          </dd>
        </div>
      </dl>
      <details>
        <summary className="ink-link">观察与回复策略</summary>
        <ReplyPermissions
          actorId={group.actor_id}
          conversationId={group.conversation_id}
        />
      </details>
    </section>
  );
}
