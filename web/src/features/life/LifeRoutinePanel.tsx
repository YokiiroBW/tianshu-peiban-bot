import {
  useDomainCommand,
  useDomainResource,
  actorScope,
} from "../../api/useDomainResource";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { CommandFeedback } from "../../components/CommandFeedback";
import { WeatherLocationSettings } from "./WeatherSettings";
import type { LifeState, Routine } from "./domainTypes";
import { LifeScheduleStatus } from "./LifeScheduleStatus";

type ContactPreferences = {
  revision: number;
  edit_revision: number | null;
  inherited: boolean;
  timezone: string;
  enabled: boolean;
  quiet_start: string;
  quiet_end: string;
  cooldown_seconds: number;
  daily_quota: number;
  unanswered_limit: number;
  expiry_seconds: number;
  responsiveness: number;
};
export function LifeRoutinePanel({
  canManage,
  state,
  refreshState,
}: {
  canManage: boolean;
  state: LifeState | null;
  refreshState: () => Promise<void>;
}) {
  const workspace = useRoleWorkspace();
  const routine = useDomainResource<Routine>(
    "life/routine",
    actorScope(workspace.actor),
  );
  const preferences = useDomainResource<ContactPreferences>(
    "life/contact-preferences",
  );
  const clock = useDomainCommand("life-routine", "actor");
  const contact = useDomainCommand("contact-preferences");
  return (
    <div className="domain-workspace">
      <section className="panel">
        <h3>生活作息</h3>
        <p className="muted">
          按角色的作息与动机持续生活，休息时只等待下一边界。这里的日常节奏不会代替明示约定。
        </p>
        {routine.error && <p role="alert">{routine.error}</p>}
        {routine.data && (
          <form
            className="domain-form"
            key={routine.data.revision ?? "new"}
            onSubmit={(event) => {
              event.preventDefault();
              const data = new FormData(event.currentTarget);
              void clock
                .run(
                  "life/routine.save",
                  {
                    enabled: data.get("enabled") === "on",
                    timezone: data.get("timezone"),
                    wake_at: data.get("wake_at"),
                    sleep_at: data.get("sleep_at"),
                    idle_seconds: Number(data.get("idle_minutes")) * 60,
                  },
                  routine.data!.revision,
                )
                .then((result) => {
                  if (result) {
                    void routine.refresh();
                    void refreshState();
                  }
                });
            }}
          >
            <label className="inline-check">
              <input
                name="enabled"
                type="checkbox"
                defaultChecked={routine.data.enabled}
                disabled={!canManage}
              />
              持续推进生活
            </label>
            <div className="domain-grid">
              <label>
                时区
                <input
                  name="timezone"
                  defaultValue={routine.data.timezone}
                  required
                  readOnly={!canManage}
                />
              </label>
              <label>
                起床
                <input
                  name="wake_at"
                  type="time"
                  defaultValue={routine.data.wake_at}
                  required
                  disabled={!canManage}
                />
              </label>
              <label>
                入睡
                <input
                  name="sleep_at"
                  type="time"
                  defaultValue={routine.data.sleep_at}
                  required
                  disabled={!canManage}
                />
              </label>
              <label>
                空闲时下次安排间隔（分钟）
                <input
                  name="idle_minutes"
                  type="number"
                  min="15"
                  max="1440"
                  defaultValue={routine.data.idle_seconds / 60}
                  required
                  readOnly={!canManage}
                />
              </label>
            </div>
            <LifeScheduleStatus routine={routine.data} />
            {canManage && (
              <button className="button primary" disabled={clock.busy}>
                保存作息
              </button>
            )}
            <CommandFeedback {...clock} />
          </form>
        )}
      </section>
      <section className="panel">
        <h3>联系节奏</h3>
        <p className="muted">
          {workspace.conversation
            ? `当前对象：${workspace.conversation.label}`
            : "当前设置用于角色的默认联系节奏"}
          。明确说好的时间按约定执行；普通分享使用以下节奏。私聊是否主动联系以用户档案中的私聊模式为准，默认不主动。
        </p>
        {preferences.error && <p role="alert">{preferences.error}</p>}
        {preferences.data && (
          <form
            className="domain-form"
            key={`${workspace.conversation?.person_id ?? "default"}:${preferences.data.edit_revision}`}
            onSubmit={(event) => {
              event.preventDefault();
              const data = new FormData(event.currentTarget);
              void contact
                .run(
                  "life/contact-preferences.save",
                  {
                    enabled: data.get("enabled") === "on",
                    timezone: data.get("timezone"),
                    quiet_start: data.get("quiet_start"),
                    quiet_end: data.get("quiet_end"),
                    cooldown_seconds: Number(data.get("cooldown")) * 60,
                    daily_quota: Number(data.get("daily_quota")),
                    unanswered_limit: Number(data.get("unanswered_limit")),
                    expiry_seconds: Number(data.get("expiry_seconds")),
                    responsiveness: Number(data.get("responsiveness")),
                  },
                  preferences.data!.edit_revision,
                )
                .then((result) => {
                  if (result) void preferences.refresh();
                });
            }}
          >
            {preferences.data.inherited && (
              <p className="muted">
                当前显示角色默认值，保存后为这位人物单独使用。
              </p>
            )}
            <label className="inline-check">
              <input
                name="enabled"
                type="checkbox"
                defaultChecked={preferences.data.enabled}
                disabled={!canManage}
              />
              {workspace.conversation?.person_id
                ? "允许此人物的普通主动联系"
                : "允许已开启主动参与的群使用此联系节奏"}
            </label>
            <div className="domain-grid">
              <label>
                时区
                <input
                  name="timezone"
                  defaultValue={preferences.data.timezone}
                  required
                  readOnly={!canManage}
                />
              </label>
              <label>
                安静开始
                <input
                  name="quiet_start"
                  type="time"
                  defaultValue={preferences.data.quiet_start}
                  required
                  disabled={!canManage}
                />
              </label>
              <label>
                安静结束
                <input
                  name="quiet_end"
                  type="time"
                  defaultValue={preferences.data.quiet_end}
                  required
                  disabled={!canManage}
                />
              </label>
              <label>
                联系间隔（分钟）
                <input
                  name="cooldown"
                  type="number"
                  min="0"
                  max="10080"
                  defaultValue={preferences.data.cooldown_seconds / 60}
                  required
                  readOnly={!canManage}
                />
              </label>
              <label>
                每日普通联系上限
                <input
                  name="daily_quota"
                  type="number"
                  min="0"
                  max="100"
                  defaultValue={preferences.data.daily_quota}
                  required
                  readOnly={!canManage}
                />
              </label>
              <label>
                未回应时最多保留几次联系
                <input
                  name="unanswered_limit"
                  type="number"
                  min="0"
                  max="100"
                  defaultValue={preferences.data.unanswered_limit}
                  required
                  readOnly={!canManage}
                />
              </label>
              <label>
                普通分享有效期（秒）
                <input
                  name="expiry_seconds"
                  type="number"
                  min="60"
                  max="2592000"
                  step="1"
                  defaultValue={preferences.data.expiry_seconds}
                  required
                  readOnly={!canManage}
                />
                <small className="muted">
                  从候选产生时计算；过期后不再补发。明示约定保留自己的时间窗。
                </small>
              </label>
              <label>
                对普通分享的积极程度
                <input
                  name="responsiveness"
                  type="range"
                  min="0"
                  max="1"
                  step="0.1"
                  defaultValue={preferences.data.responsiveness}
                  disabled={!canManage}
                />
              </label>
            </div>
            {canManage && (
              <button className="button primary" disabled={contact.busy}>
                保存联系节奏
              </button>
            )}
            <CommandFeedback {...contact} />
          </form>
        )}
      </section>
      {state && canManage && (
        <WeatherLocationSettings state={state} refresh={refreshState} />
      )}
    </div>
  );
}
