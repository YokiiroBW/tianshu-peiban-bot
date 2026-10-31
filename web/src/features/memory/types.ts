import type { Coverage, SourceRef } from "../../api/contracts";
export type Claim = {
  claim_id: string;
  revision: number;
  actor_id: string;
  speaker_id: string | null;
  subject_id: string;
  predicate: string;
  value: unknown;
  assertion: string;
  polarity: boolean;
  conditions: Record<string, unknown>;
  valid_from: string | null;
  valid_until: string | null;
  observed_at: string;
  state: string;
  source_refs: SourceRef[];
  supersedes: string[];
  contradictions: string[];
  matter_id?: string | null;
};
export type Episode = {
  episode_id: string;
  event_id: string;
  revision: number;
  origin_kind: string;
  occurred_at: string;
  summary: string;
  source_refs: SourceRef[];
  actual_result: Record<string, unknown> | null;
  coverage: Coverage;
  extraction_state: string;
  extraction_coverage: Coverage | null;
  action_id?: string | null;
};
export type Reflection = {
  proposal_id: string;
  revision: number;
  content: string;
  kind: string;
  occurred_at: string;
  source_refs: SourceRef[];
  counterexamples: string[];
  conditions: Record<string, unknown>;
  interpretation: boolean;
};
export type Matter = {
  matter_id: string;
  revision: number;
  subject_id: string | null;
  title: string;
  summary: string;
  state: string;
  due_at: string | null;
  source_refs: SourceRef[];
  claim_ids: string[];
  conditions: Record<string, unknown>;
  interpretation: boolean;
};
export type Projection = {
  person_id: string | null;
  group_id: string | null;
  claims: Claim[];
  matters: Matter[];
  reflections: Reflection[];
  coverage: Coverage;
};
export const assertionNames: Record<string, string> = {
  self_confirmed: "本人确认",
  reported: "转述",
  quoted: "引用",
  inferred: "推断",
  observed: "观察",
};
export const matterStates: Record<string, string> = {
  planned: "计划中",
  active: "进行中",
  rescheduled: "已改期",
  cancelled: "已取消",
  uncertain: "进展未确认",
  completed: "已完成",
};
export const showValue = (value: unknown) =>
  typeof value === "string" ? value : JSON.stringify(value, null, 2);
export function time(value: string | null | undefined) {
  if (!value) return "未记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "未记录" : date.toLocaleString();
}
