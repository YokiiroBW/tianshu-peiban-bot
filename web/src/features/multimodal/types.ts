import type { ArtifactRef, Coverage, SourceRef } from "../../api/contracts";
import type { MediaRef } from "../../components/MediaPreview";

export type FishConnection = {
  id: string;
  revision: number;
  transport: "mcp" | "native" | "edge" | "compatible";
  enabled: boolean;
  configured: boolean;
  account_id: string | null;
  team_id: string | null;
  workspace_id: string | null;
  quota_group: string;
  billing_source: "plus" | "api" | "free";
  capabilities: Record<
    | "tts"
    | "asr"
    | "stream"
    | "timestamps"
    | "voices"
    | "clone"
    | "dictionaries",
    boolean
  >;
  balance: { value: number; unit: string; observed_at: string } | null;
  state: string;
  reason: string | null;
};
export type VoiceProfile = {
  id: string;
  revision: number;
  actor_id: string;
  connection_id: string;
  voice_id: string;
  model: string;
  language: string;
  output_mode: "text" | "audio" | "both" | "natural";
  dictionary_ids: string[];
  vocal_defaults: Record<string, unknown>;
};
export type PronunciationDictionary = {
  id: string;
  revision: number;
  name: string;
  entries: { text: string; pronunciation: string }[];
  provider_id: string | null;
  provider_version: string | null;
};
export type SpeechView = {
  connections: FishConnection[];
  profiles: VoiceProfile[];
  dictionaries: PronunciationDictionary[];
  default_route: { connection_id: string; model: string } | null;
};
export type Voice = {
  id: string;
  name: string;
  preview: MediaRef | null;
  visibility: string;
  owner_id: string | null;
};
export type SpeechArtifact = {
  artifacts: ArtifactRef[];
  alignment: unknown[];
  spoken_text: string;
  model: string;
  billing_source: string;
  usage: Record<string, unknown> | null;
};
export type ReactionPack = {
  enabled: boolean;
  id: string;
  revision: number;
  name: string;
  actor_ids: string[];
  scope_ids: string[];
  asset_count: number | null;
};
export type ReactionAsset = {
  id: string;
  revision: number;
  pack_id: string;
  media: MediaRef;
  description: string;
  ocr: string;
  actions: string[];
  purposes: string[];
  enabled: boolean;
  tags: string[];
  animated: boolean;
  index_state: string;
  embedding_profile: string | null;
};
export type ReactionsView = {
  packs: ReactionPack[];
  assets: ReactionAsset[];
  index: {
    profile: string | null;
    pending: number | null;
    failed: number | null;
  };
  next_cursor: string | null;
  coverage?: Coverage;
};
export type ImageEngine = {
  endpoint: string | null;
  account_id: string;
  reference_mode: string | null;
  parameters: Record<string, unknown>;
  id: string;
  revision: number;
  engine: "comfyui" | "nai" | "gpt_image";
  enabled: boolean;
  configured: boolean;
  model: string;
  quota_group: string;
  capabilities: Record<string, unknown>;
  state: string;
  reason: string | null;
};
export type ImageProfile = {
  id: string;
  revision: number;
  actor_id: string;
  engine_id: string;
  workflow_id: string | null;
  character: string;
  references: MediaRef[];
  outfit_defaults: Record<string, unknown>;
  style: string;
};
export type WorkflowBinding = {
  node: string;
  class_type: string;
  input: string;
  mode?: "append" | "replace";
  min?: number;
  max?: number;
};
export type WorkflowGraph = Record<
  string,
  { class_type: string; inputs: Record<string, unknown> }
>;
export type ImageWorkflow = {
  id: string;
  revision: number;
  name: string;
  graph: WorkflowGraph;
  bindings: Record<string, WorkflowBinding>;
  outputs: string[];
};
export type SceneSnapshot = {
  [key: string]: unknown;
};
export type ImageView = {
  engines: ImageEngine[];
  profiles: ImageProfile[];
  workflows: ImageWorkflow[];
  identity: { actor_id: string; references: unknown[] } | null;
  outfits: { id: string; description: string }[];
  scene: SceneSnapshot | null;
  jobs: {
    action_id: string;
    attempt_id: string | null;
    execution: string;
    effect: string;
    artifacts: ArtifactRef[];
    reason: string | null;
  }[];
  album: ArtifactRef[];
};
export type ImagePreview = {
  source_refs: SourceRef[];
  scene: SceneSnapshot;
  intent: Record<string, unknown>;
  engine_id: string;
  compiled: {
    prompt: string;
    negative_prompt: string;
    parameters: Record<string, unknown>;
    references: MediaRef[];
  };
  warnings: string[];
};
