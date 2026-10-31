export type SkillDefinition = {
  id: string;
  version: string;
  title: string;
  description: string;
  domain: string;
  operations: string[];
  handler_id: string;
};

export type Skill = Omit<SkillDefinition, "handler_id"> & {
  definition: SkillDefinition;
  source_id: string;
  enabled: boolean;
  installed: boolean;
  revision: string;
  availability: {
    state:
      | "available"
      | "disabled"
      | "not_configured"
      | "unsupported"
      | "unavailable";
    can_execute: boolean;
    reason_code: string | null;
  };
  config: {
    provider: string | null;
    base_url: string | null;
    options: Record<string, unknown>;
    credential_configured: boolean;
  };
};

export type SkillSource = {
  source_id: string;
  name: string;
  manifest_url: string;
  version: number;
  credential_configured: boolean;
  enabled: boolean;
  expected_sha256: string | null;
  state: "configured" | "disabled" | "ready" | "unreachable" | "invalid";
  last_refreshed_at: string | null;
  content_sha256: string | null;
  error_code: string | null;
};

export type SkillsCatalog = {
  actor_version: number;
  catalog_version: string;
  skills: Skill[];
  sources: SkillSource[];
};

export type SkillsResponse = {
  schema_version: number;
  request_id: string;
  actor_id: string;
  result: SkillsCatalog;
};

export type CredentialChange =
  { action: "keep" | "clear" } | { action: "replace"; value: string };

export type CredentialView = {
  revision: number | null;
  credential_configured: boolean;
};

export type SkillAccess = { actor: string; csrf: string };
