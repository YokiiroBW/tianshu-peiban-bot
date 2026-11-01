import { useAuth } from "../../app/Auth";
import { requestId } from "../../app/requestId";
import { useState } from "react";
import {
  useDomainCommand,
  useDomainResource,
  type DomainTarget,
} from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import { SourceEvidence } from "../../components/SourceEvidence";
import { CommandFeedback } from "../../components/CommandFeedback";
import { useMemoryCursor } from "./pagination";
import { matterStates, time, type Matter } from "./types";

function MatterEditor({
  item,
  scope,
  saved,
  cancel,
  subjectId,
}: {
  item: Matter | null;
  scope: DomainTarget;
  saved: () => Promise<void>;
  cancel: () => void;
  subjectId?: string | null;
}) {
  const [id] = useState(() => item?.matter_id ?? requestId());
  const [title, setTitle] = useState(item?.title ?? "");
  const [summary, setSummary] = useState(item?.summary ?? "");
  const [state, setState] = useState(item?.state ?? "planned");
  const [due, setDue] = useState(
    item?.due_at
      ? new Date(
          new Date(item.due_at).getTime() -
            new Date().getTimezoneOffset() * 60000,
        )
          .toISOString()
          .slice(0, 16)
      : "",
  );
  const command = useDomainCommand<Matter>(`matter:${id}`, "actor");
  return (
    <form
      className="memory-edit"
      onSubmit={async (event) => {
        event.preventDefault();
        const result = await command.run(
          "memory/matters/save",
          {
            ...scope,
            matter_id: id,
            subject_id:
              item?.subject_id ??
              subjectId ??
              scope.person_id ??
              `actor:${scope.actor_id}`,
            title,
            summary,
            state,
            due_at: due ? new Date(due).toISOString() : null,
            claim_ids: item?.claim_ids ?? [],
            conditions: item?.conditions ?? {},
          },
          item?.revision ?? 0,
        );
        if (result) {
          await saved();
          cancel();
        }
      }}
    >
      <label>
        事项标题
        <input
          required
          maxLength={1000}
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
      </label>
      <label>
        状态
        <select
          value={state}
          onChange={(event) => setState(event.target.value)}
        >
          {Object.entries(matterStates).map(([key, name]) => (
            <option key={key} value={key}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <label>
        约定时间
        <input
          type="datetime-local"
          value={due}
          onChange={(event) => setDue(event.target.value)}
        />
      </label>
      <label>
        本次说明
        <textarea
          rows={3}
          value={summary}
          onChange={(event) => setSummary(event.target.value)}
        />
      </label>
      <div className="models-actions">
        <button className="button" disabled={command.busy}>
          保存这次明确确认
        </button>
        <button type="button" className="button" onClick={cancel}>
          取消
        </button>
      </div>
      <CommandFeedback {...command} />
    </form>
  );
}
export function Matters({
  scope,
  subjectId,
}: {
  scope: DomainTarget;
  subjectId?: string | null;
}) {
  const { session } = useAuth();
  const manageable = session?.principal?.permissions.some(
    (value) => value === "*" || value === "memory.manage",
  );
  const [editing, setEditing] = useState<Matter | "new" | null>(null);
  const cursor = useMemoryCursor({ ...scope, subjectId }, "matters");
  const view = useDomainResource<Page<Matter>>(
    "memory/matters",
    {
      ...scope,
      subject_id: subjectId ?? null,
      cursor: cursor.cursor,
      limit: 40,
    },
    !!scope.actor_id,
  );
  return (
    <section className="memory-review">
      <div className="section-heading">
        <h3>事项与尚未结束的约定</h3>
        {manageable && (
          <button className="button" onClick={() => setEditing("new")}>
            记下一件事
          </button>
        )}
      </div>
      {editing && (
        <MatterEditor
          key={editing === "new" ? "new" : editing.matter_id}
          item={editing === "new" ? null : editing}
          scope={scope}
          subjectId={subjectId}
          saved={view.refresh}
          cancel={() => setEditing(null)}
        />
      )}
      {view.error && <p role="alert">{view.error}</p>}
      {view.loading && <p>正在读取事项…</p>}
      <ul>
        {view.data?.items.map((item) => (
          <li key={item.matter_id}>
            <h4>{item.title}</h4>
            <p>
              {matterStates[item.state] ?? item.state} ·{" "}
              {item.due_at ? time(item.due_at) : "没有指定日期"}
              {item.interpretation ? " · 模型解释，未经本人确认完成" : ""}
            </p>
            <p>{item.summary}</p>
            <SourceEvidence sources={item.source_refs} scope={scope} />
            {manageable && (
              <button className="text-link" onClick={() => setEditing(item)}>
                更正进展或时间
              </button>
            )}
          </li>
        ))}
      </ul>
      {!view.loading && view.data?.items.length === 0 && (
        <p>本批没有可显示的事项。</p>
      )}
      <div className="models-actions">
        {cursor.previous && (
          <button className="button" onClick={cursor.back}>
            上一批
          </button>
        )}
        {view.data?.next_cursor && (
          <button
            className="button"
            onClick={() => cursor.next(view.data!.next_cursor!)}
          >
            下一批事项
          </button>
        )}
      </div>
    </section>
  );
}
