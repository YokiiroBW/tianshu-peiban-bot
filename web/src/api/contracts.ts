/** Public HTTP contracts. Domain projections live beside their domain client. */
export type Coverage = {
  complete: boolean;
  total: number | null;
  missing: string[];
  ranges?: Record<string, unknown>[];
  representation?: string | null;
};

export type Page<T> = {
  items: T[];
  next_cursor: string | null;
  coverage: Coverage;
};

export type Receipt = {
  receipt_id: string;
  action_id: string;
  attempt_id: string;
  execution: "pending" | "running" | "succeeded" | "failed" | "cancelled" | "unknown";
  effect: "none" | "confirmed" | "unknown";
  delivery: "not_requested" | "pending" | "delivered" | "failed" | "unknown";
  artifacts: ArtifactRef[];
  coverage: Coverage | null;
  result: Record<string, unknown>;
  error_code: string | null;
};

export type CommandResult<T> = {
  client_request_id: string;
  revision: number | null;
  action_id: string | null;
  attempt_id: string | null;
  receipt: Receipt | null;
  data: T;
};

export type CommandInput = {
  client_request_id: string;
  expected_revision: number | null;
};

export type ArtifactRef = {
  artifact_id: string;
  media: {
    media_id: string;
    uri: string;
    mime_type: string;
    size_bytes: number | null;
    sha256: string | null;
    duration_ms: number | null;
    width: number | null;
    height: number | null;
  };
  source_refs: { source_id: string; revision: number; locator?: Record<string, unknown> }[];
  coverage: Coverage | null;
  metadata: Record<string, unknown>;
};

export type Availability = {
  configured: boolean;
  available: boolean;
  reason: string | null;
  verified_at: string | null;
};

export type ApiErrorBody = {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown> | null;
    request_id: string | null;
  };
};
