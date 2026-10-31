import type {
  Coverage,
  Page,
  SourceRef,
  MediaRef,
  ArtifactRef,
  CommandResult,
} from "../../api/contracts";
export type Worktree = {
  id: string;
  path: string;
  branch: string;
  files: string[];
  max_bytes?: number;
};
export type ProjectCard = {
  project_id: string;
  conversation_id: string;
  label: string;
  revision: number;
  content_revision: number;
  enabled: boolean;
};
export type Project = ProjectCard & {
  actor_id: string;
  root: string;
  directories: string[];
  allowed_urls: string[];
  worktrees: Worktree[];
  scope_ids: string[];
};
export type ProjectsView = Page<ProjectCard> & {
  allowed_roots?: string[];
  can_manage: boolean;
  can_write: boolean;
  can_share: boolean;
};
export type ReadUnitRef = {
  document_id: string;
  version: number;
  unit_id: string;
  sha256: string;
};
export type NoteVersionRef = {
  note_id: string;
  version: number;
  sha256: string;
};
export type Citation =
  | { kind: "unit"; reference: ReadUnitRef }
  | { kind: "note"; reference: NoteVersionRef };
export type DocumentRow = {
  document_id: string;
  locator: string;
  kind: string;
  version: number;
  state: string;
  sha256: string;
  source_validation: string;
  metadata: Record<string, unknown>;
  source_refs: SourceRef[];
};
export type DocumentRead = {
  reference: ReadUnitRef;
  text: string | null;
  spans: { unit: string; start: number; end: number }[];
  source_refs: SourceRef[];
  artifacts: ArtifactRef[];
  freshness: { state: string; basis: string };
  next_cursor: string | null;
  next_position?: number;
  coverage: Coverage;
  consumption?: {
    coverage: Record<string, unknown>;
    complete: boolean;
    missing: string[];
    analysis_completed: false;
  };
};
export type Original = {
  media: MediaRef | null;
  source_ref: SourceRef;
  source_refs: SourceRef[];
};
export type ResearchNote = {
  question: string;
  source_statements: { kind: string; statement: string; source: ReadUnitRef }[];
  inferences: string[];
  open_questions: string[];
  decision: { summary: string; basis: Citation[] } | null;
};
export type Note = {
  note_id: string;
  project_id: string;
  version: number;
  revision: number;
  state: string;
  sha256: string;
  current: boolean;
  citation_state: string;
  payload: ResearchNote;
  source_refs: SourceRef[];
  reference: NoteVersionRef;
};
export type PlanItem = {
  locator: string;
  sha256?: string;
  state: "new" | "changed" | "unchanged" | "removed" | "unavailable";
  size_bytes?: number;
  expected_version: number;
  reason?: string;
};
export type DirectoryPlan = {
  plan_id: string;
  project_id: string;
  directory: string;
  project_revision: number;
  sha256: string;
  items: PlanItem[];
  coverage: Coverage;
  next_position: string | null;
  imported: false;
};
export const lines = (value: string) =>
  value
    .split("\n")
    .map((part) => part.trim())
    .filter(Boolean);
export const confirmed = <T>(answer: CommandResult<T> | null) =>
  answer?.receipt?.execution === "succeeded";
