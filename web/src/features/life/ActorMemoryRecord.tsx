import { useState } from "react";
import type { ManagedMemory, MemoryImpact, Episode } from "./actorMemoryTypes";
import {
  ActorSources,
  EpisodeEvidence,
  memoryLabel,
  memoryTime,
} from "./ActorMemoryEvidence";

export type MemoryCorrection = {
  text: string;
  importance: string;
  retention: string;
  interest?: { topic: string; stance: string };
};

export function ActorMemoryRecord({
  item,
  impact,
  episode,
  busy,
  onInspect,
  onCorrect,
  onForget,
}: {
  item: ManagedMemory;
  impact: MemoryImpact | null;
  episode: Episode | null;
  busy: boolean;
  onInspect: () => void;
  onCorrect: (value: MemoryCorrection) => void;
  onForget: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(item.text ?? "");
  const [importance, setImportance] = useState(item.importance ?? "normal");
  const [retention, setRetention] = useState(item.retention ?? "medium");
  const [topic, setTopic] = useState(item.interest?.topic ?? "");
  const [stance, setStance] = useState(item.interest?.stance ?? "curious");
  return (
    <article className="life-record">
      <header>
        <strong>
          {item.forgotten ? "已忘记的记录" : memoryLabel(item.kind ?? "记录")}
        </strong>
        <span className="status-pill">{memoryLabel(item.state)}</span>
      </header>
      <p>
        {item.text ??
          (item.forgotten ? "角色副本正文已擦除。" : "当前不可读取正文。")}
      </p>
      <p className="muted">
        记录于 {memoryTime(item.recorded_at)} · 版本 {item.ref.revision}
        {item.kind === "episode" && ` · 发生于 ${memoryTime(item.occurred_at)}`}
      </p>
      {item.expires_at && (
        <p className="muted">有效至 {memoryTime(item.expires_at)}</p>
      )}
      <ActorSources sources={item.source_refs} />
      {item.episode_refs.length > 0 && (
        <details className="life-evidence">
          <summary>经历依据（{item.episode_refs.length}）</summary>
          <ul>
            {item.episode_refs.map((ref) => (
              <li key={`${ref.episode_id}:${ref.expected_revision}`}>
                {ref.episode_id} · 版本 {ref.expected_revision}
              </li>
            ))}
          </ul>
        </details>
      )}
      {!item.forgotten && (
        <button className="button" disabled={busy} onClick={onInspect}>
          查看详情与更正范围
        </button>
      )}
      {episode && <EpisodeEvidence {...episode} />}
      {impact && (
        <div className="life-memory-impact">
          <p>
            更正：
            {impact.can_revise && item.editable
              ? "可更正此认识"
              : "此记录不可直接更正"}{" "}
            · 忘记：{impact.can_forget ? "可忘记" : "当前不可忘记"}
          </p>
          <details open>
            <summary>
              受影响的派生记录（{impact.dependent_refs.length}
              {impact.omitted ? "+" : ""}）
            </summary>
            <ul>
              {impact.dependent_refs.map((ref) => (
                <li key={`${ref.kind}:${ref.id}:${ref.revision}`}>
                  {ref.id} · 版本 {ref.revision}
                </li>
              ))}
            </ul>
            {impact.omitted && <p>仅展示部分依赖，忘记会处理完整依赖范围。</p>}
          </details>
          <p className="muted">
            忘记会擦除角色副本及其依赖认识。原件、用户聊天和既有备份不在此操作范围。
          </p>
          <div className="life-action-row">
            {impact.can_revise && item.editable && (
              <button
                className="button"
                disabled={busy}
                onClick={() => setEditing(!editing)}
              >
                更正认识
              </button>
            )}
            {impact.can_forget && (
              <button className="button" disabled={busy} onClick={onForget}>
                忘记此记忆及派生内容
              </button>
            )}
          </div>
          {editing && (
            <form
              className="life-form"
              onSubmit={(event) => {
                event.preventDefault();
                onCorrect({
                  text,
                  importance,
                  retention,
                  ...(topic ? { interest: { topic, stance } } : {}),
                });
              }}
            >
              <label>
                更正后的认识
                <textarea
                  value={text}
                  required
                  maxLength={4000}
                  onChange={(event) => setText(event.target.value)}
                />
              </label>
              <div className="life-form-row">
                <label>
                  重要性
                  <select
                    value={importance}
                    onChange={(event) =>
                      setImportance(event.target.value as typeof importance)
                    }
                  >
                    <option value="low">低</option>
                    <option value="normal">普通</option>
                    <option value="high">高</option>
                  </select>
                </label>
                <label>
                  保留时间
                  <select
                    value={retention}
                    onChange={(event) =>
                      setRetention(event.target.value as typeof retention)
                    }
                  >
                    <option value="short">短期</option>
                    <option value="medium">中期</option>
                    <option value="long">长期</option>
                  </select>
                </label>
              </div>
              {item.kind === "preference" && (
                <div className="life-form-row">
                  <label>
                    兴趣主题
                    <input
                      maxLength={160}
                      value={topic}
                      onChange={(event) => setTopic(event.target.value)}
                    />
                  </label>
                  <label>
                    角色态度
                    <select
                      value={stance}
                      onChange={(event) =>
                        setStance(event.target.value as typeof stance)
                      }
                    >
                      {["like", "dislike", "mixed", "curious"].map((value) => (
                        <option key={value} value={value}>
                          {memoryLabel(value)}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
              )}
              <button className="button primary" disabled={busy}>
                保存更正
              </button>
            </form>
          )}
        </div>
      )}
    </article>
  );
}
