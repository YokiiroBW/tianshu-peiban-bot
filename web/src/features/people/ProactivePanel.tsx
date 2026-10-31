import { useEffect, useState } from "react";
import {
  useDomainCommand,
  useDomainResource,
  type DomainTarget,
} from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";
type Preferences = {
  revision: number;
  timezone: string;
  quiet_start: string;
  quiet_end: string;
  cooldown_seconds: number;
  daily_quota: number;
  unanswered_limit: number;
  enabled: boolean;
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
      <h3>主动联系偏好</h3>
      <p className="muted">
        偏好约束该角色与此人物的实际联系节奏；明确提醒沿原承诺期限执行，不从普通随兴联系筛选。
      </p>
      {view.error && <p role="alert">{view.error}</p>}
      {draft && (
        <form
          className="people-config-form"
          onSubmit={async (event) => {
            event.preventDefault();
            if (
              await command.run(
                "life/contact-preferences.save",
                { ...scope, ...draft },
                draft.revision,
              )
            )
              await view.refresh();
          }}
        >
          <label>
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(event) =>
                setDraft({ ...draft, enabled: event.target.checked })
              }
            />
            允许普通主动联系
          </label>
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
