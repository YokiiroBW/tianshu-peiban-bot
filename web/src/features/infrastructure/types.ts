export type Tone = "blue" | "yellow" | "red" | "gray";
export type Observation<T> = {
  status: string;
  freshness: "current" | "stale" | "missing";
  observed_at: string | null;
  last_attempt_at: string | null;
  age_seconds: number | null;
  reason_codes: string[];
  capabilities: string[];
  completeness: {
    state: string;
    returned: number;
    total: number | null;
    next_cursor: string | null;
  } | null;
  data: T | null;
};
export type Attempt = {
  attempt_id: string;
  state: string;
  stage: string;
  started_at: string;
  finished_at: string | null;
  reason_codes: string[];
};
export type NodeSummary = {
  id: string;
  label: string;
  kind: string;
  enabled: boolean;
  connection_revision: number;
  connection_state: string;
  observed_at: string | null;
  last_attempt_at: string | null;
  refresh: Attempt | null;
  capabilities: string[];
  last_test: {
    state: string;
    code: string;
    checked_at: string;
    connection_revision: number;
  } | null;
};
export type CatalogNode = {
  id: string;
  label: string;
  kind: string;
  capabilities: string[];
};
export type InfrastructureView = {
  configured: boolean;
  revision: number;
  max_nodes: number;
  catalog:
    (Observation<{ nodes: CatalogNode[] }> & { refreshing: boolean }) | null;
  nodes: NodeSummary[];
};
export type HostInterface = {
  id: string;
  name: string;
  ifindex: number | null;
  receive_bytes: number;
  transmit_bytes: number;
  receive_errors: number;
  transmit_errors: number;
  receive_drops: number;
  transmit_drops: number;
  receive_bytes_per_second: number | null;
  transmit_bytes_per_second: number | null;
  state: string;
  reason_codes: string[];
};
export type RouterInterface = {
  name: string;
  up: boolean | null;
  pending: boolean | null;
  available: boolean | null;
  device: string | null;
  l3_device: string | null;
  protocol: string | null;
  uptime_seconds: number | null;
  ipv4_addresses: { address: string; mask: number | null }[];
  ipv6_addresses: { address: string; mask: number | null }[];
};
export type Snapshot = {
  identity: {
    node_id: string;
    kind: string;
    hostname: string | null;
    kernel: string | null;
    boot_id: string | null;
  };
  system: {
    uptime_seconds: number | null;
    load_average: number[] | null;
    source: string;
  };
  cpu: {
    usage_ratio: number | null;
    basis: string;
    state: string;
    reason_codes: string[];
  };
  memory: {
    total_bytes: number | null;
    available_bytes: number | null;
    used_bytes: number | null;
    state: string;
    source: string;
  };
  filesystems: {
    id: string;
    device: string;
    mountpoint: string;
    total_bytes: number;
    used_bytes: number;
    available_bytes: number;
  }[];
  interfaces: HostInterface[];
  openwrt: {
    board: {
      model: string | null;
      board_name: string | null;
      system: string | null;
      release: { description: string | null; version: string | null } | null;
    } | null;
    info: {
      uptime_seconds: number | null;
      load_average: number[] | null;
      memory: {
        total_bytes: number | null;
        free_bytes: number | null;
        available_bytes: number | null;
      } | null;
    } | null;
    interfaces: RouterInterface[];
    per_device_traffic: {
      state: string;
      reason_code: string;
      items: unknown[];
    };
    reason_codes: string[];
  } | null;
};
export type Stack = {
  id: string;
  name: string;
  state: string;
  drift_status: string;
  reason_codes: string[];
  services: {
    name: string;
    desired_instances: number | null;
    observed_container_ids: string[];
    drift_status: string;
    findings: string[];
    reason_codes: string[];
  }[];
  runtime_only_services: string[];
};
export type NodeDetail = NodeSummary & {
  snapshot: Observation<Snapshot>;
  compose: Observation<{ stacks: Stack[] }>;
};
export type PlatformHostResources = {
  configured: boolean;
  node: (NodeSummary & { snapshot: Observation<Snapshot> }) | null;
};
export type ContainerStats = {
  id: string;
  availability: string;
  reason_codes: string[];
  sampled_at: string | null;
  cpu: { usage_ratio: number | null; basis: string; state: string } | null;
  memory: {
    raw_usage_bytes: number | null;
    cache_adjusted_usage_bytes: number | null;
    cache_source: string | null;
    limit_bytes: number | null;
  } | null;
  block_io: { read_bytes: number | null; write_bytes: number | null } | null;
  network: {
    receive_bytes: number | null;
    transmit_bytes: number | null;
    attribution: string;
  } | null;
  rates?: {
    receive_bytes_per_second: number | null;
    transmit_bytes_per_second: number | null;
    read_bytes_per_second: number | null;
    write_bytes_per_second: number | null;
    state: string;
  };
};
export type Container = {
  id: string;
  name: string | null;
  image_reference: string | null;
  state: string;
  health: string;
  availability: string;
  compose: { project: string | null; service: string | null } | null;
  stats: ContainerStats | null;
};
export type ContainerRecord = Container & {
  image_id: string | null;
  created_at: string | null;
  started_at: string | null;
  network_mode: string | null;
  ports: {
    container_port: number;
    protocol: string;
    host_ip: string | null;
    host_port: number | null;
  }[];
  mounts: {
    kind: string;
    source: string | null;
    target: string;
    read_only: boolean | null;
  }[];
};
export type ContainerList = {
  node: NodeSummary;
  containers: Container[];
  total: number;
  inventory_total: number;
  page: number;
  page_size: number;
  has_more: boolean;
  applications: { name: string; containers: number }[];
  inventory: Omit<Observation<never>, "data">;
  metrics: Omit<Observation<never>, "data">;
};
export type ContainerDetail = {
  node: NodeSummary;
  container: ContainerRecord;
  stats: ContainerStats | null;
  inventory: Omit<Observation<never>, "data">;
  metrics: Omit<Observation<never>, "data">;
};
export type LogView = {
  state: string;
  attempt_id: string | null;
  observed_at: string | null;
  reason_codes: string[];
  data: {
    stdout: string;
    stderr: string;
    truncated: boolean;
    returned_bytes: number;
    tail: number;
    since_seconds: number;
    max_bytes: number;
  } | null;
};
