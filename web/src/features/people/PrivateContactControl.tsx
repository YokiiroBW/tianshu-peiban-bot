import { useEffect, useState } from "react";
import {
  useDomainCommand,
  useDomainResource,
  type DomainTarget,
} from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";

type Mode = "disabled" | "passive" | "active";
type Policy = {
  mode: Mode;
  token: string;
  contact_revision: number | null;
  can_manage: boolean;
  links: { connection_id: string; external_id: string; label: string }[];
};
const descriptions: Record<Mode, string> = {
  disabled: "不接收、不回复、不主动联系。新消息在入口过滤，不触发后续处理。",
  passive: "正常接收消息并回复，由用户发起聊天；BOT 不主动联系。",
  active: "正常接收并回复，也允许 BOT 根据状态、联系节奏和可用时间主动私聊。",
};

export function PrivateContactControl({
  scope,
  changed,
}: {
  scope: DomainTarget;
  changed?: () => Promise<void>;
}) {
  const view = useDomainResource<Policy>(
    "life/private-contact-policy",
    scope,
    !!scope.actor_id && !!scope.person_id,
  );
  const command = useDomainCommand<Policy>("private-contact-policy", "actor");
  const [mode, setMode] = useState<Mode>("passive");
  useEffect(() => {
    if (view.data) setMode(view.data.mode);
  }, [view.data]);
  return (
    <section className="people-config-form">
      <h4>私聊模式</h4>
      {view.loading && <p role="status">正在读取私聊模式…</p>}
      {view.error && <p role="alert">{view.error}</p>}
      {view.data &&
        (view.data.links.length ? (
          <form
            className="people-config-form"
            onSubmit={async (event) => {
              event.preventDefault();
              if (
                await command.run(
                  "life/private-contact-policy.save",
                  { ...scope, mode, policy_token: view.data!.token },
                  view.data!.contact_revision,
                )
              ) {
                await view.refresh();
                await changed?.();
              }
            }}
          >
            <label>
              联系状态
              <select
                aria-label="私聊模式"
                value={mode}
                disabled={!view.data.can_manage || command.busy}
                onChange={(event) => setMode(event.target.value as Mode)}
              >
                <option value="disabled">禁用</option>
                <option value="passive">被动</option>
                <option value="active">主动</option>
              </select>
            </label>
            <p>{descriptions[mode]}</p>
            <p className="muted">
              应用到当前角色与此人物关联的 {view.data.links.length} 个私聊渠道。
            </p>
            {view.data.can_manage && (
              <button className="button" disabled={command.busy}>
                保存私聊模式
              </button>
            )}
          </form>
        ) : (
          <p>尚无已关联的外部私聊。首次私聊默认被动接收和回复，不主动联系。</p>
        ))}
      <CommandFeedback {...command} />
    </section>
  );
}
