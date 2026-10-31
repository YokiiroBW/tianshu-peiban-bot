import { call, WebError } from "./api";
import type { Provider, ProviderSource, ModelKind } from "./providerTypes";

export type ModelFunction = {
  function_id: string;
  name: string;
  description: string;
  connected: boolean;
  revision: number;
  provider_id: string | null;
  provider_revision: number | null;
  effective_provider_id: string | null;
  effective_provider_revision: number | null;
  configured: boolean;
};

export type ProviderView = {
  providers: Provider[];
  sources: ProviderSource[];
  defaultRevision: number;
  functions: ModelFunction[];
};

type WireProvider = {
  provider_id: string;
  source_id?: string;
  source_name?: string;
  source_enabled?: boolean;
  kind?: ModelKind;
  name: string;
  protocol: "openai-chat-completions";
  base_url: string;
  model_id: string;
  enabled: boolean;
  revision: number;
  has_key: boolean;
  test: {
    revision: number;
    outcome: string;
    tested_at: number;
    error_code?: string;
  } | null;
};

type WireView = {
  providers: WireProvider[];
  sources?: {
    source_id: string;
    name: string;
    base_url: string;
    protocol: "openai-chat-completions";
    enabled: boolean;
    revision: number;
    has_key: boolean;
  }[];
  functions?: ModelFunction[];
  default: {
    provider_id: string | null;
    revision: number;
    provider_revision: number | null;
    configured: boolean;
  };
};

const testErrors = [
  "authentication_failed",
  "endpoint_failed",
  "model_not_found",
  "enumeration_unsupported",
  "connection_failed",
  "timed_out",
  "upstream_invalid",
  "upstream_rejected",
  "dependency_unavailable",
];

function validProvider(item: WireProvider): boolean {
  return (
    !!item &&
    typeof item.provider_id === "string" &&
    (item.kind === undefined ||
      ["text", "embed", "rerank"].includes(item.kind)) &&
    typeof item.name === "string" &&
    item.protocol === "openai-chat-completions" &&
    typeof item.base_url === "string" &&
    typeof item.model_id === "string" &&
    typeof item.enabled === "boolean" &&
    Number.isInteger(item.revision) &&
    item.revision > 0 &&
    typeof item.has_key === "boolean" &&
    (item.test === null ||
      (!!item.test &&
        Number.isInteger(item.test.revision) &&
        typeof item.test.outcome === "string" &&
        (item.test.error_code === undefined ||
          testErrors.includes(item.test.error_code)) &&
        Number.isFinite(item.test.tested_at) &&
        !Number.isNaN(new Date(item.test.tested_at * 1000).getTime())))
  );
}

export function mapProviderView(wire: WireView): ProviderView {
  if (
    !wire ||
    !Array.isArray(wire.providers) ||
    !wire.default ||
    !(
      wire.default.provider_id === null ||
      typeof wire.default.provider_id === "string"
    ) ||
    !(
      wire.default.provider_revision === null ||
      Number.isInteger(wire.default.provider_revision)
    ) ||
    typeof wire.default.configured !== "boolean" ||
    !Number.isInteger(wire.default.revision) ||
    wire.default.revision < 0 ||
    !wire.providers.every(validProvider) ||
    (wire.sources !== undefined &&
      (!Array.isArray(wire.sources) ||
        !wire.sources.every(
          (item) =>
            item &&
            typeof item.source_id === "string" &&
            typeof item.name === "string" &&
            typeof item.base_url === "string" &&
            item.protocol === "openai-chat-completions" &&
            typeof item.enabled === "boolean" &&
            Number.isInteger(item.revision) &&
            item.revision > 0 &&
            typeof item.has_key === "boolean",
        ))) ||
    (wire.functions !== undefined &&
      (!Array.isArray(wire.functions) ||
        !wire.functions.every(
          (item) =>
            item &&
            typeof item.function_id === "string" &&
            typeof item.name === "string" &&
            typeof item.description === "string" &&
            typeof item.connected === "boolean" &&
            Number.isInteger(item.revision) &&
            item.revision >= 0 &&
            (item.provider_id === null ||
              typeof item.provider_id === "string") &&
            (item.provider_revision === null ||
              Number.isInteger(item.provider_revision)) &&
            (item.effective_provider_id === null ||
              typeof item.effective_provider_id === "string") &&
            (item.effective_provider_revision === null ||
              Number.isInteger(item.effective_provider_revision)) &&
            typeof item.configured === "boolean",
        )))
  )
    throw new WebError("dependency_unavailable", 502);
  return {
    sources: (wire.sources ?? []).map((item) => ({
      sourceId: item.source_id,
      name: item.name,
      baseUrl: item.base_url,
      protocol: item.protocol,
      enabled: item.enabled,
      revision: item.revision,
      hasKey: item.has_key,
    })),
    functions: wire.functions ?? [],
    defaultRevision: wire.default.revision,
    providers: wire.providers.map((item) => ({
      providerId: item.provider_id,
      sourceId: item.source_id,
      sourceName: item.source_name,
      sourceEnabled: item.source_enabled,
      kind: item.kind ?? "text",
      name: item.name,
      protocol: item.protocol,
      baseUrl: item.base_url,
      modelId: item.model_id,
      enabled: item.enabled,
      revision: item.revision,
      hasKey: item.has_key,
      isDefault:
        wire.default.configured &&
        item.provider_id === wire.default.provider_id &&
        item.revision === wire.default.provider_revision,
      test: item.test
        ? {
            state:
              item.test.outcome === "succeeded"
                ? ("passed" as const)
                : [
                      "unknown",
                      "cancelled",
                      "timed_out",
                      "connection_failed",
                    ].includes(item.test.outcome)
                  ? ("unknown" as const)
                  : ("failed" as const),
            code:
              item.test.outcome === "succeeded"
                ? null
                : (item.test.error_code ?? item.test.outcome),
            revision: item.test.revision,
            at: new Date(item.test.tested_at * 1000).toISOString(),
          }
        : { state: "untested" as const, code: null, revision: null, at: null },
    })),
  };
}

export async function readProviders(csrf: string, signal: AbortSignal) {
  const result = await call<WireView>("providers/view", {}, csrf, signal);
  return mapProviderView(result);
}
