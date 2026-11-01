import type { MediaRef, SourceRef, Coverage } from "../../api/contracts";

export type LifeAccessView = {
  can_manage: boolean;
  can_read: boolean;
  can_read_published: boolean;
};
export type Goal = {
  id: string;
  revision: number;
  title: string;
  motivation: string;
  state: string;
  priority: number;
  matter_id: string | null;
  due_at: string | null;
  source_refs: SourceRef[];
};
export type Activity = {
  id: string;
  revision: number;
  title: string;
  kind: string;
  state: string;
  goal_id: string | null;
  next_due_at: string | null;
  waiting_reason: string | null;
  resume_condition: string | null;
  action_id: string | null;
  checkpoint_step: number;
  checkpoint_data: {
    capability_id?: string;
    arguments?: Record<string, unknown>;
    consumption?: { analysis_completed: boolean; next_position: unknown };
  };
  source_refs: SourceRef[];
};
export type Outfit = {
  id: string;
  revision: number;
  description: string;
  appearance: string;
  reference: MediaRef | null;
  activity_kinds: string[];
  source_refs: SourceRef[];
};
export type Affect = {
  feelings: {
    event_id: string;
    appraisal: string;
    attribution: string;
    control: number;
    goal_relevance: number;
  }[];
  source_refs: SourceRef[];
  missing: string[];
};
export type LifeState = {
  actor_id: string;
  revision: number;
  timezone: string;
  world_id: string;
  room_id: string;
  weather_location: string | null;
  activity: Activity | null;
  outfit: Outfit | null;
  affect: Affect;
  observed_at: string;
  missing: string[];
  room_controls: Record<string, boolean | number>;
};
export type Routine = {
  actor_id: string;
  revision: number | null;
  enabled: boolean;
  timezone: string;
  wake_at: string;
  sleep_at: string;
  idle_seconds: number;
  next_due_at: string | null;
  last_tick_at: string | null;
  schedule_status:
    "not_started" | "disabled" | "blocked" | "overdue" | "waiting";
  schedule_reason: string | null;
  observed_at: string;
  planning: {
    revision: number;
    reason: string | null;
    next_due_at: string | null;
    last_planned_at: string | null;
    generation_consumed: boolean;
    pending: boolean;
  } | null;
};
export type GrowthItem = {
  id: string;
  revision: number;
  kind: "reflection" | "interest" | "style";
  title: string;
  content: string;
  state: string;
  occurred_at: string | null;
  reason: string | null;
  evidence: SourceRef[];
  can_modify: boolean;
  conditions: Record<string, unknown>;
  counterexamples: string[];
};
export type GrowthView = {
  review: {
    revision: number;
    last_error: string | null;
    can_request_review: boolean;
  } | null;
  can_review: boolean;
  items: GrowthItem[];
  reflections: GrowthItem[];
  next_cursor: string | null;
  missing: string[];
};
export type Episode = {
  episode_id: string;
  revision: number;
  summary: string;
  occurred_at: string;
  origin_kind: string;
  source_refs: SourceRef[];
  actual_result: Record<string, unknown>;
  coverage: Coverage;
  extraction_state?: string;
  extraction_coverage?: Coverage;
};
export type Reading = {
  id: string;
  revision: number;
  source_ref: SourceRef;
  state: string;
  requested: Record<string, unknown>;
  next_position: number | null;
  covered: { coverage: Coverage; at: string }[];
};

export const activityLabels: Record<string, string> = {
  planned: "已安排",
  running: "进行中",
  paused: "已暂停",
  dormant: "等待继续",
  completed: "本次活动完成",
  cancelled: "已取消",
};
const activityWaitingReasons: Record<string, string> = {
  fictional_activity: "想象中的日常活动，正在按设定时间进行。",
  another_activity_running: "另一项活动正在进行，本项等待安排。",
  capability_required: "这项活动需要配置相应能力后才能执行。",
  capability_unavailable: "所需能力当前不可用，活动正在等待。",
  capability_queued: "原任务已排队，等待执行。",
  capability_running: "原任务正在执行，尚未取得最终结果。",
  unavailable: "所需能力当前不可用，活动正在等待。",
  queued: "原任务已排队，等待执行。",
  running: "原任务正在执行，尚未取得最终结果。",
  outcome_unknown: "原任务结果尚未确认，请先核对原执行，不能重新提交。",
  analysis_unconfirmed: "原任务已有结果，但分析尚未确认，暂未作为完成经历。",
  continuation_available: "本次已处理范围结束，可以从已保存的位置继续。",
  actor_disabled: "角色已停用，活动已暂停。",
  user_paused: "活动已按要求暂停。",
  failed: "原任务未完成，请查看原任务结果。",
  cancelled: "原任务已取消。",
  external_cancelled: "原任务已确认取消。",
  cancellation_outcome_unknown: "取消结果尚未确认，请核对原任务。",
  external_action_already_finished:
    "原任务已经结束，取消不会撤回已发生的结果。",
  external_cancellation_pending: "正在等待原任务确认取消。",
  configure_capability: "配置所需能力后再继续。",
  capability_available: "等待所需能力可用后再继续。",
  reconcile_original_action: "先核对原任务结果，不能重新执行。",
  review_original_result: "先确认原任务的分析结果。",
  continue_from_checkpoint: "从已保存的位置继续。",
  review_failure: "先查看原任务的失败原因。",
};
export function activityWaitingReason(reason: string): string {
  return activityWaitingReasons[reason] ?? "活动暂未完成，可查看原任务结果。";
}
export function timeLabel(value: string | null, zone?: string) {
  return value
    ? new Date(value).toLocaleString(
        "zh-CN",
        zone ? { timeZone: zone } : undefined,
      )
    : "尚未确定";
}
