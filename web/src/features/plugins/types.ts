export type JsonSchema = {
  type?: string | string[];
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  format?: string;
  "x-secret"?: boolean;
  "x-ui"?: { multiline?: boolean };
};
export type Manifest = {
  id: string;
  name: string;
  version: string;
  description: string;
  author: string;
  capabilities: {
    required: { id: string; major: number }[];
    optional: { id: string; major: number }[];
  };
};
export type Package = {
  id: string;
  manifest: Manifest;
  digest: string;
  installed: boolean;
  source: Record<string, unknown>;
};
export type Action = {
  id: string;
  title: string;
  effect: "read" | "write";
  input_schema: JsonSchema;
  output_schema: JsonSchema;
  resumable: boolean;
  resume_handler?: string;
  reconcile_handler?: string;
  availability: { available: boolean; missing_capabilities: string[] };
};
export type PluginView = {
  id: string;
  title: string;
  entry: string;
  actions: string[];
};
export type Instance = {
  id: string;
  name: string;
  package_id: string;
  version: string;
  revision: number;
  desired_state: "enabled" | "disabled";
  state: string;
  problem: string | null;
  config: Record<string, unknown>;
  config_schema: JsonSchema;
  secret_configured: Record<string, boolean>;
  bindings: Binding[];
  binding_overrides: string[];
  missing_capabilities: string[];
  contributions: {
    actions: Action[];
    tools?: { name: string; action: string }[];
    commands?: { names: string[]; action: string }[];
    views?: PluginView[];
  };
};
export type Binding = {
  kind: "binding";
  schema_version: 1;
  binding_id: string;
  instance_id: string;
  revision: number;
  enabled: boolean;
  priority: number;
  overrides: Record<string, unknown>;
  scope: { kind: string; workspace_id?: string; [key: string]: unknown };
  entries?: { kind: "action" | "event" | "bridge"; id: string }[];
};
export type ActorOption = { actor_id: string; name: string; version: number };
export type Invocation = {
  id: string;
  instance_id: string;
  action: string;
  package_version: string;
  config_revision: number;
  execution: string;
  effect: string;
  delivery: string;
  revision: number;
  result: unknown;
  external?: { status?: string; [key: string]: unknown };
  error: { code: string; retriable?: boolean } | null;
  created_at: number;
  updated_at: number;
  progress: { completed: number; total: number | null; message: string } | null;
};
export type Installation = {
  id: string;
  execution: string;
  error: { code: string } | null;
  package_id: string | null;
};
export type PluginOverview = {
  packages: Package[];
  instances: Instance[];
  catalog: (Manifest & { source_id: string })[];
  installations: Installation[];
  capabilities: string[];
  notifications: {
    id: string;
    title: string;
    body: string;
    instance_id: string;
    created_at: number;
  }[];
};
