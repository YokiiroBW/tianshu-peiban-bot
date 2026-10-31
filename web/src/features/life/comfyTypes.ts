export type ComfyBinding = {
  node: string;
  class_type: string;
  input: string;
  mode: "replace" | "append";
  min?: number;
  max?: number;
};
export type ComfyBindings = Record<string, ComfyBinding>;
export type ImageIntent = Partial<
  Record<
    | "character"
    | "outfit"
    | "pose"
    | "background"
    | "camera"
    | "positive"
    | "negative",
    string
  >
>;
export type ComfyStatus = {
  id: string;
  version: number;
  state:
    | "not_configured"
    | "disabled"
    | "configured"
    | "unreachable"
    | "workflow_required"
    | "unsupported";
  provider: "comfyui";
  base_url: string | null;
  enabled: boolean;
  workflow_id: string | null;
  workflow_version: string | null;
  checked_at: number | null;
  error_code: string | null;
  capabilities: {
    discovery: boolean;
    model_assistance: boolean;
    structured_prompt: boolean;
    reference: boolean;
  };
  models: string[];
};
export type ComfyWorkflowList = {
  source: "comfyui_userdata";
  items: { id: string; name: string; source: "comfyui_userdata" }[];
};
export type ComfyWorkflow = {
  workflow_id: string;
  workflow_version: string;
  format: "api" | "ui";
  state: "ready" | "unsupported";
  nodes: {
    node_id: string;
    class_type: string;
    title: string;
    inputs: { name: string; type: string; value: unknown; linked: boolean }[];
  }[];
  bindings: ComfyBindings;
  candidates: Record<string, ComfyBinding[]>;
  unresolved: { code: string; node_id: string | null; detail: string }[];
  protected_nodes: { node_id: string; class_type: string }[];
  outputs: string[];
  conversion: { strategy: string; evidence: unknown };
};
export type ComfyActor = {
  id: string;
  actor_id: string;
  version: number;
  state: "configured" | "not_configured";
  workflow_id: string | null;
  workflow_version: string | null;
  character_prompt: string;
  bindings: ComfyBindings;
  defaults: ImageIntent & {
    width?: number;
    height?: number;
    seed?: number;
  };
};
export type ComfyCompile = {
  state: "ready";
  provider: "comfyui";
  workflow_id: string;
  workflow_version: string;
  graph: Record<string, unknown>;
  graph_sha256: string;
  prompts: ImageIntent;
  dimensions: { width: number; height: number };
  changes: {
    semantic: string;
    node_id: string;
    input: string;
    before: unknown;
    after: unknown;
  }[];
  preserved_nodes: string[];
  warnings: string[];
  model_receipt: Record<string, unknown> | null;
};
export type ComfyResponse<T> = {
  schema_version: 1;
  request_id: string;
  actor_id: string;
  result: T;
};

export const comfySemanticLabels: Record<string, string> = {
  character: "角色形象",
  outfit: "穿搭",
  pose: "姿态与动作",
  background: "背景与场景",
  camera: "镜头与构图",
  positive: "补充细节",
  negative: "排除细节",
  width: "宽度",
  height: "高度",
  seed: "随机种子",
  steps: "步数",
  reference: "形象参考",
  reference2: "服装参考",
  reference3: "原图参考",
};
