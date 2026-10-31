import type { SourceRef, Page } from "../../api/contracts";
import type { ReadUnitRef } from "../knowledge/types";

export type LessonRef = {
  project_id: string;
  lesson_id: string;
  version: number;
  sha256: string;
};
export type LessonContent = {
  trigger: string;
  symptom: string;
  cause: string;
  correction: string;
  verification: string;
  scope: {
    platform: string;
    language: string;
    framework: string;
    applies_to: string[];
    excludes: string[];
  };
  evidence: ReadUnitRef[];
};
export type Lesson = {
  lesson_id: string;
  project_id: string;
  version: number;
  revision: number;
  state: string;
  version_state: string;
  current: boolean;
  citation_state: string;
  sha256: string;
  payload: LessonContent;
  source_refs: SourceRef[];
  reference: LessonRef;
};
export type ExperienceContent = {
  title: string;
  rule: string;
  applicability: string[];
  excludes: string[];
  counterexamples: string[];
  recheck_after: string | null;
};
export type Experience = {
  experience_id: string;
  revision: number;
  payload: ExperienceContent;
  evidence: LessonRef[] | "protected";
  source_refs: SourceRef[];
  observed_at: string;
  can_withdraw: boolean;
};
export type Checkout = {
  id: string;
  expected_branch: string;
  branch: string | null;
  branch_matches: boolean;
  head: string | null;
  dirty: boolean;
  detached: boolean;
  unborn: boolean;
  counts: Record<string, number>;
  changes: { complete: boolean; [key: string]: unknown };
  history: { commit: string; subject: string; committed_at: string }[];
  files: {
    locator: string;
    state: string;
    size: number | null;
    digest: string | null;
    freshness: string;
    index: {
      document_id: string;
      version: number;
      state: string;
      hash: string;
    } | null;
  }[];
  collected_at: string;
  registration: string;
};
export type DeclaredVerification = {
  declaration: {
    label: string;
    status: string;
    worktree_id: string;
    head: string;
  };
  actual_binding: {
    head: string;
    branch: string;
    dirty: boolean;
    collected_at: string;
  };
  execution: string;
};
export type DeclaredState = {
  project_id: string;
  revision: number;
  state: string;
  payload: {
    goal: string;
    constraints: string[];
    unfinished: string[];
    decisions: string[];
    verifications: DeclaredVerification[];
    trust: string;
  };
  source_refs: SourceRef[];
};
export type ProjectState = {
  project_id: string;
  state: DeclaredState | null;
  revision: number;
  kind?: string;
  reason?: string;
};
export type ContinuationPack = {
  pack_id: string;
  sha256: string;
  expires_in: number;
  payload: {
    project_id: string;
    project_revision: number;
    content_revision: number;
    checkout: Checkout;
    index: Page<{ document_id: string; locator: string; version: number }> & {
      source_validation: string;
    };
    declared_state: DeclaredState | null;
    notes: Page<{ note_id: string; payload: { question: string } }>;
    lessons: Page<Lesson>;
    omissions: string[];
  };
  source_refs: SourceRef[];
  budget: { limit_bytes: number; used_bytes: number };
};
export type ContinuationCheck = {
  pack_id: string;
  valid: boolean;
  differences: string[];
  actual_checkout: Checkout;
  source_refs: SourceRef[];
};
