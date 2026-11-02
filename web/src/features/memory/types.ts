import type { Coverage, SourceRef } from "../../api/contracts";
export type MemoryTimeQuery = {
  time_mode: "current" | "valid_at" | "known_at";
  valid_at?: string;
  known_at?: string;
};
export const currentMemory: MemoryTimeQuery = { time_mode: "current" };
export const evidenceKinds: Record<string, string> = {
  claim: "事实",
  hypothesis: "可修订认识",
  matter: "事项",
  episode: "经历",
  reflection: "反思",
};
export type RecordRef = { kind: string; id: string; revision: number };
export type SemanticLink = {
  link_id: string;
  from_ref: RecordRef;
  to_ref: RecordRef;
  relation_kind: string;
  state: string;
  current_status?: string;
  valid_from: string | null;
  valid_until: string | null;
  recorded_at: string | null;
  source_refs: SourceRef[];
  from_evidence?: EvidenceUnit | null;
  to_evidence?: EvidenceUnit | null;
};
export type RevisionDetails = {
  recorded_at?: string | null;
  observed_at?: string | null;
  change_kind?: string;
  change_reason?: string;
  current_status?: string;
  relations?: SemanticLink[];
  head_revision?: number | null;
  derivation_refs?: SourceRef[];
  counterexample_refs?: SourceRef[];
  support_groups?: SourceRef[][];
};
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
  observed_at: string | null;
  state: string;
  source_refs: SourceRef[];
  supersedes: string[];
  contradictions: string[];
  matter_id?: string | null;
  memory_kind?: "fact" | "hypothesis";
} & RevisionDetails;
export type TargetResult = {
  subject_id: string;
  status: "changed" | "no_change" | "unresolved";
  processed_material_ids: string[];
  operation_ids: string[];
};
export type Consolidation = {
  isolated?: boolean;
  blocks_following?: boolean;
  held_for_refresh?: boolean;
  batch_id: string | null;
  status: string;
  action?: string;
  error_code?: string | null;
  fragment_cursor?: unknown;
  step_ids?: string[];
  schema_version?: string;
  targets?: { subject_id: string }[];
  target_results: TargetResult[];
  unresolved: {
    subject_id?: string | null;
    reason?: string;
    [key: string]: unknown;
  }[];
  context_unfulfilled?: unknown[];
  coverage?: { state?: string; missing?: string[]; [key: string]: unknown };
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
  person_id?: string | null;
  speaker_id?: string | null;
  recorded_at?: string | null;
  consolidation?: Consolidation;
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
  recorded_at?: string | null;
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
  completion_basis?: {
    kind: string;
    source_refs?: SourceRef[];
    [key: string]: unknown;
  } | null;
} & RevisionDetails;
export type EvidenceUnit = RevisionDetails & {
  kind: string;
  id: string;
  revision: number;
  subject_id: string | null;
  speaker_id: string | null;
  assertion: string;
  content: string;
  conditions: Record<string, unknown>;
  occurred_at: string | null;
  valid_from: string | null;
  valid_until: string | null;
  matter_id: string | null;
  state: string;
  source_refs: SourceRef[];
  coverage: Coverage;
  interpretation?: boolean;
  polarity?: boolean | null;
  completion_basis?: Matter["completion_basis"];
  due_at?: string | null;
};
export type Projection = {
  person_id: string | null;
  group_id: string | null;
  claims: Claim[];
  hypotheses?: Claim[];
  matters: Matter[];
  reflections: Reflection[];
  coverage: Coverage;
  group?: { subject_id: string; [key: string]: unknown } | null;
  group_consolidation?: {
    view: "current_processing";
    items: (Consolidation & { episode_id: string; revision: number })[];
    subjects: { person_id: string; display_name: string }[];
    coverage: Coverage;
  } | null;
  evidence_units?: EvidenceUnit[];
  time?: {
    mode: MemoryTimeQuery["time_mode"];
    valid_at?: string | null;
    known_at?: string | null;
  };
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
  typeof value === "string"
    ? value
    : (JSON.stringify(value, null, 2) ?? "未记录");
export function episodeOrigin(
  item: Pick<Episode, "origin_kind" | "actual_result">,
) {
  const result = item.actual_result;
  if (
    result?.imagined === true ||
    result?.kind === "imagined" ||
    result?.experience_kind === "imagined"
  )
    return "角色想象活动：完成记录支持主观反思，不能证明真实身体活动或工具执行成功。";
  return (
    (
      {
        human: "人类发言",
        observation: "观察材料",
        tool_result: "工具执行记录，以真实回执为准",
        actor: "角色表达或生活记录，保留主观语义",
      } as Record<string, string>
    )[item.origin_kind] ?? "来源类型尚未确认"
  );
}
export function time(value: string | null | undefined) {
  if (!value) return "未知（未记录）";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "未知（未记录）"
    : date.toLocaleString();
}
