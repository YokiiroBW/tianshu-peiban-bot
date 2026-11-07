import {
  actorScope,
  useDomainCommand,
  useDomainResource,
} from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";
import { DetailCard } from "../../components/DetailCard";

export function GroupParticipationControl({
  link,
  inline = false,
}: {
  link: {
    actor_id: string;
    conversation_id: string;
    mode: string;
    mention_required: boolean;
  };
  inline?: boolean;
}) {
  const scope = {
    ...actorScope(link.actor_id),
    target_conversation_id: link.conversation_id,
  };
  const view = useDomainResource<{
    enabled: boolean;
    revision: number;
    can_manage: boolean;
  }>("conversation/participation", scope);
  const command = useDomainCommand(
    "group-participation:" + link.conversation_id,
    "actor",
  );
  const content = (
    <>
      {view.error && <p role="alert">{view.error}</p>}
      {view.data && (
        <form
          key={String(view.data.revision)}
          className="domain-form"
          onSubmit={(event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            void command
              .run(
                "conversation/participation.save",
                { ...scope, enabled: data.get("enabled") === "on" },
                view.data!.revision,
              )
              .then((answer) => {
                if (answer) void view.refresh();
              });
          }}
        >
          <label>
            <input
              name="enabled"
              type="checkbox"
              defaultChecked={view.data.enabled}
              disabled={!view.data.can_manage}
            />
            允许角色在适合的实时群话题中主动发起参与
          </label>
          <p className="muted">
            复用生活页联系时段、冷却、日额度和真实未回复状态。模型决定是否适合加入；外部宿主负责或会话仅观察时不主动投递。
          </p>
          {link.mode === "reply" && !link.mention_required && (
            <p>
              当前每条消息都会进入普通回复；主动候选用于@与关键词门槛之外的实时观察消息。
            </p>
          )}
          <button
            className="button"
            disabled={command.busy || !view.data.can_manage}
          >
            保存参与策略
          </button>
          <a className="text-link" href="#/roles/life">
            查看联系节律
          </a>
          <CommandFeedback {...command} />
        </form>
      )}
    </>
  );
  return inline ? (
    content
  ) : (
    <DetailCard title="主动加入群话题">{content}</DetailCard>
  );
}
