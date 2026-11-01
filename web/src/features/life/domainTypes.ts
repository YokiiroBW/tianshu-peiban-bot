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
export function timeLabel(value: string | null, zone?: string) {
  return value
    ? new Date(value).toLocaleString(
        "zh-CN",
        zone ? { timeZone: zone } : undefined,
      )
    : "尚未确定";
}
