import { useEffect, useState } from "react";
import {
  useDomainCommand,
  useDomainResource,
} from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";
import { SourceEvidence } from "../../components/SourceEvidence";
import type { DayPlanView, DaySlot } from "./domainTypes";
import {
  activityWaitingReason,
  lifeCommandError,
  timeLabel,
} from "./domainTypes";
import { localDay, zonedInput, zonedInstant } from "./scheduleTime";
import { ActivityProgress } from "./ActivityProgress";
import { LifePlanningStatus } from "./LifeScheduleStatus";

const slotLabels: Record<DaySlot["state"], string> = {
  planned: "待开始",
  running: "进行中",
  waiting_result: "等待原执行结果",
  partial: "已完成一部分",
  completed: "本次安排结束",
  cancelled: "已取消",
  carried: "进度留待下次",
};

const applicationLabels = {
  scheduled: "已排入日程",
  intentional_idle: "主动留白",
  deferred_no_slot: "暂时没有合适时间，等待再安排",
  stale_state: "依据已变化，等待重新考虑",
  invalid_proposal: "提议无效，未排入日程",
} as const;

const timingLabels: [keyof NonNullable<DayPlanView["timings_ms"]>, string][] = [
  ["view_total", "服务内部读取视图"],
  ["identity_check", "身份校验"],
  ["state_lookup", "生活状态读取"],
  ["snapshot_database", "日程快照数据库读取"],
  ["snapshot_source_check", "日程来源校验"],
  ["application_database", "日程应用结果读取"],
  ["planning_database", "规划状态读取"],
  ["projection", "页面视图整理"],
  ["json_serialization", "JSON 序列化"],
  ["http_total", "HTTP 总耗时"],
];

function DayPlanTimings({ timings }: { timings: NonNullable<DayPlanView["timings_ms"]> }) {
  return <details aria-label="日程读取耗时">
    <summary>查看日程读取耗时</summary>
    <p className="muted">服务内部读取视图不包含路由鉴权、JSON 序列化或网络。阶段可能重叠，不能相加作为整个请求耗时。</p>
    <dl>
      {timingLabels.map(([key, label]) => {
        const value = timings[key];
        const missing = key === "application_database" ? "未执行" :
          key === "json_serialization" || key === "http_total" ? "未测量" : "未记录";
        return <div key={key}><dt>{label}</dt><dd>{typeof value === "number" && Number.isFinite(value) ? `${value.toLocaleString("zh-CN", { maximumFractionDigits: 6 })} 毫秒` : missing}</dd></div>;
      })}
    </dl>
  </details>;
}

export function DayPlanPanel({
  canManage,
  timezone,
  refreshState,
}: {
  canManage: boolean;
  timezone: string;
  refreshState: () => Promise<void>;
}) {
  const [requestedDay, setDay] = useState<string | null>(null);
  const view = useDomainResource<DayPlanView>(
    "life/day-plan",
    requestedDay ? { day: requestedDay } : {},
    true,
    15000,
  );
  const day = requestedDay ?? view.data?.day ?? localDay(timezone);
  const action = useDomainCommand("day-plan:" + day);
  const [moving, setMoving] = useState<DaySlot | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setMoving(null);
    setError("");
  }, [day, view.accessLost]);
  const zone = view.data?.plan?.timezone ?? timezone;
  const slots = [...(view.data?.slots ?? [])].sort(
    (left, right) => Date.parse(left.starts_at) - Date.parse(right.starts_at),
  );
  const editableDay =
    day === localDay(zone) && view.data?.plan?.state !== "closed";
  const next =
    slots.find(
      (slot) =>
        slot.kind === "activity" &&
        ["running", "waiting_result"].includes(slot.state),
    ) ??
    slots.find((slot) => slot.kind === "activity" && slot.state === "planned");
  async function refresh() {
    await Promise.all([view.refresh(), refreshState()]);
  }
  return (
    <section className="life-card life-day-plan">
      <div className="section-heading">
        <div>
          <h3>这一天的安排</h3>
          <p className="muted">
            留几件想做的事，也留出自由时间。等待回执时会保留原来的进度。
          </p>
        </div>
        <div className="domain-actions">
          <label>
            查看日期
            <input
              aria-label="安排日期"
              type="date"
              value={day}
              onChange={(event) => {
                if (event.target.value) setDay(event.target.value);
              }}
            />
          </label>
          <button
            className="button"
            disabled={view.loading}
            onClick={() => void refresh()}
          >
            刷新安排
          </button>
        </div>
      </div>
      <p className="muted">
        按 {zone} 显示时间
        {view.data?.plan?.state === "closed"
          ? " · 这一天已经结束，未完成的进度仍保留"
          : ""}
        。
      </p>
      {view.error && <p role="alert">{view.error}</p>}
      {view.loading && !view.data && <p role="status">正在读取这一天的安排…</p>}
      {view.data && (
        <>
          {next && (
            <div className="life-next-step">
              <small>接下来</small>
              <strong>{next.title}</strong>
              <span>
                {next.waiting_reason
                  ? activityWaitingReason(next.waiting_reason)
                  : timeLabel(next.starts_at, zone)}
              </span>
            </div>
          )}
          <ol className="life-day-slots">
            {slots.map((slot) => (
              <li
                className={`life-day-slot ${slot.kind === "free" ? "free" : ""}`}
                key={slot.id}
              >
                <div className="life-slot-time">
                  <time>
                    {new Date(slot.starts_at).toLocaleTimeString("zh-CN", {
                      timeZone: zone,
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </time>
                  <span>
                    —{" "}
                    {new Date(slot.ends_at).toLocaleTimeString("zh-CN", {
                      timeZone: zone,
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                </div>
                <div className="life-slot-content">
                  <header>
                    <strong>
                      {slot.kind === "free" ? "自由时间" : slot.title}
                    </strong>
                    <span className="badge">{slotLabels[slot.state]}</span>
                  </header>
                  {slot.kind === "free" ? (
                    <p className="muted">
                      {slot.reason || "可以随心度过，不需要把这段空白填满。"}
                    </p>
                  ) : (
                    <>
                      {slot.waiting_reason && (
                        <p>{activityWaitingReason(slot.waiting_reason)}</p>
                      )}
                      {slot.source_refs?.length ? (
                        <SourceEvidence sources={slot.source_refs} />
                      ) : null}
                      {canManage && editableDay && slot.state === "planned" && (
                        <button
                          className="text-link"
                          onClick={() => {
                            setMoving(structuredClone(slot));
                            setError("");
                          }}
                        >
                          调整开始时间
                        </button>
                      )}
                    </>
                  )}
                </div>
              </li>
            ))}
          </ol>
          {!slots.length && (
            <p className="muted">
              这一天还没有具体安排。角色会在有新兴趣、目标或实际进展时作出选择。
            </p>
          )}
          <LifePlanningStatus planning={view.data.planning} timezone={zone} />
          {view.data.timings_ms && <DayPlanTimings timings={view.data.timings_ms} />}
          {view.data.application && (
            <section aria-label="本次日程选择结果">
              <h4>本次日程选择结果</h4>
              {view.data.application.items.length === 0 ? (
                <p className="muted">这次没有记录候选结果。</p>
              ) : (
                <ul>
                  {view.data.application.items.map((item) => (
                    <li key={item.candidate_key}>
                      <strong>{applicationLabels[item.status] ?? item.status}</strong>
                      {item.reason && <> · {item.reason}</>}
                      {item.activity_id && <> · 活动 {item.activity_id}</>}
                      {item.next_due_at && <> · 下次考虑 {timeLabel(item.next_due_at, zone)}</>}
                    </li>
                  ))}
                </ul>
              )}
              {view.data.application.next_due_at && (
                <p className="muted">下一次考虑：{timeLabel(view.data.application.next_due_at, zone)}</p>
              )}
            </section>
          )}
          {view.data.unfinished.length > 0 && (
            <section>
              <h4>还想继续的事</h4>
              <div className="domain-grid">
                {view.data.unfinished.map((item) => (
                  <article className="domain-record" key={item.id}>
                    <strong>{item.title}</strong>
                    <ActivityProgress activity={item} />
                  </article>
                ))}
              </div>
            </section>
          )}
          {view.data.plan?.source_refs.length ? (
            <SourceEvidence sources={view.data.plan.source_refs} />
          ) : null}
        </>
      )}
      {moving && (
        <form
          className="domain-form life-slot-editor"
          key={`${moving.id}:${moving.revision}`}
          onSubmit={(event) => {
            event.preventDefault();
            setError("");
            try {
              const starts_at = zonedInstant(
                String(new FormData(event.currentTarget).get("starts_at")),
                zone,
              );
              void action
                .run(
                  "life/day-plan.move",
                  { id: moving.id, starts_at },
                  moving.revision,
                )
                .then((result) => {
                  if (result) {
                    setMoving(null);
                    void refresh();
                  }
                });
            } catch (cause) {
              setError(cause instanceof Error ? cause.message : "时间无效。");
            }
          }}
        >
          <h4>调整「{moving.title}」</h4>
          <label>
            开始时间 · {zone}
            <input
              name="starts_at"
              type="datetime-local"
              defaultValue={zonedInput(moving.starts_at, zone)}
              required
            />
          </label>
          <p className="muted">
            只移动尚未开始的这一项，其他活动和留白保持原安排。
          </p>
          <div className="domain-actions">
            <button className="button primary" disabled={action.busy}>
              保存时间
            </button>
            <button
              className="button"
              type="button"
              onClick={() => setMoving(null)}
            >
              收起
            </button>
          </div>
        </form>
      )}
      {canManage && editableDay && (
        <form
          className="domain-form life-reconsider"
          onSubmit={(event) => {
            event.preventDefault();
            const reason = String(
              new FormData(event.currentTarget).get("reason") ?? "",
            ).trim();
            void action
              .run(
                "life/day-plan.reconsider",
                { reason },
                view.data?.plan?.revision ?? null,
              )
              .then((result) => {
                if (result) void refresh();
              });
          }}
        >
          <label>
            有了什么新想法？
            <input
              name="reason"
              maxLength={500}
              placeholder="例如：想把阅读放到晚一点"
            />
          </label>
          <button className="button" disabled={action.busy || view.loading}>
            重新考虑今天的安排
          </button>
          <small className="muted">
            保留已经进行的事，只调整之后还没开始的部分。
          </small>
        </form>
      )}
      {error && <p role="alert">{error}</p>}
      <CommandFeedback {...action} error={lifeCommandError(action.error)} />
    </section>
  );
}
