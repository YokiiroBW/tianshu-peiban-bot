import type { Conversation } from "../people/types";
import { ReplyPermissions } from "../people/ReplyPermissions";
import { shortHashTag } from "../../design/humanize";

export function GroupOverview({ group }: { group: Conversation }) {
  return (
    <section className="curio-card panel group-overview">
      <h2>{group.label}</h2>
      <p className="muted">
        <span className="celadon-tag">{group.adapter}</span> ·{" "}
        <span className="meta-tag">{group.target_ref}</span> · 修订{" "}
        {group.revision}
      </p>
      <dl className="kv-list" style={{ margin: "12px 0" }}>
        <div className="kv-row">
          <dt className="k">登记会话</dt>
          <dd className="v" title={group.conversation_id}>
            {shortHashTag(group.conversation_id)}
          </dd>
        </div>
        <div className="kv-row">
          <dt className="k">实际角色</dt>
          <dd className="v" title={group.actor_id}>
            {shortHashTag(group.actor_id)}
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
