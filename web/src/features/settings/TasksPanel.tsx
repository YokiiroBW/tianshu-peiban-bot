import { LoginLink, useSessionGuard } from "../../app/Auth";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ListFilter, RefreshCw } from "lucide-react";
import { StatePanel } from "../../components/StatePanel";
import { StatusRail } from "../../components/StatusRail";
import {
  call,
  session as readSession,
  WebError,
  type SessionState,
  type TaskDetail,
  type TaskDetailView,
  type TaskEvidence,
  type TaskItem,
  type TaskSource,
  type TasksPage,
  type TasksView,
} from "./api";
import "./tasks.css";

const PAGE_SIZE = 10;
const POLL_MS = 15_000;

const statusRail: Record<string, { tone: Tone; label: string }> = {
  accepted: { tone: "yellow", label: "已受理" },
  in_progress: { tone: "yellow", label: "执行中" },
  observed: { tone: "blue", label: "已观测" },
  unknown: { tone: "gray", label: "未知" },
  failed: { tone: "red", label: "失败" },
};
/** Each status says what it actually claims; accepted is never shown as done. */
const statusNote: Record<string, string> = {
  accepted: "只表示平台已被受理，执行结果还没有依据。",
  in_progress: "已经认领或已经发出，结果还没有确定。",
  observed: "有后续观测或权威记录作为依据。",
  unknown: "结果无法确认；平台不会自动重发。",
  failed: "执行方明确拒绝或明确失败。",
};
const stageWording: Record<string, string> = {
  pending: "等待执行",
  observing: "已认领未发出",
  executing: "已发出待回执",
  accepted: "收到回执",
  observed: "观测确认",
  unknown: "结果未知",
  rejected: "被拒绝",
  published: "权威版本已核对",
  unverified: "权威版本未确认",
  corrupt: "权威内容不一致",
  queued: "等待下载",
  downloading: "正在下载",
  validating: "校验成品",
  decoding: "自动解锁音频",
  metadata_ready: "元数据已就绪",
  publishing: "正在发布",
  asset_indexed: "资产已入库",
  library_verifying: "媒体服务器核对中",
  completed: "核对完成",
  auth_required: "需要重新登录 B 站",
  waiting_metadata: "等待补全元数据",
  retry_wait: "等待重试",
  paused: "已暂停",
  cancelled: "已取消",
  failed: "失败",
  finished: "本轮结束",
};
const sourceWording: Record<string, string> = {
  "platform.plugins": "插件动作",
  "platform.home": "家庭设备控制",
  "platform.models": "模型配置发布",
  "platform.infrastructure": "容器与 Compose 控制",
  "companion.core": "Core 对话与写作",
  "assets.remote": "资产远端任务",
  "resources.download": "订阅与下载",
  "platform.dialogue": "网页对话",
};
const sourceDetail: Record<string, string> = {
  "companion.core": "对话与写作任务还没有正式的任务合同，本轮没有接入。",
  "assets.remote": "资产远端任务还没有正式的任务合同，本轮没有接入。",
  "resources.download": "显示已记录的音视频下载与归档进度。",
  "platform.dialogue":
    "网页对话只有发送回执，没有可投影的操作台账，本轮没有接入。",
};
const kindWording: Record<string, string> = {
  "device.control": "设备控制",
  "model.publish": "模型发布",
  "media.download": "媒体下载与入库",
};
const sourceState: Record<string, { tone: Tone; label: string; note: string }> =
  {
    online: { tone: "blue", label: "在线", note: "状态读取正常。" },
    offline: {
      tone: "red",
      label: "离线",
      note: "最近一次状态读取失败；已有记录保持原样，不显示成功。",
    },
    never_read: {
      tone: "yellow",
      label: "尚未读取",
      note: "还没有成功读取过状态，因此没有可核对的依据。",
    },
    disabled: {
      tone: "gray",
      label: "未开启",
      note: "设置里没有开启这一块，历史记录仍然保留。",
    },
    credential_missing: {
      tone: "red",
      label: "缺少凭据",
      note: "登记的凭据环境变量没有值。",
    },
    unconfigured: {
      tone: "gray",
      label: "未配置",
      note: "这个部署没有登记该来源。",
    },
    missing: {
      tone: "yellow",
      label: "登记已移除",
      note: "登记被移除，历史记录仍然保留在这里。",
    },
    unreadable: {
      tone: "red",
      label: "无法读取",
      note: "台账暂时读不到，这里的内容可能不是最新。",
    },
    available: {
      tone: "blue",
      label: "可读",
      note: "权威记录与意图台账都能读取。",
    },
    empty: {
      tone: "gray",
      label: "暂无记录",
      note: "还没有发布或撤销过任何版本。",
    },
    not_connected: { tone: "gray", label: "未接入", note: "" },
  };
const originWording: Record<string, { tone: Tone; label: string }> = {
  current: { tone: "blue", label: "登记仍在" },
  origin_missing: { tone: "yellow", label: "登记已移除" },
  source_missing: { tone: "gray", label: "来源已移除" },
};
const cancelWording: Record<string, string> = {
  executor_does_not_cancel:
    "执行方不提供取消：已经发出的设备指令无法撤回，也不会从任务中心自动重发。要改变状态请到家庭设备页重新操作。",
  recorded_fact:
    "这是已经发生并记录的发布事实，不是可以取消的操作。要改变配置请到模型与用量页发布新版本。",
  media_job_control: "请到订阅与下载页取消任务；已发布的成品会保留。",
  terminal_state: "任务已经结束，不再接受取消。",
};
const fieldWording: Record<string, string> = {
  acceptance: "受理",
  receipt: "回执中出现目标状态",
  observation: "观测结果",
  observation_state: "观测状态",
  observed_at: "观测时间",
  revision: "读数修订号",
  operation: "操作",
  actor: "操作者",
  integrity: "权威内容",
  availability: "记录可用性",
  revoked: "已撤销",
  claim: "另有未确认回执",
  receipt_recorded: "回执已落库",
  authoritative: "权威版本",
  window: "受理窗口",
  requested_state: "请求状态",
  template_id: "模板",
  entity_id: "实体",
  service: "服务",
  target: "目标",
  version: "版本",
  recorded_at: "记录时间",
  updated_at: "更新时间",
  published_at: "发布时间",
  usable_until: "可用至",
  revoked_at: "撤销时间",
  settled_at: "结案时间",
  job_id: "任务标识",
  bvid: "视频标识",
  provider: "音乐平台",
  source_id: "歌曲标识",
  audio_archived: "音乐已归档",
  audio_quality: "实际音质",
  audio_unlock: "音频解锁",
  format: "原始格式",
  tool: "处理工具",
  input_sha256: "原件摘要",
  input_size_bytes: "原件大小",
  cid: "分 P 标识",
  actual_quality: "实际画质",
  target_id: "目标媒体库",
  cancel_requested: "已请求取消",
  asset_indexed: "资产已入库",
  library_verified: "媒体服务器已核对",
  result: "执行结果",
  node_id: "节点",
  connection_revision: "连接修订号",
  stack_id: "栈身份",
  container_id: "容器身份",
  action: "请求动作",
  command_state: "命令结果",
  execution_active: "命令仍在运行",
  runtime_state: "实际运行",
  health_state: "健康检查",
  containers: "容器读回",
  version_id: "配置版本",
  node_operation_id: "节点操作标识",
  state: "状态",
  health: "健康",
  media_servers: "媒体服务器结果",
  server_id: "媒体服务器",
  kind: "类型",
  code: "原因",
  item_id: "项目标识",
};
const valueWording: Record<string, string> = {
  not_needed: "无需解锁",
  decoding: "正在解锁",
  decoded: "已解锁",
  key_required: "缺少客户端密钥",
  interrupted: "已中断",
  unsupported: "版本不受支持",
  pending: "等待",
  observing: "已认领",
  sending: "已发出",
  accepted: "已受理",
  observed: "已观测",
  unknown: "未知",
  rejected: "已拒绝",
  confirmed: "与目标一致",
  contradicted: "与目标不一致",
  unreadable: "不可读",
  digest_match: "摘要一致",
  digest_mismatch: "摘要不一致",
  absent: "权威行不存在",
  open: "未到期",
  expired: "已过期",
  published: "已发布并核对",
  unverified: "未确认",
  corrupt: "内容不一致",
  config: "Chat 兼容配置",
  native: "原生配置",
  "config.publish": "发布 Chat 配置",
  "native_config.publish": "发布原生配置",
  "config.revoke": "撤销 Chat 配置",
  "native_config.revoke": "撤销原生配置",
  submitted: "已受理",
  not_sent: "尚未发送",
  sent: "已发送",
  succeeded: "成功",
  running: "运行中",
  stopped: "已停止",
  partial: "部分结果",
  none: "无",
  not_configured: "未配置",
  healthy: "检查通过",
  unhealthy: "检查异常",
  "stack.apply": "执行部署计划",
  verified: "已核对",
  unavailable: "不可用",
  library_verified: "媒体库已核对",
  media_server_unavailable: "媒体服务器不可用",
};
const dateOnly = /^\d{4}-\d{2}-\d{2}T/;

type Tone = "blue" | "yellow" | "red" | "gray";

function rail(
  table: Record<string, { tone: Tone; label: string }>,
  key: string,
) {
  return table[key] ?? { tone: "gray" as Tone, label: "未知" };
}

function stamp(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-CN", { hour12: false });
}

function reason(cause: unknown) {
  if (cause instanceof WebError) return cause.message;
  if (cause instanceof Error && cause.name === "AbortError")
    return "请求已取消，记录没有改变。";
  // A transport failure has no server word for it: say what is actually known.
  return "连接中断，请重新连接。";
}

/** Evidence may include nested executor results or media-server observations. */
function show(value: TaskEvidence[string]): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") return String(value);
  if (Array.isArray(value)) return value.map(show).join("；") || "—";
  if (typeof value === "object")
    return (
      Object.entries(value)
        .map(([key, part]) => `${fieldWording[key] ?? key}：${show(part)}`)
        .join("；") || "—"
    );
  if (dateOnly.test(value)) return stamp(value);
  return valueWording[value] ?? value;
}

/** A new page never hides a record the page already showed: same id keeps its place. */
function merge(current: TaskItem[], incoming: TaskItem[]) {
  const seen = new Map(
    current.map((item, position) => [item.task_id, position]),
  );
  const next = [...current];
  for (const item of incoming) {
    const position = seen.get(item.task_id);
    if (position === undefined) next.push(item);
    else next[position] = item;
  }
  return next;
}

/**
 * A rejected read is one of exactly two things, and they are never the same:
 *
 * * the session behind this panel is gone (expired, revoked in another tab, or refused because
 *   the request no longer passes the origin/CSRF check) — the view is not stale, it is invalid,
 *   so it must be forgotten and the operator asked to log in again;
 * * the read itself failed (offline, connector unavailable) — the records already read stay on
 *   screen and are plainly marked as a stale snapshot instead of being shown as a fresh success.
 */
function authLost(cause: unknown) {
  return (
    cause instanceof WebError && (cause.status === 401 || cause.status === 403)
  );
}
const STALE = "连接中断，下面仍是上次成功读取的快照；恢复后这里会自动更新。";

type Slot = "view" | "detail" | "poll";
/** One request plus the question it was asked: an answer to an older question is dropped. */
type Ticket = { controller: AbortController; filter: string; session: number };

export function TasksPanel() {
  const [session, setSession] = useState<SessionState | null>(null);
  useSessionGuard(session);
  const [items, setItems] = useState<TaskItem[]>([]);
  const [page, setPage] = useState<TasksPage | null>(null);
  const [sources, setSources] = useState<TaskSource[]>([]);
  const [generatedAt, setGeneratedAt] = useState("");
  const [status, setStatus] = useState("");
  const [source, setSource] = useState("");
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [open, setOpen] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [stale, setStale] = useState("");
  const tickets = useRef<Record<Slot, Ticket | null>>({
    view: null,
    detail: null,
    poll: null,
  });
  const sessionGeneration = useRef(0);
  const question = useRef("");
  const alive = useRef(true);
  const read = useRef<string | null>(null);
  const auth = useRef<SessionState | null>(null);
  const cursor = useRef<string | null>(null);
  const known = useRef<TaskItem[]>([]);
  const list = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const host = useRef<HTMLElement>(null);

  const filters = `${status}|${source}`;

  /** Start one request in its slot, cancelling whatever was asking the same slot before. */
  const begin = useCallback((slot: Slot, filter: string) => {
    tickets.current[slot]?.controller.abort();
    const ticket: Ticket = {
      controller: new AbortController(),
      filter,
      session: sessionGeneration.current,
    };
    tickets.current[slot] = ticket;
    return ticket;
  }, []);

  const abortSlot = useCallback((slot: Slot) => {
    tickets.current[slot]?.controller.abort();
    tickets.current[slot] = null;
  }, []);

  const abortAll = useCallback(() => {
    for (const slot of ["view", "detail", "poll"] as Slot[]) abortSlot(slot);
  }, [abortSlot]);

  /** Still the request this panel is waiting for: alive, not replaced, not superseded. */
  const live = useCallback((slot: Slot, ticket: Ticket) => {
    return (
      alive.current &&
      !ticket.controller.signal.aborted &&
      tickets.current[slot] === ticket &&
      ticket.session === sessionGeneration.current
    );
  }, []);

  /** Live *and* still asked about the filter the panel is showing. Only then may it write. */
  const mine = useCallback(
    (slot: Slot, ticket: Ticket) =>
      live(slot, ticket) && ticket.filter === question.current,
    [live],
  );

  /**
   * Effect setups and cleanups come in pairs, and in development StrictMode React deliberately
   * runs them twice on the same instance (setup, cleanup, setup). The cleanup marks the panel not
   * alive and cancels whatever was reading; the next setup marks it alive again. Requests that
   * belonged to a torn-down setup keep failing the identity check in `live`, so an answer to an
   * older setup can never write — while the setup that is actually mounted reads normally.
   */
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      abortAll();
    };
  }, [abortAll]);

  /**
   * The session as the effects need it. It is a ref on purpose: a session that arrives must not
   * re-run a setup, because re-running would cancel the very read that just delivered it.
   */
  useEffect(() => {
    auth.current = session;
  }, [session]);

  useEffect(() => {
    known.current = items;
  }, [items]);

  const body = useCallback(
    (extra: object = {}) => ({
      status: status || null,
      source: source || null,
      page_size: PAGE_SIZE,
      ...extra,
    }),
    [source, status],
  );

  /** Forget everything that belonged to the session that just went away. */
  const forget = useCallback(() => {
    // A keyboard user focused inside a detail that is being removed must not be dropped to <body>.
    const focused = document.activeElement;
    if (
      focused &&
      focused !== document.body &&
      panel.current?.contains(focused)
    ) {
      window.setTimeout(() => host.current?.focus(), 0);
    }
    cursor.current = null;
    known.current = [];
    setItems([]);
    setPage(null);
    setSources([]);
    setGeneratedAt("");
    setDetail(null);
    setOpen("");
    setNotice("");
    setStale("");
  }, []);

  /**
   * The session this panel was reading with is gone. Nothing read under it may stay: the records,
   * the open detail and the paging cursor all belonged to it. Re-read the session once so the
   * panel says what it actually is — a login form when the session really ended, an explicit
   * refusal otherwise — instead of leaving a view that can never be refreshed again.
   */
  const lose = useCallback(
    async (cause: unknown, filter: string) => {
      sessionGeneration.current += 1;
      abortAll();
      forget();
      setBusy(true);
      const ticket = begin("view", filter);
      try {
        const state = await readSession(ticket.controller.signal);
        if (!live("view", ticket)) return;
        setSession(state);
        setError(
          state.authenticated
            ? reason(cause)
            : "登录已失效，请重新登录后再查看任务记录。",
        );
      } catch (failure) {
        if (!live("view", ticket)) return;
        setError(reason(failure));
      } finally {
        if (live("view", ticket)) setBusy(false);
        if (tickets.current.view === ticket) tickets.current.view = null;
      }
    },
    [abortAll, begin, forget, live],
  );

  /**
   * The first page of one question, read with a ticket its caller already opened: the session
   * first, then the records it read them with. When it decides an answer — records, a failed
   * read, or a session that is gone — it records the question in `read`, so the panel does not
   * ask the same question over and over. A read that was cancelled before deciding records
   * nothing, which is what lets the next setup ask again.
   */
  const first = useCallback(
    async (filter: string, ticket: Ticket) => {
      setBusy(true);
      setError("");
      // A whole-panel read is fresher than any poll already on its way: that answer is now older.
      abortSlot("poll");
      try {
        const state = await readSession(ticket.controller.signal);
        if (!live("view", ticket)) return;
        setSession(state);
        if (!state.authenticated) {
          read.current = filter;
          forget();
          return;
        }
        const result = await call<TasksView>(
          "tasks/view",
          body(),
          state.csrf,
          ticket.controller.signal,
        );
        if (!mine("view", ticket)) return;
        read.current = filter;
        cursor.current = result.page.next_cursor;
        setItems(result.items);
        setPage(result.page);
        setSources(result.sources);
        setGeneratedAt(result.generated_at);
        setStale("");
      } catch (cause) {
        if (!live("view", ticket)) return;
        // A failed read is still an answer to this question: say so instead of asking again.
        read.current = filter;
        if (authLost(cause)) {
          await lose(cause, filter);
          return;
        }
        setError(reason(cause));
        setStale(STALE);
      } finally {
        if (live("view", ticket)) setBusy(false);
        if (tickets.current.view === ticket) tickets.current.view = null;
      }
    },
    [abortSlot, body, forget, live, lose, mine],
  );

  /**
   * A filter is the question the panel is asking, and it is recorded synchronously — before any
   * await — so an answer to a previous question can no longer write.
   *
   * The read belongs to this effect setup. If the setup is torn down before its read decides,
   * nothing is recorded and the next setup asks again: that covers a filter change, a remount,
   * and the development StrictMode replay (setup, cleanup, setup), which is why the panel loads
   * there instead of waiting forever for an answer it cancelled itself.
   */
  useEffect(() => {
    const changed = question.current !== filters;
    question.current = filters;
    if (changed) {
      abortSlot("detail");
      setOpen("");
      setDetail(null);
      setNotice("");
    }
    // A session already known to be gone shows the login form and reads nothing. The first read
    // happens anyway on a fresh mount: reading the session is how the panel finds out it is gone.
    if (auth.current && !auth.current.authenticated) return;
    if (read.current === filters) return;
    const ticket = begin("view", filters);
    void first(filters, ticket);
    return () => {
      // This setup is leaving; what it was reading can no longer speak for the panel.
      abortSlot("view");
    };
  }, [abortSlot, begin, filters, first]);

  const refresh = useCallback(async () => {
    const ticket = begin("view", question.current);
    await first(question.current, ticket);
  }, [begin, first]);

  /** Polling first: a rejected poll never replaces what the panel already read. */
  useEffect(() => {
    if (!session?.authenticated) return;
    let disposed = false;
    const tick = async () => {
      if (disposed || !alive.current) return;
      if (document.visibilityState !== "visible") return;
      // A page being appended must finish before its cursor can be replaced.
      if (tickets.current.view) return;
      const ticket = begin("poll", question.current);
      try {
        const result = await call<TasksView>(
          "tasks/view",
          body(),
          session.csrf,
          ticket.controller.signal,
        );
        if (!mine("poll", ticket)) return;
        const seen = new Set(known.current.map((item) => item.task_id));
        const added = result.items.filter((item) => !seen.has(item.task_id));
        // Every successful poll starts a new snapshot and walk. Keeping an old cursor
        // would skip the middle of a burst; retaining old rows would also retain
        // records which no longer match this status filter.
        cursor.current = result.page.next_cursor;
        setItems(result.items);
        setPage(result.page);
        setSources(result.sources);
        setGeneratedAt(result.generated_at);
        setStale("");
        if (added.length) setNotice(`有 ${added.length} 条新的操作记录。`);
      } catch (cause) {
        if (!mine("poll", ticket)) return;
        if (authLost(cause)) {
          await lose(cause, question.current);
          return;
        }
        // Offline or a failing connector: the snapshot stays, and it says that it is one.
        setStale(STALE);
      } finally {
        if (tickets.current.poll === ticket) tickets.current.poll = null;
      }
    };
    const timer = window.setInterval(() => void tick(), POLL_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") void tick();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      disposed = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      // The session is gone or the panel is leaving: an in-flight poll must not come back.
      abortSlot("poll");
    };
  }, [abortSlot, begin, body, lose, mine, session]);

  const close = useCallback(() => {
    const task = open;
    abortSlot("detail");
    setOpen("");
    setDetail(null);
    if (task)
      list.current
        ?.querySelector<HTMLButtonElement>(`[data-task-toggle="${task}"]`)
        ?.focus();
  }, [abortSlot, open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [close, open]);

  async function openDetail(taskId: string) {
    if (open === taskId) {
      close();
      return;
    }
    setOpen(taskId);
    setDetail(null);
    setNotice("");
    const ticket = begin("detail", question.current);
    try {
      const result = await call<TaskDetailView>(
        "tasks/detail",
        { task_id: taskId },
        session?.csrf ?? "",
        ticket.controller.signal,
      );
      if (!mine("detail", ticket)) return;
      setDetail(result.task);
      window.setTimeout(() => panel.current?.focus(), 0);
    } catch (cause) {
      if (!mine("detail", ticket)) return;
      if (authLost(cause)) {
        await lose(cause, question.current);
        return;
      }
      setError(reason(cause));
    }
  }

  async function more() {
    if (!session || !cursor.current) return;
    setBusy(true);
    setError("");
    abortSlot("poll");
    const ticket = begin("view", question.current);
    try {
      const result = await call<TasksView>(
        "tasks/view",
        body({ cursor: cursor.current }),
        session.csrf,
        ticket.controller.signal,
      );
      if (!mine("view", ticket)) return;
      cursor.current = result.page.next_cursor;
      setItems((shown) => merge(shown, result.items));
      setPage(result.page);
      setSources(result.sources);
      setStale("");
    } catch (cause) {
      if (!mine("view", ticket)) return;
      const message = reason(cause);
      if (authLost(cause)) {
        await lose(cause, question.current);
        return;
      }
      setError(message);
      if (cause instanceof WebError && cause.code === "cursor_conflict") {
        // The list conditions changed under the cursor: read the first page again.
        const retry = begin("view", question.current);
        await first(question.current, retry);
        setError(message);
      } else {
        setStale(STALE);
      }
    } finally {
      if (mine("view", ticket)) setBusy(false);
      if (tickets.current.view === ticket) tickets.current.view = null;
    }
  }

  const connected = useMemo(
    () => sources.filter((item) => item.connected),
    [sources],
  );
  const knownRecords = connected.reduce(
    (total, item) => total + (item.records ?? 0),
    0,
  );
  const unreadable = connected.filter((item) => item.state === "unreadable");
  const picked = sources.find((item) => item.id === source);
  const filtering = Boolean(status || source);

  return (
    <section
      className="panel tasks"
      aria-label="任务中心"
      aria-busy={busy}
      ref={host}
      tabIndex={-1}
    >
      <div className="section-heading">
        <h2>任务</h2>
        {page && (
          <StatusRail
            tone={items.length ? "blue" : "gray"}
            label={`已显示 ${items.length} 条`}
          />
        )}
      </div>
      <p className="muted">
        这里列出本平台已经真实记录的模型发布与设备控制操作，直接读取它们各自的台账，
        不新建一套执行数据库。受理、执行中、已观测、未知与失败分开显示：受理不是执行，
        回执不是结果，无法确认的结果保持未知。
      </p>
      <div className="tasks-status" role="status">
        {busy
          ? "正在读取操作记录…"
          : session?.authenticated
            ? `已登录 · ${session.username} · 最近读取 ${stamp(generatedAt)}`
            : "请登录本机管理员账号"}
      </div>
      {error && (
        <p className="tasks-error" role="alert">
          {error}
        </p>
      )}
      {stale && session?.authenticated ? (
        <p className="tasks-stale" role="status">
          {stale}
        </p>
      ) : null}
      {notice && (
        <p className="tasks-notice" role="status">
          {notice}
        </p>
      )}
      {!session ? (
        <StatePanel kind="loading" title="正在连接任务中心">
          <p>稍候。</p>
        </StatePanel>
      ) : !session.authenticated ? (
        <LoginLink />
      ) : (
        <>
          <div className="tasks-actions">
            <button
              className="button"
              onClick={() => void refresh()}
              disabled={busy}
            >
              <RefreshCw aria-hidden="true" />
              重新读取
            </button>
          </div>
          <div className="tasks-sources" aria-label="操作来源">
            {sources.map((item) => {
              const state = sourceState[item.state] ?? {
                tone: "gray" as Tone,
                label: "未知",
                note: "来源没有报告状态。",
              };
              return (
                <article className="tasks-source" key={item.id}>
                  <div className="tasks-source-head">
                    <h3>
                      {sourceWording[item.id] ?? item.id}
                      {item.kind ? (
                        <span className="muted">
                          {" "}
                          · {kindWording[item.kind] ?? item.kind}
                        </span>
                      ) : null}
                    </h3>
                    <StatusRail tone={state.tone} label={state.label} />
                  </div>
                  <p className="muted">
                    {item.connected
                      ? `已记录 ${item.records ?? "未知"} 条 · ${state.note}`
                      : sourceDetail[item.id]}
                  </p>
                </article>
              );
            })}
          </div>
          <div className="tasks-filters">
            <label>
              状态
              <select
                value={status}
                onChange={(event) => setStatus(event.target.value)}
                disabled={busy}
              >
                <option value="">全部状态</option>
                {Object.entries(statusRail).map(([value, item]) => (
                  <option value={value} key={value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              来源
              <select
                value={source}
                onChange={(event) => setSource(event.target.value)}
                disabled={busy}
              >
                <option value="">全部来源</option>
                {sources.map((item) => (
                  <option value={item.id} key={item.id}>
                    {sourceWording[item.id] ?? item.id}
                    {item.connected ? "" : "（未接入）"}
                  </option>
                ))}
              </select>
            </label>
            {filtering && (
              <button
                className="button"
                onClick={() => {
                  setStatus("");
                  setSource("");
                }}
                disabled={busy}
              >
                <ListFilter aria-hidden="true" />
                清除筛选
              </button>
            )}
          </div>
          {!items.length ? (
            picked && !picked.connected ? (
              <StatePanel kind="unconfigured" title="这个来源还没有接入">
                <p>{sourceDetail[picked.id]}</p>
              </StatePanel>
            ) : unreadable.length === connected.length && connected.length ? (
              <StatePanel kind="error" title="操作台账暂时读不到">
                <p>
                  责任模块自己的记录无法读取，因此这里不显示任何记录，也不把空白当作没有操作。
                </p>
              </StatePanel>
            ) : filtering ? (
              <StatePanel kind="empty" title="当前筛选没有记录">
                <p>
                  已记录的 {knownRecords}{" "}
                  条操作都不符合当前筛选，清除筛选可以看到全部。
                </p>
              </StatePanel>
            ) : (
              <StatePanel kind="empty" title="还没有任何平台操作记录">
                <p>
                  平台还没有发布过模型配置，也没有执行过设备控制。这里只显示真实发生并被记录的操作，
                  不会用占位记录填充。
                </p>
              </StatePanel>
            )
          ) : (
            <div className="tasks-list" ref={list}>
              {items.map((item) => {
                const state = rail(statusRail, item.status);
                const origin = rail(originWording, item.source_state);
                const expanded = open === item.task_id;
                return (
                  <article
                    className={`tasks-item${item.attention ? " attention" : ""}`}
                    key={item.task_id}
                  >
                    <div className="tasks-item-head">
                      <div>
                        <h3>{item.title}</h3>
                        <p className="muted">
                          {sourceWording[item.source] ?? item.source} ·{" "}
                          {kindWording[item.kind] ?? item.kind} · {item.target}
                        </p>
                      </div>
                      <StatusRail tone={state.tone} label={state.label} />
                    </div>
                    <dl className="tasks-facts">
                      <div>
                        <dt>阶段</dt>
                        <dd>
                          {item.source === "resources.download" &&
                          item.stage === "published"
                            ? "发布完成（未配置媒体服务器）"
                            : (stageWording[item.stage] ?? item.stage)}
                        </dd>
                      </div>
                      <div>
                        <dt>记录时间</dt>
                        <dd>{stamp(item.created_at)}</dd>
                      </div>
                      <div>
                        <dt>更新时间</dt>
                        <dd>
                          {stamp(item.updated_at)}
                          {item.pending ? "（受理后尚未结案）" : ""}
                        </dd>
                      </div>
                      <div>
                        <dt>记录来源</dt>
                        <dd>{origin.label}</dd>
                      </div>
                      <div>
                        <dt>结果依据</dt>
                        <dd>
                          {statusNote[item.status] ?? "结果无法确认。"}
                          {item.code ? `（${item.code}）` : ""}
                        </dd>
                      </div>
                    </dl>
                    <div className="tasks-item-actions">
                      <button
                        className="button"
                        data-task-toggle={item.task_id}
                        aria-expanded={expanded}
                        onClick={() => void openDetail(item.task_id)}
                      >
                        {expanded ? "收起详情" : "查看详情"}
                      </button>
                      {!item.cancel.supported && item.pending && (
                        <span className="tasks-chip">不可取消</span>
                      )}
                    </div>
                    {expanded && (
                      <div
                        className="tasks-detail"
                        role="region"
                        aria-label={`${item.title} 的操作详情`}
                        ref={panel}
                        tabIndex={-1}
                      >
                        {detail?.task_id === item.task_id ? (
                          <>
                            <p className="muted">
                              详情是这一刻的重新读取，不是列表里的旧快照。按 Esc
                              收起并把焦点还给列表。
                            </p>
                            <p className="tasks-cancel">
                              {item.cancel.supported
                                ? "请到下载任务页查看取消视频任务的可用操作。"
                                : `取消：不可用 — ${cancelWording[item.cancel.code] ?? "执行方没有声明取消能力。"}`}
                            </p>
                            <h4>时间线</h4>
                            <dl className="tasks-facts">
                              {Object.entries(detail.timeline).map(
                                ([key, value]) => (
                                  <div key={key}>
                                    <dt>{fieldWording[key] ?? key}</dt>
                                    <dd>{show(value)}</dd>
                                  </div>
                                ),
                              )}
                            </dl>
                            <h4>结果依据</h4>
                            <dl className="tasks-facts">
                              {Object.entries(detail.evidence).map(
                                ([key, value]) => (
                                  <div key={key}>
                                    <dt>{fieldWording[key] ?? key}</dt>
                                    <dd>{show(value)}</dd>
                                  </div>
                                ),
                              )}
                            </dl>
                            <h4>请求内容</h4>
                            <dl className="tasks-facts">
                              {Object.entries(detail.request).map(
                                ([key, value]) => (
                                  <div key={key}>
                                    <dt>{fieldWording[key] ?? key}</dt>
                                    <dd>{show(value)}</dd>
                                  </div>
                                ),
                              )}
                            </dl>
                            <p className="muted">
                              标识 {detail.task_id} · 原始责任模块{" "}
                              {sourceWording[detail.source] ?? detail.source}
                            </p>
                            {detail.module.page ? (
                              <p className="tasks-module">
                                <a
                                  className="button"
                                  href={
                                    detail.source === "resources.download"
                                      ? "#/media/jobs"
                                      : detail.module.page
                                  }
                                >
                                  到责任模块页面查看
                                </a>
                              </p>
                            ) : null}
                          </>
                        ) : (
                          <p className="muted">正在重新读取这条记录…</p>
                        )}
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          )}
          {page?.has_more && (
            <div className="tasks-more">
              <button
                className="button"
                onClick={() => void more()}
                disabled={busy}
              >
                加载更多（已显示 {items.length} 条）
              </button>
              <p className="muted">
                继续读取的游标绑定当前排序与筛选；期间新增的记录比游标更新，会在下一次读取时出现。
              </p>
            </div>
          )}
        </>
      )}
    </section>
  );
}
