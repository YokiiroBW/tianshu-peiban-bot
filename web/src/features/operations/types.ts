export type Incident = {
  incident_id: string;
  dependency: string;
  error_family: string;
  version: number | null;
  severity: string;
  state: string;
  revision: number;
  occurrences: number;
  first_seen: string;
  last_seen: string;
  resolved_at: string | null;
  summary: string | null;
  affected: unknown;
  diagnostic_state: string | null;
  recovery_action: string | null;
  recovery_lineage: unknown;
  last_observation: unknown;
};
export type IncidentPage = { items: Incident[]; next_cursor: string | null; coverage: { complete: boolean } };
export type IncidentDetail = Incident & { diagnostics: unknown[]; recoveries: unknown[]; notifications: unknown[] };
export type OperationsValue = {
  diagnosis: { primary_route_id: string | null; backup_route_id: string | null };
  notifications: {
    qq: { connection_id: string; recipient_id: string } | null;
    smtp: { host: string; port: number; sender: string; recipient: string; username: string | null; credential_ref: string | null; tls: "tls" | "starttls" } | null;
  };
};
export type OperationsSettings = { revision: number; value: OperationsValue; diagnosis_configured: boolean; notification_configured: { qq: boolean; smtp: boolean }; smtp_credential_configured: boolean | null };
