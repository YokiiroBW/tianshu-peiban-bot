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
  origin?: "authored" | "autonomous";
  reason?: string | null;
  progress?: number;
  criteria?:
    | {
        kind: "completed_activity" | "reading_complete" | "created_artifact";
        capability_id: string;
        target_count: number;
      }
    | Record<string, never>;
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
  checkpoint_position?: number;
  checkpoint_unit?: string;
  wake_condition?: {
    kind:
      | "user_reply"
      | "activity_completed"
      | "capability_available"
      | "matter_changed";
    subject_id: string;
  } | null;
  checkpoint_data: {
    capability_id?: string;
    arguments?: Record<string, unknown>;
    consumption?: Consumption | null;
    continuation?: Continuation | null;
    coverage?: Coverage | null;
    completed_action_id?: string;
    completion_basis?: string;
    exploration?: {
      mode?: string;
      intention?: string;
      stop_reason?: string;
      unresolved_questions?: { question: string }[];
    };
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
export type Continuation = {
  capability_id: string;
  arguments: Record<string, unknown>;
};
export type Consumption = {
  source?: SourceRef;
  requested?: { start: number; end: number };
  ranges: { start: number; end: number }[];
  frame_times?: number[];
  model_input_modalities?: string[];
  modalities?: string[];
  omitted?: string[];
  analysis_completed: boolean;
  requested_completed?: boolean;
  next_position: unknown;
  unresolved_questions?: { question: string; reason?: string }[];
};
export type DaySlot = {
  id: string;
  actor_id: string;
  day: string;
  revision: number;
  kind: "activity" | "free";
  state:
    | "planned"
    | "running"
    | "waiting_result"
    | "partial"
    | "completed"
    | "cancelled"
    | "carried";
  starts_at: string;
  ends_at: string;
  activity_id: string | null;
  title: string;
  activity_kind: string | null;
  goal_id: string | null;
  waiting_reason: string | null;
  checkpoint_data: Activity["checkpoint_data"];
  source_refs?: SourceRef[];
};
export type DayPlanView = {
  day: string;
  plan: {
    actor_id: string;
    day: string;
    revision: number;
    timezone: string;
    state: "open" | "closed";
    reason: string | null;
    source_refs: SourceRef[];
    created_at: string;
    updated_at: string;
  } | null;
  slots: DaySlot[];
  unfinished: Activity[];
  planning?: Routine["planning"];
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
    last_reviewed_at?: string | null;
    material_key?: string | null;
    attempt_id?: string | null;
    recovery?: "observe_original_attempt" | null;
    cursor?: string | null;
    pending_inputs?: number;
    pending_original_requests?: number;
    legacy_state?: "requests" | "episodes" | "complete";
    legacy_request_cursor?: string | null;
    legacy_episode_cursor?: string | null;
    legacy_missing?: string[];
    blocked_material?: {
      episode_id?: string;
      revision?: number;
      cursor?: string | null;
      [key: string]: unknown;
    } | null;
    batch?: {
      material_key: string;
      start_cursor: string | null;
      end_cursor: string | null;
      watermark: string;
      has_more: boolean;
      inputs?: [string, number][];
      legacy_material?: boolean;
    } | null;
  } | null;
  can_review: boolean;
  items: GrowthItem[];
  reflections: GrowthItem[];
  next_cursor: string | null;
  missing: string[];
};
export type { Episode } from "../memory/types";
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
  day_plan_slot: "已留在今天的安排里，到时间再开始。",
  day_finished: "今天的活动时间已结束，进度留待下次安排。",
  scheduled_authorization_unavailable:
    "原安排的授权已变化，正在等待有权管理的账号重新安排。",
  user_reply: "等待对方回应，收到回应后再继续。",
  activity_completed: "等待关联活动完成。",
  matter_changed: "等待相关事项有新进展。",
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

export function lifeCommandError(message: string) {
  const labels: Record<string, string> = {
    revision_conflict: "这份安排已经更新，请刷新后按最新内容调整。",
    day_slot_not_found: "这项安排已不存在，或当前范围无权读取，请刷新。",
    day_slot_already_started:
      "这项活动已经开始，进度会保留；请调整尚未开始的安排。",
    invalid_day_slot_window:
      "请选择这一天清醒时间内、尚未到来的开始时间，并为活动留够时长。",
    day_slot_conflict: "这个时间与另一项已安排活动重叠，请换一个时间。",
    invalid_goal_criteria:
      "请完整选择完成方式、对应能力和 1 到 50 次的实际完成次数。",
    goal_criteria_already_started: "目标已有实际进度，请保留原来的完成约定。",
    activity_action_unresolved:
      "原活动结果还没确认，请先读取原任务，进度没有丢失。",
    diary_generation_unresolved:
      "原日记任务仍在处理中，请核对或取消原任务后再整理。",
    source_unavailable: "相关原始依据已经失效，请刷新当前材料。",
  };
  return labels[message] ?? message;
}
