import type { SourceRef } from "../../../api/contracts";
export type SkillSource = {
  source_id: string;
  name: string;
  revision: number;
  manifest_url: string;
  enabled: boolean;
  expected_sha256: string | null;
  quota_group: string | null;
  credential_configured: boolean;
  state: string;
  error_code?: string | null;
  last_refreshed_at?: string | null;
  source_refs?: SourceRef[];
  entries?: unknown[];
};
export type Skill = {
  definition: {
    id: string;
    title: string;
    description: string;
    effect: "read" | "write";
    version: number;
    model_tool: { name: string } | null;
    public_command: { name: string; aliases: string[] } | null;
    long_running: boolean;
    permissions: string[];
  };
  availability: {
    implemented: boolean;
    configured: boolean;
    enabled: boolean;
    provider_supported: boolean | null;
    verified_at: string | null;
    reason: string | null;
  };
  can_execute: boolean;
  enabled: boolean;
  source_ids: string[];
};
export type SkillsCatalog = {
  actor_id: string;
  revision: number;
  items: Skill[];
  sources: SkillSource[];
};
