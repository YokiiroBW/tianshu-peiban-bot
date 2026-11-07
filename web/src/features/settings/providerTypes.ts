export type NativeProtocol =
  | "openai.chat"
  | "openai.responses"
  | "anthropic.messages"
  | "gemini.generate"
  | "openai.embedding"
  | "cohere.rerank";
export type ServiceClass = "interactive" | "due" | "elastic";
export type Provider = {
  provider_id: string;
  name: string;
  base_url: string;
  protocol: NativeProtocol;
  enabled: boolean;
  revision: number;
  credential_configured?: boolean;
};
export type ModelRoute = {
  route_id: string;
  provider_id: string;
  model: string;
  quota_group: string;
  enabled: boolean;
  revision: number;
  verified_capabilities: string[];
  verification: Record<string, unknown> | null;
  parameters: Record<string, unknown>;
};
export type QuotaGroup = {
  quota_group: string;
  revision: number;
  concurrency: number;
  cloud_concurrency?: number | null;
  interactive_reserve: number;
  background_max: number;
  rpm: number | null;
  tpm: number | null;
  max_queue: number;
  max_wait_seconds: number | null;
  unknown_hold_seconds: number;
  unit_limits: Record<string, number>;
  interactive_rpm_reserve: number;
  background_rpm_max: number | null;
  interactive_unit_reserve: Record<string, number>;
  background_unit_max: Record<string, number>;
};
export type ModelBinding = {
  function: string;
  service_class: ServiceClass;
  actor_id: string;
  route_id: string;
  fallback_routes?: string[];
  revision: number;
};
export type EmbeddingProfile = {
  route_id: string;
  model_version: string;
  normalization: string;
  query_instruction: string;
  dimensions: number;
  revision: number;
};
export type ModelCatalog = {
  probe_capabilities?: Record<string, string[]>;
  providers: Provider[];
  routes: ModelRoute[];
  quota_groups: QuotaGroup[];
  bindings: ModelBinding[];
  functions: string[];
  protocols: NativeProtocol[];
  embedding_profile: EmbeddingProfile | null;
  broker_reload?: "applied" | "pending";
};
export type ModelReceipt = {
  diagnostic?: {
    upstream?: {
      status: number;
      category: string;
      code?: string;
      type?: string;
      param?: string;
    };
  } | null;
  attempt_id: string;
  intent_id: string;
  status:
    "queued" | "running" | "succeeded" | "failed" | "cancelled" | "unknown";
  dispatch: "not_started" | "submitted" | "unknown";
  route_id: string;
  provider_id: string;
  model: string;
  protocol: string;
  quota_group: string;
  error_code: string | null;
  started_at: string | null;
  finished_at: string | null;
  usage: {
    input_tokens: number | null;
    output_tokens: number | null;
    cached_input_tokens: number | null;
    cache_write_tokens: number | null;
    metering: string;
  };
};
export type SourceDraft = Omit<
  Provider,
  "revision" | "credential_configured"
> & { api_key?: string; clear_api_key?: boolean };
export const protocolNames: Record<NativeProtocol, string> = {
  "openai.chat": "OpenAI Chat Completions",
  "openai.responses": "OpenAI Responses",
  "anthropic.messages": "Anthropic Messages",
  "gemini.generate": "Gemini GenerateContent",
  "openai.embedding": "OpenAI Embeddings",
  "cohere.rerank": "Cohere Rerank",
};
export const serviceNames: Record<ServiceClass, string> = {
  interactive: "即时",
  due: "到期",
  elastic: "后台",
};
export const functionNames: Record<string, string> = {
  "*": "系统默认",
  chat: "对话",
  tools: "工具决策",
  code: "代码",
  search: "检索分析",
  writing: "写作",
  participation: "会话参与",
  "life.plan": "生活计划",
  "memory.extract": "记忆提取",
  reflect: "反思",
  embed: "语义嵌入",
  rerank: "重排序",
  vision: "视觉",
  asr: "语音识别",
  tts: "语音合成",
  "reaction.annotate": "表情标注",
  "reaction.search": "表情选材",
  "image.plan": "图像规划",
  "image.consistency": "图像一致性检查",
  "video.observe": "视频观察",
  "ops.diagnose": "运行诊断",
};
