import { useEffect, useState } from "react";
import {
  useDomainCommand,
  useDomainResource,
} from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import { CommandFeedback } from "../../components/CommandFeedback";
import { SourceEvidence } from "../../components/SourceEvidence";
import { DetailCard } from "../../components/DetailCard";
import { SchemaFields } from "../plugins/SchemaFields";
import type { JsonSchema } from "../plugins/types";
import type { Activity, Goal } from "./domainTypes";
import { CapabilityReadiness } from "../multimodal/CapabilityReadiness";
import { ActivityProgress } from "./ActivityProgress";
import {
  activityLabels,
  activityWaitingReason,
  lifeCommandError,
  timeLabel,
} from "./domainTypes";

type Choice = {
  definition: {
    id: string;
    title: string;
    description: string;
    input_schema: JsonSchema;
    model_tool: { name: string } | null;
  };
  can_execute: boolean;
  availability: { reason: string | null };
};
function needsLaterArrangement(activity: Activity) {
  return (
    activity.waiting_reason === "follow_up_available" ||
    activity.resume_condition === "plan_later_check"
  );
}
export function LifecyclePanel({
  canManage,
  refreshState,
  section = "all",
  onReviewDay,
}: {
  canManage: boolean;
  refreshState: () => Promise<void>;
  section?: "all" | "goals" | "activities";
  onReviewDay?: () => void;
}) {
  const [goalCursor, setGoalCursor] = useState<string | null>(null);
  const [activityCursor, setActivityCursor] = useState<string | null>(null);
  const goals = useDomainResource<Page<Goal>>(
    "life/goals",
    { cursor: goalCursor, limit: 40 },
    true,
    15000,
  );
  const activities = useDomainResource<Page<Activity>>(
    "life/activities",
    { cursor: activityCursor, limit: 40 },
    section !== "goals",
    15000,
  );
  const [goal, setGoal] = useState<Goal | "new" | null>(null);
  const [activity, setActivity] = useState<Activity | "new" | null>(null);
  const action = useDomainCommand("activity-controls");
  const goalAction = useDomainCommand("goal-controls");
  useEffect(() => {
    if (goals.accessLost) setGoal(null);
  }, [goals.accessLost]);
  useEffect(() => {
    if (activities.accessLost) setActivity(null);
  }, [activities.accessLost]);
  const reload = async () => {
    await Promise.all([goals.refresh(), activities.refresh(), refreshState()]);
  };
  return (
    <div className="domain-workspace">
      <CapabilityReadiness
        ids={["content.read", "images.generate"]}
        title="阅读与主动创作能力"
      />
      {section !== "activities" && (
        <section className="life-card">
          <div className="section-heading">
            <div>
              <h3>正在关心的目标</h3>
              <p className="muted">
                目标提供方向，实际完成仍取决于活动与执行结果。
              </p>
            </div>
            {canManage && (
              <button className="button" onClick={() => setGoal("new")}>
                记下新目标
              </button>
            )}
          </div>
          {goals.error && <p role="alert">{goals.error}</p>}
          <div className="domain-grid">
            {goals.data?.items.map((item) => (
              <article className="domain-record" key={item.id}>
                <header>
                  <strong>{item.title}</strong>
                  <span className="badge">
                    {(
                      {
                        active: "在意中",
                        paused: "暂放一边",
                        completed: "已完成",
                        cancelled: "已取消",
                      } as Record<string, string>
                    )[item.state] ?? item.state}
                  </span>
                </header>
                <p>{item.motivation}</p>
                <p className="muted">
                  {item.origin === "autonomous"
                    ? "随实际经历与兴趣形成"
                    : "作者记下的方向"}
                </p>
                {item.reason && item.reason !== item.motivation && (
                  <p>形成或调整的理由：{item.reason}</p>
                )}
                <div className="life-goal-progress">
                  <progress
                    aria-label={`${item.title}实际进度`}
                    max={1}
                    value={item.progress ?? 0}
                  />
                  <span>
                    实际进度 {Math.round((item.progress ?? 0) * 100)}%
                  </span>
                </div>
                <p className="muted">
                  {goalCriteria(item)}。进度只随真实活动回执或章节保存推进。
                </p>
                {item.due_at && (
                  <small>计划时间：{timeLabel(item.due_at)}</small>
                )}
                <SourceEvidence sources={item.source_refs} />
                {canManage && (
                  <div className="domain-actions">
                    <button
                      className="text-link"
                      onClick={() => setGoal(structuredClone(item))}
                    >
                      调整目标
                    </button>
                    {["active", "paused"].includes(item.state) && (
                      <button
                        className="button"
                        disabled={goalAction.busy}
                        onClick={() => {
                          void goalAction
                            .run(
                              "life/goals.save",
                              {
                                id: item.id,
                                title: item.title,
                                motivation: item.motivation,
                                priority: item.priority,
                                due_at: item.due_at,
                                matter_id: item.matter_id,
                                source_refs: item.source_refs,
                                state:
                                  item.state === "active" ? "paused" : "active",
                                reason:
                                  item.state === "active"
                                    ? "作者暂放一边"
                                    : "作者选择继续",
                              },
                              item.revision,
                            )
                            .then((result) => {
                              if (result) void reload();
                            });
                        }}
                      >
                        {item.state === "active" ? "暂放一边" : "继续这个目标"}
                      </button>
                    )}
                    {!["completed", "cancelled"].includes(item.state) && (
                      <button
                        className="button"
                        disabled={goalAction.busy}
                        onClick={() => {
                          void goalAction
                            .run(
                              "life/goals.save",
                              {
                                id: item.id,
                                title: item.title,
                                motivation: item.motivation,
                                priority: item.priority,
                                due_at: item.due_at,
                                matter_id: item.matter_id,
                                source_refs: item.source_refs,
                                state: "cancelled",
                                reason: "作者选择放弃这个方向",
                              },
                              item.revision,
                            )
                            .then((result) => {
                              if (result) void reload();
                            });
                        }}
                      >
                        放弃这个目标
                      </button>
                    )}
                  </div>
                )}
              </article>
            ))}
          </div>
          <CommandFeedback
            {...goalAction}
            error={lifeCommandError(goalAction.error)}
          />
          {goals.data && !goals.data.items.length && (
            <p className="muted">
              还没有单独记下目标，已配置的人格兴趣仍可成为生活动机。
            </p>
          )}
          <div className="domain-actions">
            {goalCursor && (
              <button className="button" onClick={() => setGoalCursor(null)}>
                回到首批目标
              </button>
            )}
            {goals.data?.next_cursor && (
              <button
                className="button"
                onClick={() => setGoalCursor(goals.data!.next_cursor)}
              >
                更多目标
              </button>
            )}
          </div>
          {goal && (
            <GoalEditor
              key={goal === "new" ? "new" : `${goal.id}:${goal.revision}`}
              value={goal === "new" ? null : goal}
              onClose={() => setGoal(null)}
              onSaved={() => {
                setGoal(null);
                void reload();
              }}
            />
          )}
        </section>
      )}
      {section !== "goals" && (
        <section className="life-card">
          <div className="section-heading">
            <div>
              <h3>持续活动</h3>
              <p className="muted">暂停保留进度；未知的原执行只核对原任务。</p>
            </div>
            {canManage && (
              <button className="button" onClick={() => setActivity("new")}>
                安排活动
              </button>
            )}
          </div>
          {activities.error && <p role="alert">{activities.error}</p>}
          <div className="domain-grid">
            {activities.data?.items.map((item) => (
              <article className="domain-record" key={item.id}>
                <header>
                  <strong>{item.title}</strong>
                  <span className="badge">
                    {activityLabels[item.state] ?? item.state}
                  </span>
                </header>
                <ActivityProgress activity={item} />
                {!item.checkpoint_data?.capability_id && (
                  <p className="muted">
                    角色主观活动，完成记录不表示发生了现实身体活动或真实工具执行。
                  </p>
                )}
                {item.resume_condition && (
                  <p className="muted">
                    继续条件：{activityWaitingReason(item.resume_condition)}
                  </p>
                )}
                <SourceEvidence sources={item.source_refs} />
                <div className="domain-actions">
                  {item.action_id && (
                    <a
                      className="text-link"
                      href={`#/system/tasks?action=${encodeURIComponent(item.action_id)}`}
                    >
                      查看原任务
                    </a>
                  )}
                  {canManage && (
                    <>
                      {item.state === "running" && (
                        <button
                          className="button"
                          disabled={action.busy}
                          onClick={() =>
                            void action
                              .run(
                                "life/activities.pause",
                                { id: item.id },
                                item.revision,
                              )
                              .then(reload)
                          }
                        >
                          暂停
                        </button>
                      )}
                      {["planned", "paused", "dormant"].includes(item.state) &&
                        !needsLaterArrangement(item) &&
                        ![
                          "outcome_unknown",
                          "capability_running",
                          "capability_queued",
                          "cancellation_outcome_unknown",
                        ].includes(item.waiting_reason ?? "") && (
                          <button
                            className="button"
                            disabled={action.busy}
                            onClick={() =>
                              void action
                                .run(
                                  "life/activities.resume",
                                  { id: item.id },
                                  item.revision,
                                )
                                .then(reload)
                            }
                          >
                            {item.waiting_reason === "continuation_available"
                              ? "从实际位置继续"
                              : "开始或继续"}
                          </button>
                        )}
                      {needsLaterArrangement(item) && onReviewDay && (
                        <button className="button" onClick={onReviewDay}>
                          去今日安排重新考虑
                        </button>
                      )}
                      {!["completed", "cancelled"].includes(item.state) && (
                        <button
                          className="button"
                          disabled={action.busy}
                          onClick={() =>
                            void action
                              .run(
                                "life/activities.cancel",
                                { id: item.id },
                                item.revision,
                              )
                              .then(reload)
                          }
                        >
                          取消后续活动
                        </button>
                      )}
                      {!item.action_id &&
                        !["completed", "cancelled"].includes(item.state) && (
                          <button
                            className="text-link"
                            onClick={() => setActivity(structuredClone(item))}
                          >
                            编辑安排
                          </button>
                        )}
                    </>
                  )}
                </div>
              </article>
            ))}
          </div>
          <CommandFeedback {...action} error={lifeCommandError(action.error)} />
          {activity && (
            <ActivityEditor
              key={
                activity === "new"
                  ? "new"
                  : `${activity.id}:${activity.revision}`
              }
              value={activity === "new" ? null : activity}
              goals={goals.data?.items ?? []}
              onClose={() => setActivity(null)}
              onSaved={() => {
                setActivity(null);
                void reload();
              }}
            />
          )}
          <div className="domain-actions">
            {activityCursor && (
              <button
                className="button"
                onClick={() => setActivityCursor(null)}
              >
                回到首批活动
              </button>
            )}
            {activities.data?.next_cursor && (
              <button
                className="button"
                onClick={() => setActivityCursor(activities.data!.next_cursor)}
              >
                更多活动
              </button>
            )}
          </div>
          {activities.data && !activities.data.items.length && (
            <p className="muted">
              还没有持续活动。新的实际经历或目标会影响下一次选择。
            </p>
          )}
        </section>
      )}
    </div>
  );
}

function goalCriteria(goal: Goal) {
  const criteria = goal.criteria;
  if (!criteria?.kind) return "尚未约定自动完成条件，可在调整目标中补充";
  if (criteria.kind === "chapter_saved") {
    return `完成条件：实际保存所选作品章节 ${criteria.target_count} 章`;
  }
  const labels = {
    completed_activity: "完成相关活动",
    reading_complete: "完成约定阅读范围",
    created_artifact: "取得实际创作产物",
  };
  return `完成条件：${labels[criteria.kind]} ${criteria.target_count} 次`;
}

function GoalEditor({
  value,
  onClose,
  onSaved,
}: {
  value: Goal | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const action = useDomainCommand(`goal:${value?.id ?? "new"}`);
  const choices = useDomainResource<{ items: Choice[] }>(
    "capabilities/discover",
  );
  const [criteriaKind, setCriteriaKind] = useState(value?.criteria?.kind ?? "");
  const [workCursor, setWorkCursor] = useState<string | null>(null);
  const [workId, setWorkId] = useState(
    value?.criteria?.kind === "chapter_saved" ? value.criteria.work_id : "",
  );
  const [workTitle, setWorkTitle] = useState("");
  const works = useDomainResource<Page<{ id: string; title: string }>>(
    "life/works",
    { cursor: workCursor, limit: 30 },
    criteriaKind === "chapter_saved",
  );
  const fixedCriteria = (value?.progress ?? 0) > 0;
  return (
    <DetailCard
      title={value ? "调整目标" : "记下新目标"}
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <form
        className="domain-form"
        onSubmit={(event) => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          void action
            .run(
              "life/goals.save",
              {
                ...(value ? { id: value.id, matter_id: value.matter_id } : {}),
                title: form.get("title"),
                motivation: form.get("motivation"),
                reason: form.get("reason"),
                ...(value ? { source_refs: value.source_refs } : {}),
                ...(!fixedCriteria
                  ? {
                      criteria: criteriaKind
                        ? {
                            kind: criteriaKind,
                            capability_id:
                              criteriaKind === "chapter_saved"
                                ? "writing.request"
                                : String(form.get("capability_id") ?? ""),
                            target_count: Number(form.get("target_count")),
                            ...(criteriaKind === "chapter_saved"
                              ? { work_id: workId }
                              : {}),
                          }
                        : {},
                    }
                  : {}),
                priority: Number(form.get("priority")),
                state: form.get("state"),
                due_at: form.get("due_at")
                  ? new Date(String(form.get("due_at"))).toISOString()
                  : null,
              },
              value?.revision ?? null,
            )
            .then((result) => {
              if (result) onSaved();
            });
        }}
      >
        <label>
          想做的事
          <input
            name="title"
            defaultValue={value?.title ?? ""}
            required
            maxLength={1000}
          />
        </label>
        <label>
          为什么在意
          <textarea
            name="motivation"
            defaultValue={value?.motivation ?? ""}
            maxLength={4000}
          />
        </label>
        <label>
          这次调整的理由
          <textarea
            name="reason"
            defaultValue={value?.reason ?? ""}
            maxLength={500}
            placeholder="想继续、暂放一边，或为什么改变方向"
          />
        </label>
        <fieldset className="life-goal-criteria" disabled={fixedCriteria}>
          <legend>怎样算走到这一步</legend>
          <label>
            完成方式
            <select
              value={criteriaKind}
              onChange={(event) =>
                setCriteriaKind(event.target.value as typeof criteriaKind)
              }
            >
              <option value="">暂不设自动完成条件</option>
              <option value="completed_activity">完成相关活动</option>
              <option value="reading_complete">读完约定范围</option>
              <option value="created_artifact">取得实际创作产物</option>
              <option value="chapter_saved">实际保存作品章节</option>
            </select>
          </label>
          {criteriaKind && (
            <>
              {criteriaKind === "chapter_saved" ? (
                <>
                  <label>
                    对应作品
                    <select
                      name="work_id"
                      value={workId}
                      onChange={(event) => {
                        setWorkId(event.target.value);
                        setWorkTitle(
                          works.data?.items.find(
                            (item) => item.id === event.target.value,
                          )?.title ?? "",
                        );
                      }}
                      required
                    >
                      <option value="">选择这次想继续写的作品</option>
                      {workId &&
                        !works.data?.items.some(
                          (item) => item.id === workId,
                        ) && (
                          <option value={workId}>
                            {workTitle || "当前约定的作品（不在此页）"}
                          </option>
                        )}
                      {works.data?.items.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.title}
                        </option>
                      ))}
                    </select>
                  </label>
                  <p className="muted">
                    只在所选作品的章节正文实际保存后计入进度，创作请求排队不算完成。
                  </p>
                  {works.loading && !works.data && (
                    <p role="status">正在读取作品…</p>
                  )}
                  {works.error && <p role="alert">{works.error}</p>}
                  {works.data && !works.data.items.length && !workCursor && (
                    <p className="muted">
                      还没有可选作品，请先在创作中建立作品。
                    </p>
                  )}
                  <div className="domain-actions">
                    {workCursor && (
                      <button
                        className="button"
                        type="button"
                        disabled={works.loading}
                        onClick={() => setWorkCursor(null)}
                      >
                        回到首批作品
                      </button>
                    )}
                    {works.data?.next_cursor && (
                      <button
                        className="button"
                        type="button"
                        disabled={works.loading}
                        onClick={() => setWorkCursor(works.data!.next_cursor)}
                      >
                        更多作品
                      </button>
                    )}
                  </div>
                </>
              ) : (
                <label>
                  对应的活动能力
                  <select
                    name="capability_id"
                    defaultValue={value?.criteria?.capability_id ?? ""}
                    required
                  >
                    <option value="">选择当前可用的能力</option>
                    {value?.criteria?.capability_id &&
                      !choices.data?.items?.some(
                        (item) =>
                          item.definition.id === value.criteria?.capability_id,
                      ) && (
                        <option value={value.criteria.capability_id}>
                          当前约定的能力
                        </option>
                      )}
                    {choices.data?.items
                      ?.filter(
                        (item) =>
                          item.can_execute && item.definition.model_tool,
                      )
                      .map((item) => (
                        <option
                          key={item.definition.id}
                          value={item.definition.id}
                        >
                          {item.definition.title}
                        </option>
                      ))}
                  </select>
                </label>
              )}
              <label>
                {criteriaKind === "chapter_saved"
                  ? "实际保存章数"
                  : "实际完成次数"}
                <input
                  name="target_count"
                  type="number"
                  min={1}
                  max={50}
                  defaultValue={value?.criteria?.target_count ?? 1}
                  required
                />
              </label>
            </>
          )}
        </fieldset>
        {fixedCriteria && (
          <p className="muted">已有实际进度，完成方式沿用原来的约定。</p>
        )}
        {choices.error && <p role="alert">{choices.error}</p>}
        <label>
          状态
          <select name="state" defaultValue={value?.state ?? "active"}>
            <option value="active">在意中</option>
            <option value="paused">暂放一边</option>
            <option value="completed">已完成</option>
            <option value="cancelled">已取消</option>
          </select>
        </label>
        <label>
          优先程度
          <input
            name="priority"
            type="range"
            min="0"
            max="1"
            step="0.1"
            defaultValue={value?.priority ?? 0.5}
          />
        </label>
        <label>
          计划时间
          <input
            name="due_at"
            type="datetime-local"
            defaultValue={value?.due_at ? localInput(value.due_at) : ""}
          />
        </label>
        <div className="domain-actions">
          <button className="button primary" disabled={action.busy}>
            保存目标
          </button>
          <button className="button" type="button" onClick={onClose}>
            收起
          </button>
        </div>
        <CommandFeedback {...action} error={lifeCommandError(action.error)} />
      </form>
    </DetailCard>
  );
}

function ActivityEditor({
  value,
  goals,
  onClose,
  onSaved,
}: {
  value: Activity | null;
  goals: Goal[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const choices = useDomainResource<{ items: Choice[] }>(
    "capabilities/discover",
  );
  const [capability, setCapability] = useState(
    value?.checkpoint_data.capability_id ?? "",
  );
  const [argumentsValue, setArguments] = useState<Record<string, unknown>>(
    value?.checkpoint_data.arguments ?? {},
  );
  const action = useDomainCommand(`activity:${value?.id ?? "new"}`);
  const selected = choices.data?.items.find(
    (choice) => choice.definition.id === capability,
  );
  return (
    <DetailCard
      title="活动安排"
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <form
        className="domain-form"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          void action
            .run(
              "life/activities.save",
              {
                ...(value ? { id: value.id } : {}),
                title: data.get("title"),
                kind: capability
                  ? data.get("kind")
                  : data.get("fictional_kind"),
                state: value?.state ?? "planned",
                goal_id: data.get("goal_id") || null,
                capability_id: capability || null,
                arguments: capability ? argumentsValue : {},
                next_due_at: data.get("due_at")
                  ? new Date(String(data.get("due_at"))).toISOString()
                  : null,
                waiting_reason: value?.waiting_reason,
                resume_condition: value?.resume_condition,
              },
              value?.revision ?? null,
            )
            .then((result) => {
              if (result) onSaved();
            });
        }}
      >
        <label>
          活动名称
          <input
            name="title"
            defaultValue={value?.title ?? ""}
            required
            maxLength={1000}
          />
        </label>
        <label>
          关联目标
          <select name="goal_id" defaultValue={value?.goal_id ?? ""}>
            <option value="">不关联单独目标</option>
            {value?.goal_id &&
              !goals.some((goal) => goal.id === value.goal_id) && (
                <option value={value.goal_id}>
                  当前关联目标 · {value.goal_id}
                </option>
              )}
            {goals.map((goal) => (
              <option key={goal.id} value={goal.id}>
                {goal.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          实际能力
          <select
            value={capability}
            onChange={(event) => {
              setCapability(event.target.value);
              setArguments({});
            }}
          >
            <option value="">角色生活中的休息、反思或小屋活动</option>
            {choices.data?.items
              .filter(
                (choice) => choice.can_execute && choice.definition.model_tool,
              )
              .map((choice) => (
                <option key={choice.definition.id} value={choice.definition.id}>
                  {choice.definition.title}
                </option>
              ))}
          </select>
        </label>
        {choices.error && <p role="alert">{choices.error}</p>}
        {capability ? (
          <>
            <label>
              活动类别
              <input
                name="kind"
                defaultValue={value?.kind ?? "software"}
                required
              />
            </label>
            {selected && (
              <>
                <p className="muted">{selected.definition.description}</p>
                <SchemaFields
                  schema={selected.definition.input_schema}
                  value={argumentsValue}
                  onChange={(next) =>
                    setArguments(next as Record<string, unknown>)
                  }
                />
              </>
            )}
          </>
        ) : (
          <label>
            角色活动
            <select name="fictional_kind" defaultValue={value?.kind ?? "rest"}>
              <option value="rest">休息</option>
              <option value="reflection">想一想</option>
              <option value="room">在小屋里</option>
            </select>
          </label>
        )}
        <label>
          开始时间
          <input
            type="datetime-local"
            name="due_at"
            defaultValue={
              value?.next_due_at ? localInput(value.next_due_at) : ""
            }
          />
        </label>
        <div className="domain-actions">
          <button
            className="button primary"
            disabled={action.busy || (!!capability && !selected)}
          >
            保存活动
          </button>
          <button className="button" type="button" onClick={onClose}>
            收起
          </button>
        </div>
        <CommandFeedback {...action} error={lifeCommandError(action.error)} />
      </form>
    </DetailCard>
  );
}
export function localInput(value: string) {
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}
