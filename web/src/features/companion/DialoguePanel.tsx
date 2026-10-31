import { useState } from "react";
import { useAuth } from "../../app/Auth";
import type { ConversationSelection } from "../../app/RoleWorkspace";
import { useDomainCommand, useDomainResource } from "../../api/useDomainResource";
import { uploadMedia, type UploadedMedia } from "../../api/uploads";
import type { ArtifactRef, Page, Receipt } from "../../api/contracts";
import { MediaPreview } from "../../components/MediaPreview";
import { CommandFeedback } from "../../components/CommandFeedback";

type Part = { kind: string; text?: string; media?: ArtifactRef["media"]; metadata?: Record<string, unknown> };
type Message = { id: number; speaker: string; parts: Part[]; created_at: string };
type Turn = { turn_id: string; state: string; revision: number; receipt: Receipt | null; missing: string[]; action_ids: string[]; expression_id: string | null };
const active = new Set(["collecting", "queued", "running", "waiting_external", "waiting_expression"]);
const labels: Record<string, string> = { collecting: "正在收集连续消息", queued: "等待回应", running: "正在回应", waiting_external: "正在等待原任务", waiting_expression: "正在准备公开回应", completed: "本轮完成", failed: "本轮未完成", unknown: "原结果尚未确认", cancelled: "已停止本轮等待，外部效果以回执为准" };

export function DialoguePanel({ actor, conversation }: { actor: string; conversation: ConversationSelection }) {
  const { session } = useAuth();
  const selector = { actor_id: actor, conversation_id: conversation.conversation_id, ...(conversation.person_id ? { person_id: conversation.person_id } : {}) };
  const [cursor, setCursor] = useState<string | null>(null);
  const messages = useDomainResource<Page<Message>>("conversation/messages", { ...selector, cursor, limit: 50 }, true, cursor ? 0 : 2500);
  const send = useDomainCommand<{ turn_id: string; input_id: string; state: string }>(`${actor}:${conversation.conversation_id}`);
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<UploadedMedia[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [turns, setTurns] = useState<string[]>([]);
  return <div className="dialogue-panel">
    {messages.error && <p className="chat-error" role="alert">{messages.error}</p>}
    <div className="chat-messages" role="log" aria-label="公开消息">{[...(messages.data?.items ?? [])].reverse().map((message) => <article key={message.id} className={`chat-message ${message.speaker}`}>
      <header><strong>{message.speaker === "assistant" ? "角色" : "你与会话中的发言者"}</strong><time dateTime={message.created_at}>{new Date(message.created_at).toLocaleString("zh-CN")}</time></header>
      <PublicParts parts={message.parts} />
    </article>)}{messages.data && !messages.data.items.length && <p className="muted">这里还没有可读的公开消息。</p>}</div>
    <div className="domain-actions">{messages.data?.next_cursor && <button className="button" onClick={() => setCursor(messages.data!.next_cursor)}>查看更早消息</button>}{cursor && <button className="button" onClick={() => setCursor(null)}>回到最新消息</button>}</div>
    <div aria-live="polite">{turns.map((turn) => <TurnStatus key={turn} turnId={turn} selector={selector} refresh={messages.refresh} />)}</div>
    <form className="chat-composer domain-form" onSubmit={(event) => {
      event.preventDefault(); if (!text.trim() && !attachments.length) return;
      const parts: Part[] = [...(text.trim() ? [{ kind: "text", text }] : []), ...attachments.map((item) => ({ kind: item.media.mime_type.startsWith("image/") ? "image" : item.media.mime_type.startsWith("audio/") ? "audio" : item.media.mime_type.startsWith("video/") ? "video" : "file", media: item.media }))];
      void send.run("conversation/send", { ...selector, parts }).then((result) => {
        if (!result?.data) return;
        setTurns((current) => [...new Set([...current, result.data.turn_id])].slice(-8));
        setText(""); setAttachments([]); setCursor(null); void messages.refresh();
      });
    }}><label>说点什么<textarea value={text} onChange={(event) => setText(event.target.value)} maxLength={100000} placeholder="连续发来的消息会在短暂停顿后一起回应。" /></label>
      <div className="domain-grid">{attachments.map((item) => <div key={item.artifact.artifact_id}><MediaPreview media={item.media} /><button className="text-link" type="button" onClick={() => setAttachments((current) => current.filter((value) => value.artifact.artifact_id !== item.artifact.artifact_id))}>移除此附件</button></div>)}</div>
      <label>图片、音视频或文件<input type="file" disabled={uploading} onChange={(event) => {
        const file = event.target.files?.[0]; if (!file || !session?.authenticated) return;
        setUploading(true); setUploadError("");
        void uploadMedia(file, selector, session.csrf, new AbortController().signal).then((uploaded) => setAttachments((current) => [...current, uploaded])).catch((cause) => setUploadError(cause instanceof Error ? cause.message : "上传未确认")).finally(() => setUploading(false));
      }} /></label>{uploadError && <p role="alert">{uploadError}</p>}
      <button className="button primary" disabled={send.busy || uploading || (!text.trim() && !attachments.length)}>发送</button><CommandFeedback {...send} />
    </form>
  </div>;
}

function TurnStatus({ turnId, selector, refresh }: { turnId: string; selector: object; refresh: () => Promise<void> }) {
  const view = useDomainResource<Turn>("conversation/turn", { ...selector, turn_id: turnId }, true, 1500, (turn) => active.has(turn.state));
  const action = useDomainCommand(turnId);
  return <div className="chat-turn-status"><p>{view.data ? labels[view.data.state] ?? view.data.state : "正在读取本轮状态…"}</p>{view.error && <p role="alert">{view.error}</p>}
    {view.data?.missing.length ? <p className="muted">本轮缺少：{view.data.missing.join("、")}</p> : null}
    {view.data?.receipt?.error_code && <p>{view.data.receipt.error_code}</p>}
    {view.data && active.has(view.data.state) && <button className="text-link" disabled={action.busy} onClick={() => void action.run("conversation/cancel", { ...selector, turn_id: turnId }, view.data!.revision).then(() => { void view.refresh(); void refresh(); })}>停止本轮</button>}
    {view.data?.state === "unknown" && view.data.expression_id && <button className="text-link" onClick={() => { window.location.hash = `#/system/tasks?expression=${encodeURIComponent(view.data!.expression_id!)}`; }}>查看原表达</button>}
    <CommandFeedback {...action} />
  </div>;
}

function PublicParts({ parts }: { parts: Part[] }) {
  return <>{parts.map((part, index) => part.media ? <MediaPreview key={index} media={part.media} /> : part.kind === "text" ? <p key={index} style={{ whiteSpace: "pre-wrap" }}>{part.text}</p> : <span key={index} className="muted">{({ mention: "提及成员", mention_all: "提及全体", reply: "引用回复", forward: "合并转发", reference: "引用材料", channel_segment: "渠道消息段" } as Record<string, string>)[part.kind] ?? part.kind}</span>)}</>;
}
