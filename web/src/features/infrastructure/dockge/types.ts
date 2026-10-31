// TS-206 HTTP contract, fixed at 7bbf30d; node identities are never display names.
export type Availability = { enabled: boolean; reason_codes: string[] };
export type Maintenance = {
  required: boolean;
  owner: string | null;
  reason_codes: string[];
};
export type ControlObservation = {
  status: string;
  freshness: string;
  observed_at: string | null;
  reason_codes: string[];
};
export type ContainerControl = {
  container_id: string;
  stack_id: string | null;
  maintenance: Maintenance;
  allowed_actions: Record<string, Availability>;
};
export type ControlStack = {
  stack_id: string;
  project_name: string;
  display_name: string;
  revision: string;
  ownership: string;
  exists: boolean;
  runtime_state: string;
  health_state: string;
  containers: string[];
  maintenance: Maintenance;
  allowed_actions: Record<string, Availability>;
};
export type ExecutionResult = {
  command_state: string;
  execution_active: boolean | null;
  runtime_state: string;
  health_state: string;
  containers: { container_id: string; state: string; health: string }[];
  version_id: string | null;
  node_operation_id: string;
};
export type Plan = {
  plan_id: string;
  node_id: string;
  connection_revision: number;
  stack_id: string;
  project_name: string;
  action: string;
  expected_revision: string;
  expires_at: string;
  executable: boolean;
  changes: string[];
  diff: string;
  images: string[];
  ports: string[];
  mounts: string[];
  warnings: string[];
  blockers: string[];
  maintenance: Maintenance;
};
export type Operation = {
  operation_id: string;
  client_request_id: string;
  kind: string;
  node_id: string;
  connection_revision: number;
  stack_id: string | null;
  container_id: string | null;
  action: string;
  state: string;
  stage: string;
  acceptance: string;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
  settled: boolean;
  reason_codes: string[];
  can_refresh: boolean;
  result: ExecutionResult | Plan | Record<string, unknown> | null;
};
export type ControlView = {
  configured: boolean;
  node_id: string;
  connection_revision: number;
  capabilities: {
    available: boolean;
    reason_codes: string[];
    create: Availability;
  };
  stacks: ControlStack[];
  containers: ContainerControl[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
  observation: ControlObservation;
  refresh: Operation | null;
};
export type ConfigVersion = {
  version_id: string;
  created_at: string;
  label: string;
  current: boolean;
  restorable: boolean;
  reason_codes: string[];
};
export type StackDetail = {
  node_id: string;
  connection_revision: number;
  compose_content: string | null;
  versions: ConfigVersion[];
  observation: ControlObservation;
  refresh: Operation | null;
} & ({ stack: ControlStack } | { stack: null });
export type ContainerControlDetail = {
  node_id: string;
  connection_revision: number;
  container: ContainerControl | null;
  observation: ControlObservation;
  refresh: Operation | null;
};
export type OperationList = {
  items: Operation[];
  page: { returned: number; has_more: boolean; next_cursor: string | null };
};
