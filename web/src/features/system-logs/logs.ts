export type LogQuery = {
  start_ns: string;
  end_ns: string;
  role: string | null;
  level: string | null;
  module: string | null;
  correlation_id: string | null;
  trace_id: string | null;
  job_id: string | null;
  attempt_id: string | null;
  keyword: string | null;
  exclude_conversation: boolean;
  limit: number;
};

export type LogResponse = {
  entries?: Record<string, unknown>[];
  streams?: { stream: Record<string, string>; values: [string, string][] }[];
  next_cursor?: string | null;
  next_end_ns?: string | null;
  coverage?: { complete?: boolean; snapshot_complete?: boolean; has_more?: boolean; source?: string; scope?: string; generated_coverage?: string; collection_coverage?: string; missing?: unknown[]; [key: string]: unknown } | null;
  freshness?: {
    as_of?: string | null;
    snapshot_at?: string | null;
    snapshot_expires_at?: string | null;
    late_arrivals?: string | null;
    writers?: { instance_id: string; role: string; generated_sequence: number | null; persisted_sequence: number | null; persisted_bytes: number | null; pending: number | null; clean_shutdown: boolean | null; missing_count: number | null; updated_at: string | null }[];
    disk?: { total_bytes: number | null; free_bytes: number | null } | null;
    collector?: { status: string; checkpoint?: string | null; collected_sequence?: number | null; delivered_sequence?: number | null; reason?: string | null } | null;
    loki?: { status: string } | null;
  } | null;
};

export type LogEntry = {
  key: string;
  identity: string | null;
  timestamp: string;
  role: string;
  level: string;
  module: string;
  message: string;
  fields: Record<string, unknown>;
  origin: "entries" | "streams";
};

function text(value: unknown): string {
  return typeof value === "string" || typeof value === "number" ? String(value) : "";
}

function parsedLine(line: unknown): Record<string, unknown> {
  if (typeof line !== "string") return {};
  try {
    const value: unknown = JSON.parse(line);
    return value && typeof value === "object" && !Array.isArray(value)
      ? value as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

function entry(raw: Record<string, unknown>, fallback: string, origin: LogEntry["origin"]): LogEntry {
  const fields = { ...parsedLine(raw.line), ...raw };
  const instance = text(fields.instance_id);
  const eventId = text(fields.event_id);
  const sequence = text(fields.sequence);
  // Without a stable event identity, do not discard coincident records.
  const identity = eventId ? `event:${instance}:${eventId}` :
    instance && sequence ? `sequence:${instance}:${sequence}` : null;
  return {
    key: identity ?? fallback,
    identity,
    timestamp: text(fields.timestamp_ns ?? fields.time_ns ?? fields.stamp ?? fields.timestamp),
    role: text(fields.role) || "未记录",
    level: text(fields.level) || "未记录",
    module: text(fields.module) || "未记录",
    message: text(fields.message ?? fields.event ?? fields.line) || "无消息正文",
    fields,
    origin,
  };
}

export function entriesOf(response: LogResponse, pageKey: string): LogEntry[] {
  if (Array.isArray(response.entries)) return response.entries.map((row, index) => entry(row, `${pageKey}:${index}`, "entries"));
  return (response.streams ?? []).flatMap((stream, group) =>
    stream.values.map(([timestamp, line], index) => entry(
      { ...stream.stream, ...parsedLine(line), timestamp_ns: timestamp, line },
      `${pageKey}:${group}:${index}`, "streams",
    )),
  );
}

export function appendEntries(previous: LogEntry[], incoming: LogEntry[]): LogEntry[] {
  const identities = new Set(previous.flatMap((row) => row.identity ? [row.identity] : []));
  const result = [...previous];
  for (const row of incoming) {
    if (row.identity && identities.has(row.identity)) continue;
    if (row.identity) identities.add(row.identity);
    result.push(row);
  }
  return result;
}

export function formatTimestamp(value: string): string {
  if (!value) return "时间未记录";
  if (/^\d{10,19}$/.test(value)) {
    try {
      const number = BigInt(value);
      const milliseconds = value.length > 16 ? number / 1000000n : value.length > 13 ? number / 1000n : value.length <= 10 ? number * 1000n : number;
      const date = new Date(Number(milliseconds));
      if (!Number.isNaN(date.getTime())) return date.toLocaleString("zh-CN", { hour12: false });
    } catch { /* Preserve an unrecognized timestamp below. */ }
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

export function localInput(date: Date): string {
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

export function ns(value: string): string {
  return (BigInt(new Date(value).getTime()) * 1000000n).toString();
}
