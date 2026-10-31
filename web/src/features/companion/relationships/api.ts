import type { SourceRef } from "../../../api/contracts";
export type Detail = {
  detail_id: string;
  content: string;
  revision: number;
  explicit: boolean;
  source_refs: SourceRef[];
};
export type Projection = {
  pair: { actor_id: string; person_id: string };
  revision: number;
  relationship_type: string;
  display_label: string;
  summary: string;
  frozen: boolean;
  freeze_revision: number;
  frozen_at: string | null;
  boundaries: Detail[];
  preferences: Detail[];
  shared_experiences: Detail[];
  source_refs: SourceRef[];
  can_manage: boolean;
};
export type RelationshipTarget = { roleId: string; personId: string };
export const types: Record<string, string> = {
  acquaintance: "相识",
  friend: "朋友",
  family: "家人",
  partner: "伴侣",
  colleague: "同事",
  custom: "自定义",
};
