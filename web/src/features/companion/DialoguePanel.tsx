import { DetailCard } from "../../components/DetailCard";
import { requestId } from "../../app/requestId";
import { useEffect, useRef, useState } from "react";
import { MessageCircle } from "lucide-react";
import { request, ConversationError, type Session } from "./api";
import { ContentViewer, contentLabel } from "../life/ContentViewer";
import type { content_ref } from "../life/runtimeTypes";
import "../life/life.css";

type Message = {
  message_id: string;
  revision: number;
  parts: { kind: "text"; text: string }[];
  sent_at: string;
  state: string;
};
type Turn = {
  turn_id: string;
  turn_sequence: number;
  version: number;
  phase: string;
  delivery_state: string;
  unresolved_delivery: boolean;
};
type TurnView = {
  turn: Turn;
  messages: Message[];
  replies: {
    reply_id: string;
    segment_sequence: number;
    segment_count: number;
    state: string;
    content_state: string;
    text: string | null;
    content_refs?: content_ref[];
  }[];
};
type Snapshot = {
  observed_at: string;
  collectors: {
    collection_id: string;
    revision: number;
    deadline_at: string;
    messages: Message[];
  }[];
  active_turns: TurnView[];
  history: TurnView[];
  next_before_turn_sequence: number | null;
};
type Submission = {
  message_id: string;
  state: string;
  result: { code?: string } | null;
};
type ReadResult = {
  state: string;
  snapshot: Snapshot | null;
  submissions: Submission[];
};
const states: Record<string, string> = {
  queued: "排队中",
  preparing: "正在准备",
  generating: "后台处理中",
  waiting_dependency: "等待前序对话",
  ready_to_send: "准备回复",
  sending: "正在发送",
  reconciling: "正在核实发送结果",
  sent: "已送达",
  failed: "失败",
  cancelled: "已取消",
  observed: "已观察",
  closed_unknown: "发送结果未知",
  unknown: "未知",
  pending: "等待发送",
  partial: "部分送达",
  not_started: "尚未开始",
  not_required: "无需发送",
  partially_cancelled: "已部分取消",
  too_late: "已无法取消",
  edited: "原消息已编辑",
  retracted: "原消息已撤回",
  unavailable: "正文暂不可用",
};

function Messages({ messages }: { messages: Message[] }) {
  return (
    <>
      {messages.map((message) => (
        <div
          className="chat-message user-message"
          key={`${message.message_id}:${message.revision}`}
        >
          <span className="chat-message-label">
            你 · {new Date(message.sent_at).toLocaleTimeString("zh-CN")}
          </span>
          {message.state === "active" ? (
            message.parts.map((part, index) => <p key={index}>{part.text}</p>)
          ) : (
            <p className="muted">{states[message.state] ?? "正文暂不可用"}</p>
          )}
        </div>
      ))}
    </>
  );
}

export function DialoguePanel({
  session,
  conversation,
  actor,
  actorName = actor,
}: {
  session: Session;
  conversation: string;
  actor: string;
  actorName?: string;
}) {
  const [draft, setDraft] = useState("");
  const [read, setRead] = useState<ReadResult | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [sending, setSending] = useState(false);
  const [cancelling, setCancelling] = useState("");
  const [before, setBefore] = useState<number | null>(null);
  const [refresh, setRefresh] = useState(0);
  const alive = useRef(true);
  const mutations = useRef(new Set<AbortController>());
  const timeline = useRef<HTMLDivElement>(null);
  const followLatest = useRef(true);
  const readIdentity = useRef("");
  const available = Boolean(
    session.dialogue?.available && conversation && actor,
  );
  const modelUnavailable =
    session.dialogue?.actor_models?.[actor] === false ||
    (session.dialogue?.actor_models?.[actor] === undefined &&
      session.dialogue?.model === "not_configured");

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      for (const c of mutations.current) c.abort();
    };
  }, []);

  useEffect(() => {
    if (!available) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const identity = JSON.stringify([
      session.csrf,
      conversation,
      actor,
      before,
    ]);
    if (readIdentity.current !== identity) {
      setRead(null);
      setError("");
      readIdentity.current = identity;
    }
    async function poll() {
      if (document.hidden) {
        timer = setTimeout(poll, 2000);
        return;
      }
      try {
        const result = await request<ReadResult>(
          "snapshot",
          controller.signal,
          { conversation, actor, before },
          session.csrf,
        );
        if (!controller.signal.aborted) {
          setRead(result);
          setError("");
        }
      } catch (cause) {
        if (!controller.signal.aborted) {
          if (
            cause instanceof ConversationError &&
            [
              "forbidden",
              "upstream_forbidden",
              "scope_changed",
              "not_found",
              "session_expired",
              "unauthorized",
            ].includes(cause.code)
          )
            setRead(null);
          setError(cause instanceof Error ? cause.message : "连接中断");
        }
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, 2000);
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [session, conversation, actor, before, refresh, available]);

  async function send() {
    if (!available || modelUnavailable || !draft.trim() || sending) return;
    const controller = new AbortController();
    mutations.current.add(controller);
    setSending(true);
    setError("");
    setNotice("正在提交原文…");
    const text = draft;
    followLatest.current = true;
    setDraft("");
    try {
      const result = await request<Submission>(
        "messages",
        controller.signal,
        { conversation, actor, text, client_request_id: requestId() },
        session.csrf,
      );
      if (alive.current) {
        setNotice(
          result.state === "accepted"
            ? "消息已提交。"
            : `提交结果：${states[result.state] ?? result.state}${result.result?.code ? ` · ${result.result.code}` : ""}。不会自动重发。`,
        );
        setBefore(null);
        setRefresh((n) => n + 1);
      }
    } catch (cause) {
      if (alive.current)
        setNotice(
          `提交结果未确认，不会自动重发。${cause instanceof Error ? cause.message : "请重新连接查看后台状态。"}`,
        );
    } finally {
      mutations.current.delete(controller);
      if (alive.current) setSending(false);
    }
  }

  async function cancel(turn: Turn) {
    const controller = new AbortController();
    mutations.current.add(controller);
    setCancelling(turn.turn_id);
    try {
      const result = await request<{ state: string; version: number }>(
        "cancel",
        controller.signal,
        {
          conversation,
          actor,
          turn_id: turn.turn_id,
          expected_revision: turn.version,
        },
        session.csrf,
      );
      if (alive.current) {
        setNotice(
          `取消结果：${states[result.state] ?? "请查看后台状态"}。已送达内容不会撤回。`,
        );
        setRefresh((n) => n + 1);
      }
    } catch (cause) {
      if (alive.current) {
        setNotice(
          `取消未确认：${cause instanceof Error ? cause.message : "请刷新状态"}`,
        );
        setRefresh((n) => n + 1);
      }
    } finally {
      mutations.current.delete(controller);
      if (alive.current) setCancelling("");
    }
  }

  const snapshot = read?.snapshot;
  useEffect(() => {
    if (snapshot && followLatest.current && !before && timeline.current)
      timeline.current.scrollTop = timeline.current.scrollHeight;
  }, [snapshot, before]);
  const turns = snapshot
    ? [...snapshot.history, ...snapshot.active_turns].sort(
        (a, b) => a.turn.turn_sequence - b.turn.turn_sequence,
      )
    : [];
  return (
    <>
      {!available ? (
        <div className="chat-empty">
          <MessageCircle aria-hidden="true" />
          <h3>对话通道尚未接通</h3>
          <p>登录服务已连接；消息历史与回复通道仍待接入。</p>
          <p>
            {modelUnavailable ? "模型尚未配置。" : "模型运行状态尚未核验。"}
            这里不会生成演示回复。
          </p>
        </div>
      ) : (
        <>
          <div className="chat-actions">
            <span className="muted">
              {snapshot
                ? `已同步 · ${new Date(snapshot.observed_at).toLocaleTimeString("zh-CN")}`
                : error
                  ? "连接中断，内容已隐藏"
                  : "正在读取后台状态…"}
            </span>
            <button
              className="button"
              onClick={() => {
                followLatest.current = true;
                setBefore(null);
                setRefresh((n) => n + 1);
              }}
            >
              回到最新
            </button>
            {snapshot?.next_before_turn_sequence && (
              <button
                className="button"
                onClick={() => setBefore(snapshot.next_before_turn_sequence)}
              >
                更早对话
              </button>
            )}
          </div>
          {error && (
            <p className="chat-error" role="alert">
              {error}
            </p>
          )}
          <div
            className="chat-timeline scroll-layout scroll-pane"
            ref={timeline}
            onScroll={(event) => {
              const node = event.currentTarget;
              followLatest.current =
                node.scrollHeight - node.clientHeight - node.scrollTop < 100;
            }}
            aria-label="对话记录"
            tabIndex={0}
          >
            {read?.state === "not_started" && (
              <p className="muted">
                尚无已登记对话。发送第一条消息后，后台会建立会话。
              </p>
            )}
            {snapshot && !turns.length && !snapshot.collectors.length && (
              <p className="muted">这个会话还没有可显示的消息。</p>
            )}
            {turns.map(({ turn, messages, replies }) => (
              <article className="chat-turn" key={turn.turn_id}>
                <div className="chat-turn-heading">
                  <DetailCard
                    title={`第 ${turn.turn_sequence} 轮 · ${states[turn.phase] ?? "状态待确认"}`}
                    className="muted"
                  >
                    <p>
                      {states[turn.delivery_state] ?? "送达待确认"} · 已送{" "}
                      {replies.filter((reply) => reply.state === "sent").length}{" "}
                      段
                    </p>
                    <p>
                      版本 {turn.version} · {turn.phase} / {turn.delivery_state}
                    </p>
                    {replies.map((reply) => (
                      <p key={reply.reply_id}>
                        第 {reply.segment_sequence} 段：{reply.state} /{" "}
                        {reply.content_state}
                      </p>
                    ))}
                  </DetailCard>
                  {snapshot?.active_turns.some(
                    (t) => t.turn.turn_id === turn.turn_id,
                  ) && (
                    <button
                      className="button"
                      disabled={!!cancelling}
                      onClick={() => void cancel(turn)}
                    >
                      {cancelling === turn.turn_id
                        ? "正在请求取消…"
                        : `取消第 ${turn.turn_sequence} 轮`}
                    </button>
                  )}
                </div>
                <Messages messages={messages} />
                {replies.map((reply) => (
                  <div
                    className="chat-message actor-message"
                    key={reply.reply_id}
                  >
                    <span className="chat-message-label">
                      {actorName} · {states[reply.state] ?? "状态待确认"}
                    </span>
                    {reply.content_state === "available" &&
                    reply.text !== null ? (
                      <p>{reply.text}</p>
                    ) : (
                      <p className="muted">正文暂不可展示</p>
                    )}
                    {reply.content_state === "available" &&
                      reply.content_refs?.map((ref) => (
                        <DetailCard
                          title={
                            <>
                              打开引用原件 · {contentLabel(ref.kind)} · 版本{" "}
                              {ref.version}
                            </>
                          }
                          className="chat-content-reference"
                        >
                          <ContentViewer
                            key={`${ref.object_id}:${ref.version}`}
                            access={{ actor, csrf: session.csrf }}
                            contentRef={ref}
                          />
                        </DetailCard>
                      ))}
                  </div>
                ))}
                {turn.unresolved_delivery && (
                  <p className="chat-error">
                    发送结果仍未知，系统不会据此再次发送。
                  </p>
                )}
              </article>
            ))}
            {snapshot?.collectors.map((collector) => (
              <article className="chat-turn" key={collector.collection_id}>
                <h3>正在合并续句</h3>
                <p className="muted">
                  后台等待至{" "}
                  {new Date(collector.deadline_at).toLocaleTimeString(
                    "zh-CN",
                  )}{" "}
                </p>
                <DetailCard title="合并状态详情" className="muted">
                  <p>版本 {collector.revision}</p>
                </DetailCard>
                <Messages messages={collector.messages} />
              </article>
            ))}
          </div>
          {read?.submissions.some((s) => s.state === "unknown") && (
            <p className="chat-error" role="status">
              有入站提交结果未知。请以后台快照为准；重连不会重新发送。
            </p>
          )}
        </>
      )}
      {modelUnavailable && (
        <a className="button primary" href="#/settings/models">
          前往配置模型供应商
        </a>
      )}
      <p role="status">{notice}</p>
      <form
        className="chat-composer"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <label htmlFor="chat-draft">想说些什么</label>
        <textarea
          id="chat-draft"
          rows={3}
          maxLength={8000}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault();
              void send();
            }
          }}
          placeholder="可以先写下想说的话，草稿只保留在当前页面。"
          aria-describedby="chat-send-help"
        />
        <div className="chat-compose-bottom">
          <p id="chat-send-help" className="muted">
            {!available
              ? "发送暂不可用。接入后"
              : modelUnavailable
                ? "模型尚未配置，发送不可用。"
                : ""}
            Enter 发送，Shift + Enter 换行。短时间补充的消息会一起回复。
          </p>
          <button
            className="button primary"
            type="submit"
            disabled={
              !available || sending || !draft.trim() || modelUnavailable
            }
          >
            {sending ? "正在提交…" : "发送"}
          </button>
        </div>
      </form>
    </>
  );
}
