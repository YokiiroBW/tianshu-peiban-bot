import { useEffect, useRef, useState } from "react";
import { MessageCircle, RefreshCw } from "lucide-react";
import { integrationPost, readFailure } from "../../app/integrationApi";
import { type Person, type Archive, dateText } from "./types";
export function Interactions({
  person,
  csrf,
}: {
  person: Person;
  csrf: string;
}) {
  const [selected, setSelected] = useState(0);
  const [archive, setArchive] = useState<Archive | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);
  const source = person.sources[selected];
  async function read(cursor: string | null = null) {
    if (!source) return;
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    if (!cursor) setArchive(null);
    try {
      const answer = await integrationPost<Archive>(
        "bot-observation/archive",
        {
          id: source.connection.id,
          conversation_id: source.conversation,
          limit: 20,
          cursor,
        },
        csrf,
        controller.signal,
      );
      if (!controller.signal.aborted)
        setArchive((before) =>
          cursor && before
            ? {
                ...answer,
                items: [...before.items, ...answer.items],
                archive_items: [
                  ...before.archive_items,
                  ...answer.archive_items,
                ],
              }
            : answer,
        );
    } catch (cause) {
      if (!controller.signal.aborted) {
        setArchive(null);
        setError(readFailure(cause));
      }
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  useEffect(() => {
    void read();
    return () => active.current?.abort();
  }, [source?.connection.id, source?.conversation, csrf]);
  const messages =
    archive?.archive_items.filter((item) => item.author === person.qqId) ?? [];
  const pending =
    archive?.items.filter(
      (item) =>
        item.author === person.qqId && item.archive_state !== "archived",
    ) ?? [];
  return (
    <section aria-busy={busy}>
      <div className="people-content-heading">
        <div>
          <h3>
            <MessageCircle size={19} aria-hidden="true" />
            最近收到的消息
          </h3>
          <p>
            展示当前用户的可读入站归档，不代表机器人已回复，也不等于已形成画像。
          </p>
        </div>
        {source && (
          <button
            className="button"
            disabled={busy}
            onClick={() => void read()}
          >
            <RefreshCw size={15} aria-hidden="true" />
            刷新消息
          </button>
        )}
      </div>
      {person.sources.length > 0 && (
        <label className="people-field">
          会话来源
          <select
            aria-label="会话来源"
            value={selected}
            onChange={(event) => {
              active.current?.abort();
              setArchive(null);
              setError("");
              setSelected(Number(event.target.value));
            }}
          >
            {person.sources.map((item, index) => (
              <option
                key={`${item.connection.id}:${item.conversation}`}
                value={index}
              >
                {item.connection.name} ·{" "}
                {item.conversation.startsWith("private:")
                  ? "私聊"
                  : `群 ${item.conversation.slice(6)}`}
              </option>
            ))}
          </select>
        </label>
      )}
      {!source && (
        <p className="people-empty">当前已加载目录中没有可读的会话来源。</p>
      )}
      {error && (
        <p className="people-warning" role="alert">
          {error}
        </p>
      )}
      {busy && !archive && (
        <p role="status" className="people-empty">
          正在读取消息归档…
        </p>
      )}
      {archive && (
        <>
          <p className="people-footnote">
            归档状态：
            {archive.memory_state === "available" ||
            archive.memory_state === "ready"
              ? "可读取"
              : archive.memory_state}
          </p>
          {pending.length > 0 && (
            <p className="people-warning">
              当前页有 {pending.length}{" "}
              条消息尚未完成归档；收到消息和归档完成是不同状态。
            </p>
          )}
          <ol className="people-messages">
            {messages.map((item) => (
              <li key={item.source_ref}>
                <time>{dateText(item.sent_at)}</time>
                <p>
                  {item.content_state === "available" ||
                  item.content_state === "present" ||
                  item.content_state === "readable"
                    ? item.text
                    : item.text || `正文暂不可读（${item.content_state}）`}
                </p>
              </li>
            ))}
          </ol>
          {messages.length === 0 && (
            <p className="people-empty">
              当前归档页没有这位用户的可读消息。
              {archive.archive_next_cursor ? "可继续加载下一页。" : ""}
            </p>
          )}
          {archive.archive_next_cursor && (
            <button
              className="button"
              disabled={busy}
              onClick={() => void read(archive.archive_next_cursor)}
            >
              加载更多消息
            </button>
          )}
        </>
      )}
    </section>
  );
}
