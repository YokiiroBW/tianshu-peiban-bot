import { useState } from "react";
import type { Page } from "../../api/contracts";
import {
  useDomainCommand,
  useDomainResource,
  type DomainTarget,
} from "../../api/useDomainResource";
import { SourceEvidence } from "../../components/SourceEvidence";
import { CommandFeedback } from "../../components/CommandFeedback";
import { EvidencePicker, unitLabel } from "../knowledge/EvidencePicker";
import { confirmed, lines, type ReadUnitRef } from "../knowledge/types";
import type { Lesson, LessonContent } from "./types";

export function LessonPanel({
  projectId,
  scope,
  canWrite,
}: {
  projectId: string;
  scope: DomainTarget;
  canWrite: boolean;
}) {
  const [cursor, setCursor] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [edit, setEdit] = useState<Lesson | "new" | null>(null);
  const [history, setHistory] = useState<Lesson | null>(null);
  const lessons = useDomainResource<Page<Lesson>>("projects/lessons", {
    ...scope,
    project_id: projectId,
    cursor,
    query,
    limit: 16,
  });
  async function reloadAfterWrite() {
    if (cursor) setCursor(null);
    else await lessons.refresh();
  }
  return (
    <section className="panel">
      <div className="section-heading">
        <h2>项目错题与纠正</h2>
        {canWrite && (
          <button className="button" onClick={() => setEdit("new")}>
            记录项目经验
          </button>
        )}
      </div>
      <form
        className="knowledge-actions"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(draft);
          setCursor(null);
        }}
      >
        <label>
          筛选当前页
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
        </label>
        <button className="button">筛选</button>
        <button
          className="button"
          type="button"
          onClick={() => void lessons.refresh()}
        >
          刷新
        </button>
      </form>
      {lessons.loading && <p>正在读取并核对依据…</p>}
      {lessons.error && <p role="alert">{lessons.error}</p>}
      <div className="experience-results">
        {lessons.data?.items.map((lesson) => (
          <article className="experience-card" key={lesson.lesson_id}>
            <LessonBody lesson={lesson} scope={scope} />
            <div className="knowledge-actions">
              <button className="button" onClick={() => setHistory(lesson)}>
                读取历史版本
              </button>
              {canWrite && (
                <button className="button" onClick={() => setEdit(lesson)}>
                  修订
                </button>
              )}
              {canWrite && lesson.state === "active" && (
                <RetireLesson
                  lesson={lesson}
                  scope={scope}
                  onSaved={reloadAfterWrite}
                />
              )}
            </div>
          </article>
        ))}
      </div>
      {lessons.data && !lessons.data.items.length && <p>本页没有匹配经验。</p>}
      {lessons.data?.coverage.missing.length ? (
        <p className="muted">
          本次缺口：{lessons.data.coverage.missing.join("、")}
        </p>
      ) : null}
      <div className="knowledge-actions">
        {lessons.data?.next_cursor && (
          <button
            className="button"
            onClick={() => setCursor(lessons.data!.next_cursor)}
          >
            下一页经验
          </button>
        )}
        {cursor && (
          <button className="button" onClick={() => setCursor(null)}>
            第一页
          </button>
        )}
      </div>
      {edit && (
        <LessonEditor
          key={edit === "new" ? "new" : edit.lesson_id + ":" + edit.version}
          projectId={projectId}
          scope={scope}
          lesson={edit === "new" ? null : edit}
          onClose={() => setEdit(null)}
          onSaved={async () => {
            await reloadAfterWrite();
            setEdit(null);
          }}
        />
      )}
      {history && (
        <LessonHistory
          key={history.lesson_id}
          lesson={history}
          scope={scope}
          onClose={() => setHistory(null)}
        />
      )}
    </section>
  );
}

function LessonBody({
  lesson,
  scope,
}: {
  lesson: Lesson;
  scope: DomainTarget;
}) {
  const value = lesson.payload;
  return (
    <>
      <h3>{value.trigger}</h3>
      <p className="muted">
        版本 {lesson.version} · {lesson.state} ·{" "}
        {lesson.current
          ? "依据为当前版本"
          : `依据需复核：${lesson.citation_state}`}
      </p>
      <dl>
        {[
          ["症状", value.symptom],
          ["原因", value.cause],
          ["纠正", value.correction],
          ["实际验证记录", value.verification],
          [
            "环境",
            `${value.scope.platform} / ${value.scope.language} / ${value.scope.framework}`,
          ],
          ["适用范围", value.scope.applies_to.join("；")],
          ["排除条件", value.scope.excludes.join("；") || "未记录"],
        ].map(([label, text]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{text}</dd>
          </div>
        ))}
      </dl>
      <p className="muted">
        原件单元：{value.evidence.map(unitLabel).join("；")}
      </p>
      <SourceEvidence sources={lesson.source_refs} scope={scope} />
    </>
  );
}

function RetireLesson({
  lesson,
  scope,
  onSaved,
}: {
  lesson: Lesson;
  scope: DomainTarget;
  onSaved: () => Promise<void>;
}) {
  const command = useDomainCommand(
    `lesson-retire:${lesson.lesson_id}`,
    "global",
  );
  return (
    <span>
      <button
        className="button"
        disabled={command.busy}
        onClick={async () => {
          if (
            confirmed(
              await command.run(
                "projects/lessons/retire",
                {
                  ...scope,
                  project_id: lesson.project_id,
                  lesson_id: lesson.lesson_id,
                },
                lesson.revision,
              ),
            )
          )
            await onSaved();
        }}
      >
        停用此经验
      </button>
      <CommandFeedback {...command} />
    </span>
  );
}

function LessonEditor({
  projectId,
  scope,
  lesson,
  onClose,
  onSaved,
}: {
  projectId: string;
  scope: DomainTarget;
  lesson: Lesson | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [draft, setDraft] = useState(() => ({
    trigger: lesson?.payload.trigger ?? "",
    symptom: lesson?.payload.symptom ?? "",
    cause: lesson?.payload.cause ?? "",
    correction: lesson?.payload.correction ?? "",
    verification: lesson?.payload.verification ?? "",
    platform: lesson?.payload.scope.platform ?? "",
    language: lesson?.payload.scope.language ?? "",
    framework: lesson?.payload.scope.framework ?? "",
    applies: lesson?.payload.scope.applies_to.join("\n") ?? "",
    excludes: lesson?.payload.scope.excludes.join("\n") ?? "",
  }));
  const [evidence, setEvidence] = useState<ReadUnitRef[]>(
    lesson?.payload.evidence ?? [],
  );
  const command = useDomainCommand(
    `lesson-save:${projectId}:${lesson?.lesson_id ?? "new"}`,
    "global",
  );
  return (
    <form
      className="knowledge-form knowledge-editor"
      onSubmit={async (event) => {
        event.preventDefault();
        const payload: LessonContent = {
          trigger: draft.trigger,
          symptom: draft.symptom,
          cause: draft.cause,
          correction: draft.correction,
          verification: draft.verification,
          scope: {
            platform: draft.platform,
            language: draft.language,
            framework: draft.framework,
            applies_to: lines(draft.applies),
            excludes: lines(draft.excludes),
          },
          evidence,
        };
        if (
          confirmed(
            await command.run(
              "projects/lessons/save",
              {
                ...scope,
                project_id: projectId,
                ...(lesson ? { lesson_id: lesson.lesson_id } : {}),
                lesson: payload,
              },
              lesson?.revision ?? 0,
            ),
          )
        )
          await onSaved();
      }}
    >
      <div className="section-heading">
        <h3>{lesson ? "修订项目经验" : "记录项目经验"}</h3>
        <button className="button" type="button" onClick={onClose}>
          关闭编辑
        </button>
      </div>
      {(
        [
          ["trigger", "触发条件"],
          ["symptom", "症状"],
          ["cause", "原因"],
          ["correction", "纠正方法"],
          ["verification", "实际验证记录与未验证部分"],
        ] as const
      ).map(([key, label]) => (
        <label key={key}>
          {label}
          <textarea
            required
            maxLength={2000}
            value={draft[key]}
            onChange={(event) =>
              setDraft({ ...draft, [key]: event.target.value })
            }
          />
        </label>
      ))}
      <div className="knowledge-fields">
        {(
          [
            ["platform", "平台"],
            ["language", "语言"],
            ["framework", "框架"],
          ] as const
        ).map(([key, label]) => (
          <label key={key}>
            {label}
            <input
              required
              maxLength={128}
              value={draft[key]}
              onChange={(event) =>
                setDraft({ ...draft, [key]: event.target.value })
              }
            />
          </label>
        ))}
      </div>
      <label>
        适用范围（至少一项，每行一项）
        <textarea
          required
          value={draft.applies}
          onChange={(event) =>
            setDraft({ ...draft, applies: event.target.value })
          }
        />
      </label>
      <label>
        排除条件（每行一项）
        <textarea
          value={draft.excludes}
          onChange={(event) =>
            setDraft({ ...draft, excludes: event.target.value })
          }
        />
      </label>
      <EvidencePicker
        projectId={projectId}
        scope={scope}
        values={evidence}
        onChange={setEvidence}
      />
      <button
        className="button primary"
        disabled={command.busy || !evidence.length}
      >
        保存经验版本
      </button>
      <CommandFeedback {...command} />
    </form>
  );
}

function LessonHistory({
  lesson,
  scope,
  onClose,
}: {
  lesson: Lesson;
  scope: DomainTarget;
  onClose: () => void;
}) {
  const [version, setVersion] = useState(lesson.version);
  const read = useDomainResource<Lesson>("projects/lessons/get", {
    ...scope,
    project_id: lesson.project_id,
    lesson_id: lesson.lesson_id,
    version,
  });
  return (
    <section className="knowledge-editor">
      <div className="section-heading">
        <h3>经验历史</h3>
        <button className="button" onClick={onClose}>
          关闭历史
        </button>
      </div>
      <label>
        版本
        <input
          type="number"
          min={1}
          max={lesson.version}
          value={version}
          onChange={(event) => setVersion(Number(event.target.value))}
        />
      </label>
      {read.error && <p role="alert">{read.error}</p>}
      {read.data && <LessonBody lesson={read.data} scope={scope} />}
    </section>
  );
}
