import { ApiError, apiRead } from "../../api/client";
import { GLOBAL_SCOPE } from "../../api/useDomainResource";
import { readSession } from "../../api/session";
/** Same-origin calls for the read-only asset page.
 *
 * This module owns one thing: the wire shapes the page reads, and the honest words for the
 * failures it can receive. It holds no authorization rule — which connection may be read, and
 * whether this login may read at all, is decided by the server on every request.
 *
 * The browser sends an operation name plus identifiers and a query, and nothing else: no
 * credential, no endpoint, no physical path. That is why every request body below is built from
 * values the page itselfs holds (a library id it was shown, a relative path it navigated to).
 */

export type SessionState = {
  authenticated: boolean;
  csrf: string;
  username?: string;
};

export type Preview = {
  available: boolean;
  code: string;
  reason: string;
};

export type Library = {
  library_id: string;
  display_name: string;
  availability: "online" | "offline";
  access_level: string;
  category: string;
  index_available: boolean;
  original_available: string;
  preview: Preview;
};

export type Entry = {
  entry_id: string;
  library_id: string;
  relative_path: string;
  name: string;
  kind: string;
  directory: boolean;
  content_length: string | null;
  last_write_time_utc: string;
  original_available: string;
  preview: Preview;
};

export type Page = {
  size: number;
  returned: number;
  has_more: boolean;
  next_cursor: string | null;
};

export type Connection = {
  connection_id: string;
  label: string;
  credential_registered: boolean;
};

export type Read = {
  at: number;
  connection_id: string;
  operation: string;
  code: string;
  seconds: number;
};

export type AssetConfiguration = {
  id: string; name?: string; endpoint: string; revision: number; enabled: boolean;
  configured: boolean; quota_group?: string; ca_file?: string;
  allowed_principals?: string[]; actor_ids?: string[];
};

export type PageState = {
  available: boolean;
  code: string;
  enabled: boolean;
  connection: string | null;
  scope_ttl_seconds: number;
  connections: Connection[];
  preview: Preview;
  page_size: { default: number; maximum: number };
  sort: { fields: string[]; directions: string[] };
  kinds: string[];
  scopes: string[];
  search_scopes: string[];
  reselect: string;
  cancel: string;
  reads: Read[];
  can_manage: boolean;
  configurations: AssetConfiguration[];
};

export type LibrariesPage = {
  operation: "libraries.list";
  connection: string;
  libraries: Library[];
  page: Page;
  preview: Preview;
};

export type BrowsePage = {
  operation: "entries.browse";
  connection: string;
  library: Library;
  parent_relative_path: string;
  entries: Entry[];
  page: Page;
  options: {
    sort_by: string;
    sort_direction: string;
    kind: string;
    name_filter: string;
  };
  preview: Preview;
};

export type SearchPage = {
  operation: "assets.search";
  connection: string;
  query: string;
  scope: string;
  library_id: string | null;
  parent_relative_path: string | null;
  hits: { library: Library; entry: Entry; hit_reason: string }[];
  page: Page;
  preview: Preview;
};

export type EntryDetail = {
  operation: "entries.get";
  connection: string;
  library: Library;
  entry: Entry;
  preview: Preview;
};

const messages: Record<string, string> = {
  web_not_configured: "网页控制台尚未启用，资产库不可用。",
  assets_disabled: "这个部署没有登记只读资产页。",
  operator_not_authorized: "当前身份没有资产读取权限。",
  no_asset_connections: "这个部署没有可用的资产连接。",
  connection_not_allowed: "这不是本页可以使用的资产连接。",
  connection_required: "请先选择要查看的资产连接。",
  unauthorized: "登录或资产凭据无效，请重新登录。",
  session_expired: "登录已过期，请重新登录。",
  forbidden: "上游资产服务拒绝了这次读取。",
  not_found: "这个条目在当前授权范围内不存在或不可见。",
  invalid_input: "请求内容不符合资产读取协议要求。",
  invalid_upstream: "上游返回的内容不符合资产读取协议，本次结果未采用。",
  upstream_error: "上游返回了本页无法解释的错误，本次结果未采用。",
  unsupported_operation: "上游资产服务不支持这个读取操作。",
  budget_exceeded: "读取内容超过大小上限，本次结果未采用。",
  deadline_exceeded: "上游未在时限内响应，本次结果未采用。",
  dependency_unavailable: "资产连接暂时不可用，请稍后重试。",
  cursor_conflict: "列表范围已经改变，请重新读取。",
  too_many_requests: "尝试次数过多，请稍后重试。",
  access_denied: "当前资产只读授权不允许这个操作。",
};

export class WebError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
  ) {
    super(`${messages[code] ?? "请求失败，请重新连接。"}（${code}）`);
  }
}

/** One refusal the page shows as its own state rather than as a broken page. */
export function isState(cause: unknown, ...codes: string[]) {
  return cause instanceof WebError && codes.includes(cause.code);
}

export async function call<T>(path: string, body: object, csrf: string, signal: AbortSignal): Promise<T> {
  try { return await apiRead<T>(`resources/${path}`, { ...GLOBAL_SCOPE, ...body }, csrf, signal); }
  catch (cause) {
    if (cause instanceof ApiError) throw new WebError(cause.code, cause.status);
    throw cause;
  }
}

export async function session(signal: AbortSignal): Promise<SessionState> {
  return readSession(signal);
}

export function kindLabel(kind: string, directory: boolean) {
  if (kind === "directory" || kind === "reparse_directory") return "文件夹";
  if (directory) return "文件夹";
  if (kind === "reparse_file") return "链接文件";
  if (kind === "file") return "文件";
  return kind;
}

/** A size this protocol does not state is shown as unknown, never as zero. */
export function sizeText(entry: Entry) {
  if (entry.directory) return "—";
  if (entry.content_length === null) return "大小未知";
  const value = Number(entry.content_length);
  if (!Number.isFinite(value)) return "大小未知";
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let scaled = value / 1024;
  let index = 0;
  while (scaled >= 1024 && index < units.length - 1) {
    scaled /= 1024;
    index += 1;
  }
  return `${scaled.toFixed(scaled >= 10 ? 0 : 1)} ${units[index]}`;
}

/** A missing timestamp stays unknown; the page never invents a date or a count. */
export function modifiedText(entry: Entry) {
  if (!entry.last_write_time_utc) return "修改时间未知";
  const moment = new Date(entry.last_write_time_utc);
  if (Number.isNaN(moment.getTime())) return "修改时间未知";
  return moment.toLocaleString("zh-CN", { hour12: false });
}

export function breadcrumb(relative: string) {
  return relative ? relative.split("/").filter(Boolean) : [];
}

export function libraryState(library: Library) {
  if (library.availability === "offline")
    return {
      tone: "yellow" as const,
      label: "索引离线",
      detail:
        "上游报告这个库的索引当前离线，下面是它已授权的索引快照；原件是否可用未经验证。",
    };
  if (library.access_level !== "read_only")
    return {
      tone: "blue" as const,
      label: "可读",
      detail: "本页只读取索引元数据，不写入、不下载、不预览。",
    };
  return {
    tone: "blue" as const,
    label: "可读",
    detail: "上游授权为只读；本页不写入、不下载、不预览。",
  };
}
