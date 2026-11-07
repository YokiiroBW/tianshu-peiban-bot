import { useEffect, useState } from "react";
import {
  useDomainCommand,
  useDomainResource,
  type DomainTarget,
} from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";
import { PrivateContactControl } from "./PrivateContactControl";
type Preferences = {
  revision: number;
  timezone: string;
  quiet_start: string;
  quiet_end: string;
  cooldown_seconds: number;
  daily_quota: number;
  unanswered_limit: number;
  enabled: boolean;
  edit_revision?: number | null;
  inherited?: boolean;
  availability?: string;
};
export function ProactivePanel({ scope }: { scope: DomainTarget }) {
  const view = useDomainResource<Preferences>(
    "life/contact-preferences",
    scope,
    !!scope.actor_id && !!scope.person_id,
  );
  const command = useDomainCommand<unknown>(
    "person-contact-preferences",
    "actor",
  );
  const [draft, setDraft] = useState<Preferences | null>(null);
  useEffect(() => setDraft(view.data), [view.data]);
  return (
    <section>
      <h3>主动联系</h3>
      <p className="muted">
        通过私聊模式选择是否允许主动联系。下方只调整联系节奏，已约定的提醒另行执行。
      </p>
      <PrivateContactControl scope={scope} changed={view.refresh} />
      {view.error && <p role="alert">{view.error}</p>}
      {view.loading && <p role="status">正在读取联系设置…</p>}
      {draft && (
        <form
          className="people-config-form"
          onSubmit={async (event) => {
            event.preventDefault();
            if (
              await command.run(
                "life/contact-preferences.save",
                { ...scope, ...draft },
                draft.edit_revision === null || draft.inherited
                  ? null
                  : (draft.edit_revision ?? draft.revision),
              )
            )
              await view.refresh();
          }}
        >
          <h4>联系节奏</h4>
          <label>
            时区
            <input
              required
              value={draft.timezone}
              onChange={(event) =>
                setDraft({ ...draft, timezone: event.target.value })
              }
            />
          </label>
          <label>
            安静时段开始
            <input
              type="time"
              required
              value={draft.quiet_start}
              onChange={(event) =>
                setDraft({ ...draft, quiet_start: event.target.value })
              }
            />
          </label>
          <label>
            安静时段结束
            <input
              type="time"
              required
              value={draft.quiet_end}
              onChange={(event) =>
                setDraft({ ...draft, quiet_end: event.target.value })
              }
            />
          </label>
          <label>
            联系间隔（秒）
            <input
              type="number"
              min={0}
              max={604800}
              value={draft.cooldown_seconds}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  cooldown_seconds: Number(event.target.value),
                })
              }
            />
          </label>
          <label>
            每日普通联系上限
            <input
              type="number"
              min={0}
              max={100}
              value={draft.daily_quota}
              onChange={(event) =>
                setDraft({ ...draft, daily_quota: Number(event.target.value) })
              }
            />
          </label>
          <label>
            未获回复上限
            <input
              type="number"
              min={0}
              max={100}
              value={draft.unanswered_limit}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  unanswered_limit: Number(event.target.value),
                })
              }
            />
          </label>
          <button className="button" disabled={command.busy}>
            保存偏好
          </button>
        </form>
      )}
      <CommandFeedback {...command} />
    </section>
  );
}
