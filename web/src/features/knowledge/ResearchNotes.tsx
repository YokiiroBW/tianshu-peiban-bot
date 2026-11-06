import { useState } from "react";
import type { Page } from "../../api/contracts";
import {
  useDomainCommand,
  useDomainResource,
  type DomainTarget,
} from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";
import { SourceEvidence } from "../../components/SourceEvidence";
import { EvidencePicker, unitKey, unitLabel } from "./EvidencePicker";
import type { Note, ReadUnitRef, ResearchNote, Citation } from "./types";
import { confirmed, lines } from "./types";

export function ResearchNotes({
  projectId,
  scope,
  canWrite,
  initialUnits = [],
}: {
  projectId: string;
  scope: DomainTarget;
  canWrite: boolean;
  initialUnits?: ReadUnitRef[];
}) {
  const [cursor, setCursor] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [term, setTerm] = useState("");
  const [editing, setEditing] = useState<Note | "new" | null>(
    initialUnits.length ? "new" : null,
  );
  const [history, setHistory] = useState<Note | null>(null);
  const notes = useDomainResource<Page<Note>>("projects/notes", {
    ...scope,
    project_id: projectId,
    cursor,
    limit: 16,
    query: term,
  });
  async function reloadAfterWrite() {
    if (cursor) setCursor(null);
    else await notes.refresh();
  }
  return (
    <section className="panel">
      <div className="section-heading">
        <h2>研究笔记</h2>
        {canWrite && (
          <button className="button" onClick={() => setEditing("new")}>
            新建笔记
          </button>
        )}
      </div>
      <form
        className="knowledge-actions"
        onSubmit={(event) => {
          event.preventDefault();
          setCursor(null);
          setTerm(query);
        }}
      >
        <label>
          筛选本页笔记
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <button className="button">筛选</button>
        <button
          className="button"
          type="button"
          onClick={() => void notes.refresh()}
        >
          刷新
        </button>
      </form>
      <p className="muted">
        原件陈述、推断、待答问题和决定分别保留。引用会按当前原件与笔记版本递归复核。
      </p>
      {notes.error && <p role="alert">{notes.error}</p>}
      {notes.loading && <p>正在核对笔记与引用…</p>}
      <div className="knowledge-note-grid">
        {notes.data?.items.map((note) => (
          <article className="knowledge-note" key={note.note_id}>
            <NoteBody note={note} scope={scope} />
            <div className="knowledge-actions">
              <button className="button" onClick={() => setHistory(note)}>
                读取历史版本
              </button>
              {canWrite && (
                <button className="button" onClick={() => setEditing(note)}>
                  修订此笔记
                </button>
              )}
              {canWrite && note.state === "active" && (
                <WithdrawNote
                  projectId={projectId}
                  note={note}
                  scope={scope}
                  onSaved={reloadAfterWrite}
                />
              )}
            </div>
          </article>
        ))}
      </div>
      {notes.data && !notes.data.items.length && <p>本页没有匹配笔记。</p>}
      {notes.data?.coverage.missing.length ? (
        <p className="muted">
          未显示：{notes.data.coverage.missing.join("、")}
        </p>
      ) : null}
      <div className="knowledge-actions">
        {notes.data?.next_cursor && (
          <button
            className="button"
            onClick={() => setCursor(notes.data!.next_cursor)}
          >
            下一页
          </button>
        )}
        {cursor && (
          <button className="button" onClick={() => setCursor(null)}>
            第一页
          </button>
        )}
      </div>
      {editing && canWrite && (
        <NoteEditor
          key={
            editing === "new" ? "new" : editing.note_id + ":" + editing.version
          }
          projectId={projectId}
          scope={scope}
          note={editing === "new" ? null : editing}
          initialUnits={initialUnits}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            await reloadAfterWrite();
            setEditing(null);
          }}
        />
      )}
      {history && (
        <NoteHistory
          key={history.note_id}
          projectId={projectId}
          scope={scope}
          note={history}
          onClose={() => setHistory(null)}
        />
      )}
    </section>
  );
}

function NoteBody({ note, scope }: { note: Note; scope: DomainTarget }) {
  return (
    <>
      <h3>{note.payload.question}</h3>
      <p className="muted">
        版本 {note.version} · {note.state} ·{" "}
        {note.current
          ? "引用仍为当前版本"
          : `引用需复核：${note.citation_state}`}
      </p>
      <dl>
        <dt>原件陈述</dt>
        <dd>
          <ul>
            {note.payload.source_statements.map((value, index) => (
              <li key={index}>
                <strong>{value.kind}：</strong>
                {value.statement}
                <small> · {unitLabel(value.source)}</small>
              </li>
            ))}
          </ul>
        </dd>
        <dt>推断</dt>
        <dd>{note.payload.inferences.join("；") || "未记录"}</dd>
        <dt>待答问题</dt>
        <dd>{note.payload.open_questions.join("；") || "未记录"}</dd>
        <dt>决定</dt>
        <dd>
          {note.payload.decision?.summary ?? "尚无决定"}
          {note.payload.decision && (
            <p className="muted">
              依据：
              {note.payload.decision.basis
                .map((value) =>
                  value.kind === "unit"
                    ? unitLabel(value.reference)
                    : `关联研究记录 · 版本 ${value.reference.version}`,
                )
                .join("；")}
            </p>
          )}
        </dd>
      </dl>
      <SourceEvidence sources={note.source_refs} scope={scope} />
    </>
  );
}

function WithdrawNote({
  projectId,
  note,
  scope,
  onSaved,
}: {
  projectId: string;
  note: Note;
  scope: DomainTarget;
  onSaved: () => Promise<void>;
}) {
  const command = useDomainCommand(`note-withdraw:${note.note_id}`, "global");
  return (
    <span>
      <button
        className="button"
        disabled={command.busy}
        onClick={async () => {
          if (
            confirmed(
              await command.run(
                "projects/notes/withdraw",
                { ...scope, project_id: projectId, note_id: note.note_id },
                note.revision,
              ),
            )
          )
            await onSaved();
        }}
      >
        撤回此笔记
      </button>
      <CommandFeedback {...command} />
    </span>
  );
}

function NoteEditor({
  projectId,
  scope,
  note,
  initialUnits,
  onClose,
  onSaved,
}: {
  projectId: string;
  scope: DomainTarget;
  note: Note | null;
  initialUnits: ReadUnitRef[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const original = note?.payload;
  const [question, setQuestion] = useState(original?.question ?? "");
  const [statements, setStatements] = useState<
    ResearchNote["source_statements"]
  >(original?.source_statements ?? []);
  const [units, setUnits] = useState<ReadUnitRef[]>(() =>
    Array.from(
      new Map(
        [
          ...(original?.source_statements ?? []).map((value) => value.source),
          ...initialUnits,
        ].map((value) => [unitKey(value), value]),
      ).values(),
    ),
  );
  const [inferences, setInferences] = useState(
    original?.inferences.join("\n") ?? "",
  );
  const [questions, setQuestions] = useState(
    original?.open_questions.join("\n") ?? "",
  );
  const [decision, setDecision] = useState(original?.decision?.summary ?? "");
  const [basis, setBasis] = useState<Citation[]>(
    original?.decision?.basis ?? [],
  );
  const [selectedUnit, setSelectedUnit] = useState("");
  const [noteCursor, setNoteCursor] = useState<string | null>(null);
  const [selectedNote, setSelectedNote] = useState("");
  const existing = useDomainResource<Page<Note>>("projects/notes", {
    ...scope,
    project_id: projectId,
    limit: 16,
    cursor: noteCursor,
  });
  const command = useDomainCommand(
    `note-save:${projectId}:${note?.note_id ?? "new"}`,
    "global",
  );
  function addBasis(value: Citation) {
    if (!basis.some((item) => JSON.stringify(item) === JSON.stringify(value)))
      setBasis([...basis, value]);
  }
  return (
    <form
      className="knowledge-form knowledge-editor"
      onSubmit={async (event) => {
        event.preventDefault();
        const payload: ResearchNote = {
          question,
          source_statements: statements,
          inferences: lines(inferences),
          open_questions: lines(questions),
          decision: decision.trim() ? { summary: decision, basis } : null,
        };
        if (
          confirmed(
            await command.run(
              "projects/notes/save",
              {
                ...scope,
                project_id: projectId,
                ...(note ? { note_id: note.note_id } : {}),
                note: payload,
              },
              note?.revision ?? 0,
            ),
          )
        )
          await onSaved();
      }}
    >
      <div className="section-heading">
        <h3>{note ? "修订研究笔记" : "新建研究笔记"}</h3>
        <button type="button" className="button" onClick={onClose}>
          关闭编辑
        </button>
      </div>
      <label>
        研究问题
        <textarea
          required
          maxLength={2000}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
        />
      </label>
      <EvidencePicker
        projectId={projectId}
        scope={scope}
        values={units}
        onChange={setUnits}
      />
      <fieldset>
        <legend>原件陈述（至少一项）</legend>
        {statements.map((value, index) => (
          <div className="knowledge-statement" key={index}>
            <label>
              陈述类型
              <input
                required
                maxLength={64}
                value={value.kind}
                onChange={(event) =>
                  setStatements(
                    statements.map((item, i) =>
                      i === index
                        ? { ...item, kind: event.target.value }
                        : item,
                    ),
                  )
                }
              />
            </label>
            <label>
              原件支持的陈述
              <textarea
                required
                maxLength={2000}
                value={value.statement}
                onChange={(event) =>
                  setStatements(
                    statements.map((item, i) =>
                      i === index
                        ? { ...item, statement: event.target.value }
                        : item,
                    ),
                  )
                }
              />
            </label>
            <small>{unitLabel(value.source)}</small>
            <button
              type="button"
              className="button"
              onClick={() =>
                setStatements(statements.filter((_, i) => i !== index))
              }
            >
              移除此陈述
            </button>
          </div>
        ))}
        <div className="knowledge-actions">
          <select
            value={selectedUnit}
            onChange={(event) => setSelectedUnit(event.target.value)}
          >
            <option value="">选择已读单元</option>
            {units.map((value) => (
              <option key={unitKey(value)} value={unitKey(value)}>
                {unitLabel(value)}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="button"
            disabled={!selectedUnit || statements.length >= 16}
            onClick={() => {
              const unit = units.find(
                (value) => unitKey(value) === selectedUnit,
              );
              if (unit)
                setStatements([
                  ...statements,
                  { kind: "原件陈述", statement: "", source: unit },
                ]);
            }}
          >
            添加有依据的陈述
          </button>
        </div>
      </fieldset>
      <label>
        推断（每行一项）
        <textarea
          rows={3}
          value={inferences}
          onChange={(event) => setInferences(event.target.value)}
        />
      </label>
      <label>
        待答问题（每行一项）
        <textarea
          rows={3}
          value={questions}
          onChange={(event) => setQuestions(event.target.value)}
        />
      </label>
      <label>
        决定摘要（可留空）
        <textarea
          maxLength={2000}
          value={decision}
          onChange={(event) => setDecision(event.target.value)}
        />
      </label>
      {decision.trim() && (
        <fieldset>
          <legend>决定依据</legend>
          <ul>
            {basis.map((value, index) => (
              <li key={index}>
                {value.kind === "unit"
                  ? unitLabel(value.reference)
                  : `关联研究记录 · 版本 ${value.reference.version}`}
                <button
                  type="button"
                  className="button"
                  onClick={() => setBasis(basis.filter((_, i) => i !== index))}
                >
                  移除此依据
                </button>
              </li>
            ))}
          </ul>
          <div className="knowledge-actions">
            <button
              type="button"
              className="button"
              disabled={!selectedUnit}
              onClick={() => {
                const unit = units.find(
                  (value) => unitKey(value) === selectedUnit,
                );
                if (unit) addBasis({ kind: "unit", reference: unit });
              }}
            >
              加入所选原件单元
            </button>
            <select
              value={selectedNote}
              onChange={(event) => setSelectedNote(event.target.value)}
            >
              <option value="">选择本项目的当前笔记版本</option>
              {existing.data?.items
                .filter(
                  (value) => value.current && value.note_id !== note?.note_id,
                )
                .map((value) => (
                  <option key={value.note_id} value={value.note_id}>
                    {value.payload.question} · v{value.version}
                  </option>
                ))}
            </select>
            <button
              type="button"
              className="button"
              disabled={!selectedNote}
              onClick={() => {
                const selected = existing.data?.items.find(
                  (value) => value.note_id === selectedNote,
                );
                if (selected)
                  addBasis({ kind: "note", reference: selected.reference });
              }}
            >
              加入所选笔记
            </button>
            {existing.data?.next_cursor && (
              <button
                className="button"
                type="button"
                onClick={() => {
                  setNoteCursor(existing.data!.next_cursor);
                  setSelectedNote("");
                }}
              >
                下一页笔记
              </button>
            )}
            {noteCursor && (
              <button
                className="button"
                type="button"
                onClick={() => {
                  setNoteCursor(null);
                  setSelectedNote("");
                }}
              >
                笔记第一页
              </button>
            )}
          </div>
          {existing.error && <p role="alert">{existing.error}</p>}
        </fieldset>
      )}
      <button
        className="button primary"
        disabled={
          command.busy ||
          !statements.length ||
          (!!decision.trim() && !basis.length)
        }
      >
        保存版本
      </button>
      <CommandFeedback {...command} />
    </form>
  );
}

function NoteHistory({
  projectId,
  scope,
  note,
  onClose,
}: {
  projectId: string;
  scope: DomainTarget;
  note: Note;
  onClose: () => void;
}) {
  const [version, setVersion] = useState(note.version);
  const result = useDomainResource<Note>("projects/notes/get", {
    ...scope,
    project_id: projectId,
    note_id: note.note_id,
    version,
  });
  return (
    <section className="knowledge-editor">
      <div className="section-heading">
        <h3>历史版本</h3>
        <button className="button" onClick={onClose}>
          关闭历史
        </button>
      </div>
      <label>
        版本
        <input
          type="number"
          min={1}
          max={note.version}
          value={version}
          onChange={(event) => setVersion(Number(event.target.value))}
        />
      </label>
      {result.error && <p role="alert">{result.error}</p>}
      {result.data && <NoteBody note={result.data} scope={scope} />}
    </section>
  );
}
