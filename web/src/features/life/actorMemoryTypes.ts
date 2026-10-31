export type MemoryRef = {
  kind: "episode" | "derived";
  id: string;
  revision: number;
};
export type MemorySource = {
  producer: string;
  object_id: string;
  revision: string;
  content_hash: string;
};
export type Interest = {
  topic: string;
  stance: "like" | "dislike" | "mixed" | "curious";
};
export type ManagedMemory = {
  ref: MemoryRef;
  kind:
    | "episode"
    | "understanding"
    | "preference"
    | "reflection"
    | "autobiography"
    | null;
  state: string;
  text: string | null;
  episode_refs: { episode_id: string; expected_revision: number }[];
  source_refs: MemorySource[];
  recorded_at: string;
  occurred_at: string | null;
  expires_at: string | null;
  importance: "low" | "normal" | "high" | null;
  retention: "short" | "medium" | "long" | null;
  interest: Interest | null;
  editable: boolean;
  forgotten: boolean;
};
export type MemoryList = {
  actor_id: string;
  domain_version: number;
  items: ManagedMemory[];
  next_cursor: string | null;
  omitted: boolean;
};
export type MemoryImpact = {
  ref: MemoryRef;
  domain_version: number;
  dependent_refs: MemoryRef[];
  source_refs: MemorySource[];
  can_revise: boolean;
  can_forget: boolean;
  omitted: boolean;
  owner_originals_affected: false;
};
export type InterestItem = {
  ref: MemoryRef;
  text: string;
  topic: string | null;
  stance: string;
  support_count: number;
  source_refs: MemorySource[];
  history: {
    ref: MemoryRef;
    stance: string;
    state: string;
    recorded_at: string;
  }[];
  history_omitted: boolean;
};
export type Interests = { items: InterestItem[]; omitted: boolean };
export type Episode = {
  episode: {
    fact: {
      summary: string;
      provenance: string;
      relation: string;
      outcome: string;
      occurred_at: string | null;
      learned_at: string;
      content_reads: {
        content_ref: MemorySource;
        coverage: {
          unit: string;
          start: number;
          end: number;
          complete: boolean;
          gaps: string[];
        }[];
      }[];
    };
  };
};
