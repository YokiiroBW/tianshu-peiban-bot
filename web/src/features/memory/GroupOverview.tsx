import type { Conversation } from "../people/types";
import { ReplyPermissions } from "../people/ReplyPermissions";

export function GroupOverview({ group }: { group: Conversation }) {
  return (
    <section className="panel group-overview">
      <h2>{group.label}</h2>
      <p className="muted">
        {group.adapter} · {group.target_ref} · 修订 {group.revision}
      </p>
      <dl>
        <dt>登记会话</dt>
        <dd>{group.conversation_id}</dd>
        <dt>实际角色</dt>
        <dd>{group.actor_id}</dd>
      </dl>
      <details>
        <summary>观察与回复策略</summary>
        <ReplyPermissions
          actorId={group.actor_id}
          conversationId={group.conversation_id}
        />
      </details>
    </section>
  );
}
