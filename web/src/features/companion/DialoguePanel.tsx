import { readableRecord } from "../../design/readableRecord";
import { useEffect, useState } from "react";
import { useAuth } from "../../app/Auth";
import type { ConversationSelection } from "../../app/RoleWorkspace";
import {
  useDomainCommand,
  useDomainResource,
  type DomainTarget,
} from "../../api/useDomainResource";
import { uploadMedia, type UploadedMedia } from "../../api/uploads";
import type { ArtifactRef, Page, Receipt } from "../../api/contracts";
import { MediaPreview } from "../../components/MediaPreview";
import { CommandFeedback } from "../../components/CommandFeedback";
import { ContextSelection } from "../../components/ContextSelection";
import { TurnEvidence } from "./TurnEvidence";
import { DialogueFlowEvidence, type DialogueFlow } from "./DialogueFlow";
import { InputStatusEvidence, needsLearningRefresh, type InputStatus } from "./InputStatusEvidence";

type Part = {
  kind: string;
  text?: string;
  media?: ArtifactRef["media"];
  metadata?: Record<string, unknown>;
  user_id?: string | number;
  message_id?: string | number;
  id?: string | number;
  parts?: Part[];
  channel?: string;
  segment?: { type: string; data: Record<string, unknown> };
};
type Message = {
  id: number;
  turn_id?: string | null;
  speaker: string;
  parts: Part[];
  created_at: string;
};
type Turn = {
  turn_id: string;
  state: string;
  revision: number;
  receipt: Receipt | null;
  missing: string[];
  action_ids: string[];
  expression_id: string | null;
  error_code: string | null;
  context_selection?: unknown;
  token_usage?: unknown;
  provider_usage?: unknown;
  flow?: DialogueFlow | null;
  input_status?: InputStatus[] | null;
};
const active = new Set([
  "collecting",
  "queued",
  "running",
  "waiting_external",
  "waiting_expression",
]);
const labels: Record<string, string> = {
  collecting: "正在收集连续消息",
  queued: "等待回应",
  running: "正在回应",
  waiting_external: "正在等待原任务",
  waiting_expression: "正在准备公开回应",
  completed: "本轮完成",
  failed: "本轮未完成",
  unknown: "原结果尚未确认",
  cancelled: "已停止本轮等待，外部效果以回执为准",
};
const turnErrors: Record<string, string> = {
  context_mandatory_budget_exceeded:
    "本轮必要内容超出上下文预算。请缩短输入或减少附件。",
  invalid_context_input: "本轮输入无法组成有效上下文。",
  invalid_context_budget: "当前上下文预算配置无效。",
  context_source_unavailable: "本轮上下文中的来源已不可用。",
  source_unavailable: "本轮所需的原文依据已不可用。",
  deadline_exceeded: "本轮已超过等待时限。",
  forbidden: "当前会话权限不允许继续本轮。",
  actor_disabled: "当前角色已停用。",
};

export function DialoguePanel({
  actor,
  conversation,
}: {
  actor: string;
  conversation: ConversationSelection;
}) {
  const { session } = useAuth();
  const selector = {
    actor_id: actor,
    conversation_id: conversation.conversation_id,
    ...(conversation.person_id ? { person_id: conversation.person_id } : {}),
  };
  const [cursor, setCursor] = useState<string | null>(null);
  const messages = useDomainResource<Page<Message>>(
    "conversation/messages",
    { ...selector, cursor, limit: 50 },
    true,
    cursor ? 0 : 2500,
  );
  const send = useDomainCommand<{
    turn_id: string;
    input_id: string;
    state: string;
  }>(`${actor}:${conversation.conversation_id}`);
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<UploadedMedia[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [turns, setTurns] = useState<string[]>([]);
  return (
    <div className="dialogue-panel">
      {messages.error && (
        <p className="chat-error" role="alert">
          {messages.error}
        </p>
      )}
      <div className="chat-messages" role="log" aria-label="公开消息">
        {[...(messages.data?.items ?? [])].reverse().map((message) => (
          <article
            key={message.id}
            className={`chat-message ${message.speaker}`}
          >
            <header>
              <strong>
                {message.speaker === "assistant"
                  ? "角色"
                  : "你与会话中的发言者"}
              </strong>
              <time dateTime={message.created_at}>
                {new Date(message.created_at).toLocaleString("zh-CN")}
              </time>
            </header>
            <PublicParts parts={message.parts} />
            {message.turn_id && <TurnEvidence turnId={message.turn_id} selector={selector} />}
          </article>
        ))}
        {messages.data && !messages.data.items.length && (
          <p className="muted">这里还没有可读的公开消息。</p>
        )}
      </div>
      <div className="domain-actions">
        {messages.data?.next_cursor && (
          <button
            className="button"
            onClick={() => setCursor(messages.data!.next_cursor)}
          >
            查看更早消息
          </button>
        )}
        {cursor && (
          <button className="button" onClick={() => setCursor(null)}>
            回到最新消息
          </button>
        )}
      </div>
      <div aria-live="polite">
        {turns.map((turn) => (
          <TurnStatus
            key={turn}
            turnId={turn}
            selector={selector}
            refresh={messages.refresh}
          />
        ))}
      </div>
      <form
        className="chat-composer domain-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!text.trim() && !attachments.length) return;
          const parts: Part[] = [
            ...(text.trim() ? [{ kind: "text", text }] : []),
            ...attachments.map((item) => ({
              kind: item.media.mime_type.startsWith("image/")
                ? "image"
                : item.media.mime_type.startsWith("audio/")
                  ? "audio"
                  : item.media.mime_type.startsWith("video/")
                    ? "video"
                    : "file",
              media: item.media,
            })),
          ];
          void send
            .run("conversation/send", { ...selector, parts })
            .then((result) => {
              if (!result?.data) return;
              setTurns((current) =>
                [...new Set([...current, result.data.turn_id])].slice(-8),
              );
              setText("");
              setAttachments([]);
              setCursor(null);
              void messages.refresh();
            });
        }}
      >
        <label>
          说点什么
          <textarea
            value={text}
            onChange={(event) => setText(event.target.value)}
            maxLength={100000}
            placeholder="连续发来的消息会在短暂停顿后一起回应。"
          />
        </label>
        <div className="domain-grid">
          {attachments.map((item) => (
            <div key={item.artifact.artifact_id}>
              <MediaPreview media={item.media} />
              <button
                className="text-link"
                type="button"
                onClick={() =>
                  setAttachments((current) =>
                    current.filter(
                      (value) =>
                        value.artifact.artifact_id !==
                        item.artifact.artifact_id,
                    ),
                  )
                }
              >
                移除此附件
              </button>
            </div>
          ))}
        </div>
        <label>
          图片、音视频或文件
          <input
            type="file"
            disabled={uploading}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (!file || !session?.authenticated) return;
              setUploading(true);
              setUploadError("");
              void uploadMedia(
                file,
                selector,
                session.csrf,
                new AbortController().signal,
              )
                .then((uploaded) =>
                  setAttachments((current) => [...current, uploaded]),
                )
                .catch((cause) =>
                  setUploadError(
                    cause instanceof Error ? cause.message : "上传未确认",
                  ),
                )
                .finally(() => setUploading(false));
            }}
          />
        </label>
        {uploadError && <p role="alert">{uploadError}</p>}
        <button
          className="button primary"
          disabled={
            send.busy || uploading || (!text.trim() && !attachments.length)
          }
        >
          发送
        </button>
        <CommandFeedback {...send} />
      </form>
    </div>
  );
}

function TurnStatus({
  turnId,
  selector,
  refresh,
}: {
  turnId: string;
  selector: DomainTarget;
  refresh: () => Promise<void>;
}) {
  const view = useDomainResource<Turn>(
    "conversation/turn",
    { ...selector, turn_id: turnId },
    true,
    1500,
    (turn) => active.has(turn.state),
  );
  const observeLearning = !!view.data && !active.has(view.data.state) && needsLearningRefresh(view.data.input_status);
  useEffect(() => {
    if (!observeLearning) return;
    const timer = window.setInterval(() => {
      if (!document.hidden) void view.refresh();
    }, 15000);
    return () => clearInterval(timer);
  }, [observeLearning, view.refresh]);
  const action = useDomainCommand(turnId);
  const reason = view.data?.error_code ?? view.data?.receipt?.error_code;
  return (
    <div className="chat-turn-status">
      <p>
        {view.data
          ? (labels[view.data.state] ?? view.data.state)
          : "正在读取本轮状态…"}
      </p>
      {view.error && <p role="alert">{view.error}</p>}
      {view.data?.missing.length ? (
        <p className="muted">本轮缺少：{view.data.missing.join("、")}</p>
      ) : null}
      {view.data && <details><summary>本轮材料与用量</summary>
        <ContextSelection selection={view.data.context_selection} tokenUsage={view.data.token_usage} providerUsage={view.data.provider_usage} />
      </details>}
      {view.data && <DialogueFlowEvidence flow={view.data.flow} />}
      {view.data && <InputStatusEvidence items={view.data.input_status} scope={selector} />}
      {reason && <p role="alert">{turnErrors[reason] ?? reason}</p>}
      {view.data && active.has(view.data.state) && (
        <button
          className="text-link"
          disabled={action.busy}
          onClick={() =>
            void action
              .run(
                "conversation/cancel",
                { ...selector, turn_id: turnId },
                view.data!.revision,
              )
              .then(() => {
                void view.refresh();
                void refresh();
              })
          }
        >
          停止本轮
        </button>
      )}
      {view.data?.state === "unknown" && view.data.expression_id && (
        <button
          className="text-link"
          onClick={() => {
            const query = new URLSearchParams({ expression: view.data!.expression_id! });
            for (const [key, value] of Object.entries(selector)) {
              if (typeof value === "string") query.set(key, value);
            }
            window.location.hash = `#/system/tasks?${query}`;
          }}
        >
          查看原表达
        </button>
      )}
      <CommandFeedback {...action} />
    </div>
  );
}

function PublicParts({ parts }: { parts: Part[] }) {
  return (
    <>
      {parts.map((part, index) => {
        if (part.media) return <MediaPreview key={index} media={part.media} />;
        if (part.kind === "text")
          return (
            <p key={index} style={{ whiteSpace: "pre-wrap" }}>
              {part.text}
            </p>
          );
        if (part.kind === "mention")
          return <span key={index}>@{part.user_id ?? "成员"}</span>;
        if (part.kind === "mention_all")
          return <span key={index}>@全体成员</span>;
        if (part.kind === "forward" && part.parts)
          return (
            <details key={index}>
              <summary>合并转发</summary>
              <PublicParts parts={part.parts} />
            </details>
          );
        if (part.kind === "channel_segment" && part.segment)
          return (
            <details key={index}>
              <summary>
                {part.channel} · {part.segment.type}
              </summary>
              <pre>{readableRecord(part.segment.data)}</pre>
            </details>
          );
        return (
          <span key={index} className="muted">
            {(
              {
                reply: "引用回复",
                forward: "合并转发",
                reference: "引用材料",
                channel_segment: "渠道消息段",
              } as Record<string, string>
            )[part.kind] ?? part.kind}
            {part.text ? ` · ${part.text}` : ""}
          </span>
        );
      })}
    </>
  );
}
