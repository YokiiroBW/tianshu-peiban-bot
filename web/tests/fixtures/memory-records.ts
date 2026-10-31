import type { RecordGroup } from "../../src/features/memory/MemoryRecords";

// Shared scopes carry organization metadata, without private source excerpts.
export const sharedProvenance: RecordGroup["provenance"] = {
  kind: "portrait",
  subject_person_id: "person:one",
  organized_at: "2026-10-28T12:00:00Z",
  effective_at: "2026-10-27T10:00:00Z",
  expires_at: "2027-01-01T00:00:00Z",
  expired: false,
  importance: "normal",
  retention: "long",
  memory_kind: null,
  memory_state: null,
  source_count: 2,
  context_count: 3,
  replacement_count: 1,
  related_memory_count: 2,
  source_excerpts: [],
  excerpts_truncated: false,
};
