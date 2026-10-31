export type ModelKind = "text" | "embed" | "rerank";
export const modelKindNames: Record<ModelKind, string> = {
  text: "对话",
  embed: "嵌入",
  rerank: "重排序",
};
/** Public, redacted fields only. The secret is never part of a provider view. */
export type Provider = {
  providerId: string;
  sourceId?: string;
  sourceName?: string;
  sourceEnabled?: boolean;
  kind?: ModelKind;
  name: string;
  protocol: "openai-chat-completions";
  baseUrl: string;
  modelId: string;
  enabled: boolean;
  revision: number;
  hasKey: boolean;
  isDefault: boolean;
  test: {
    state: "passed" | "failed" | "unknown" | "untested";
    code: string | null;
    revision: number | null;
    at: string | null;
  };
};

export type ProviderSource = {
  sourceId: string;
  name: string;
  baseUrl: string;
  protocol: "openai-chat-completions";
  enabled: boolean;
  revision: number;
  hasKey: boolean;
};
export type SourceDraft = {
  name: string;
  baseUrl: string;
  apiKey: string;
  enabled: boolean;
};
export type ModelDraft = {
  name: string;
  modelId: string;
  kind: ModelKind;
  enabled: boolean;
};
export type ProviderActions = {
  saveSource: (
    draft: SourceDraft,
    original: ProviderSource | null,
    signal: AbortSignal,
  ) => Promise<string>;
  listModels: (
    source: ProviderSource,
    signal: AbortSignal,
  ) => Promise<string[]>;
  addModels: (
    source: ProviderSource,
    models: string[],
    kind: ModelKind,
    signal: AbortSignal,
  ) => Promise<void>;
  clearKey: (source: ProviderSource, signal: AbortSignal) => Promise<void>;
  removeSource: (source: ProviderSource, signal: AbortSignal) => Promise<void>;
  saveModel: (
    provider: Provider,
    draft: ModelDraft,
    signal: AbortSignal,
  ) => Promise<void>;
  test: (provider: Provider, signal: AbortSignal) => Promise<void>;
  setDefault: (provider: Provider, signal: AbortSignal) => Promise<void>;
  setEnabled: (
    provider: Provider,
    enabled: boolean,
    signal: AbortSignal,
  ) => Promise<void>;
  remove: (provider: Provider, signal: AbortSignal) => Promise<void>;
};

export function modelLabel(provider: Provider) {
  const source = provider.sourceName ?? provider.name;
  return `${source} / ${provider.name === source ? provider.modelId : provider.name}`;
}
