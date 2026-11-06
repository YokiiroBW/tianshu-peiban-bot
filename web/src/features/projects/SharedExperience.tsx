import { useState } from "react";
import type { Page } from "../../api/contracts";
import {
  GLOBAL_SCOPE,
  useDomainCommand,
  useDomainResource,
  type DomainTarget,
} from "../../api/useDomainResource";
import { SourceEvidence } from "../../components/SourceEvidence";
import { CommandFeedback } from "../../components/CommandFeedback";
import type { ProjectsView } from "../knowledge/types";
import { confirmed, lines } from "../knowledge/types";
import type { Experience, Lesson, LessonRef } from "./types";

export function SharedExperience({
  canShare,
}: {
  canShare: boolean;
}) {
  const scope = GLOBAL_SCOPE;
  const [cursor, setCursor] = useState<string | null>(null);
  const [edit, setEdit] = useState<Experience | "new" | null>(null);
  const view = useDomainResource<Page<Experience>>("projects/experiences", {
    ...scope,
    cursor,
    limit: 16,
  });
  return (
    <section className="panel">
      <div className="section-heading">
        <h2>明确共享的经验摘要</h2>
        {canShare && (
          <button className="button" onClick={() => setEdit("new")}>
            从多项目经验形成共享摘要
          </button>
        )}
      </div>
      <p className="muted">
        摘要须由至少两个项目的当前经验支持并显式共享。共享摘要不公开私密原件或未获权的项目引用。
      </p>
      {view.error && <p role="alert">{view.error}</p>}
      {view.loading && <p>正在复核共享来源…</p>}
      <div className="experience-results">
        {view.data?.items.map((item) => (
          <article className="experience-card" key={item.experience_id}>
            <h3>{item.payload.title}</h3>
            <p className="muted">
              版本 {item.revision} · {item.observed_at}
            </p>
            <p className="knowledge-source-text">{item.payload.rule}</p>
            <dl>
              <dt>适用条件</dt>
              <dd>{item.payload.applicability.join("；") || "未记录"}</dd>
              <dt>排除条件</dt>
              <dd>{item.payload.excludes.join("；") || "未记录"}</dd>
              <dt>反例</dt>
              <dd>{item.payload.counterexamples.join("；") || "未记录"}</dd>
              <dt>建议复核时间</dt>
              <dd>{item.payload.recheck_after ?? "未指定"}</dd>
            </dl>
            <p className="muted">
              {item.evidence === "protected"
                ? "私密依据受保护"
                : `${item.evidence.length} 项版本化经验依据`}
            </p>
            <SourceEvidence sources={item.source_refs} scope={scope} />
            {canShare && item.can_withdraw && (
              <div className="knowledge-actions">
                {item.evidence !== "protected" && (
                  <button className="button" onClick={() => setEdit(item)}>
                    修订共享摘要
                  </button>
                )}
                <Withdraw item={item} scope={scope} onSaved={view.refresh} />
              </div>
            )}
          </article>
        ))}
      </div>
      {view.data && !view.data.items.length && (
        <p>当前范围没有可读共享摘要。</p>
      )}
      {view.data?.coverage.missing.length ? (
        <p className="muted">
          本次缺口：{view.data.coverage.missing.join("、")}
        </p>
      ) : null}
      <div className="knowledge-actions">
        {view.data?.next_cursor && (
          <button
            className="button"
            onClick={() => setCursor(view.data!.next_cursor)}
          >
            下一页摘要
          </button>
        )}
        {cursor && (
          <button className="button" onClick={() => setCursor(null)}>
            第一页
          </button>
        )}
      </div>
      {edit && (
        <ExperienceEditor
          key={
            edit === "new" ? "new" : edit.experience_id + ":" + edit.revision
          }
          item={edit === "new" ? null : edit}
          onClose={() => setEdit(null)}
          onSaved={async () => {
            await view.refresh();
            setEdit(null);
          }}
        />
      )}
    </section>
  );
}

function Withdraw({
  item,
  scope,
  onSaved,
}: {
  item: Experience;
  scope: DomainTarget;
  onSaved: () => Promise<void>;
}) {
  const command = useDomainCommand(
    `experience-withdraw:${item.experience_id}`,
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
                "projects/experiences/withdraw",
                { ...scope, experience_id: item.experience_id },
                item.revision,
              ),
            )
          )
            await onSaved();
        }}
      >
        撤回共享摘要
      </button>
      <CommandFeedback {...command} />
    </span>
  );
}

function ExperienceEditor({
  item,
  onClose,
  onSaved,
}: {
  item: Experience | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const scope = GLOBAL_SCOPE;
  const [draft, setDraft] = useState({
    title: item?.payload.title ?? "",
    rule: item?.payload.rule ?? "",
    applies: item?.payload.applicability.join("\n") ?? "",
    excludes: item?.payload.excludes.join("\n") ?? "",
    counterexamples: item?.payload.counterexamples.join("\n") ?? "",
    recheck: item?.payload.recheck_after ?? "",
  });
  const [evidence, setEvidence] = useState<LessonRef[]>(
    item && item.evidence !== "protected" ? item.evidence : [],
  );
  const command = useDomainCommand(
    `experience-promote:${item?.experience_id ?? "new"}`,
    "global",
  );
  const multipleProjects =
    new Set(evidence.map((value) => value.project_id)).size >= 2;
  return (
    <form
      className="knowledge-form knowledge-editor"
      onSubmit={async (event) => {
        event.preventDefault();
        if (
          confirmed(
            await command.run(
              "projects/experiences/promote",
              {
                ...scope,
                ...(item ? { experience_id: item.experience_id } : {}),
                entry: {
                  title: draft.title,
                  rule: draft.rule,
                  applicability: lines(draft.applies),
                  excludes: lines(draft.excludes),
                  counterexamples: lines(draft.counterexamples),
                  recheck_after: draft.recheck.trim() || null,
                  evidence,
                },
              },
              item?.revision ?? 0,
            ),
          )
        )
          await onSaved();
      }}
    >
      <div className="section-heading">
        <h3>{item ? "修订共享摘要" : "共享一份经验摘要"}</h3>
        <button type="button" className="button" onClick={onClose}>
          关闭编辑
        </button>
      </div>
      <label>
        标题
        <input
          required
          maxLength={200}
          value={draft.title}
          onChange={(event) =>
            setDraft({ ...draft, title: event.target.value })
          }
        />
      </label>
      <label>
        共享规则或方法
        <textarea
          required
          maxLength={2000}
          value={draft.rule}
          onChange={(event) => setDraft({ ...draft, rule: event.target.value })}
        />
      </label>
      {(
        [
          ["applies", "适用条件"],
          ["excludes", "排除条件"],
          ["counterexamples", "反例"],
        ] as const
      ).map(([key, label]) => (
        <label key={key}>
          {label}（每行一项）
          <textarea
            value={draft[key]}
            onChange={(event) =>
              setDraft({ ...draft, [key]: event.target.value })
            }
          />
        </label>
      ))}
      <label>
        建议复核时间（带时区，例如2026-11-30T12:00:00+08:00，可留空）
        <input
          value={draft.recheck}
          onChange={(event) =>
            setDraft({ ...draft, recheck: event.target.value })
          }
        />
      </label>
      <LessonEvidence values={evidence} onChange={setEvidence} />
      {!multipleProjects && <p>请选择至少两个不同项目的当前经验版本。</p>}
      <button
        className="button primary"
        disabled={command.busy || !multipleProjects}
      >
        保存并共享此摘要
      </button>
      <CommandFeedback {...command} />
    </form>
  );
}

function LessonEvidence({
  values,
  onChange,
}: {
  values: LessonRef[];
  onChange: (values: LessonRef[]) => void;
}) {
  const scope = GLOBAL_SCOPE;
  const [cursor, setCursor] = useState<string | null>(null);
  const [project, setProject] = useState("");
  const [lessonCursor, setLessonCursor] = useState<string | null>(null);
  const projects = useDomainResource<ProjectsView>("projects/view", {
    ...scope,
    cursor,
    limit: 40,
  });
  const selected = projects.data?.items.find(
    (row) => row.project_id === project,
  );
  const scene = scope;
  const lessons = useDomainResource<Page<Lesson>>(
    "projects/lessons",
    { ...scene, project_id: project, cursor: lessonCursor, limit: 16 },
    !!selected,
  );
  return (
    <fieldset>
      <legend>当前经验版本依据</legend>
      <ul>
        {values.map((value) => (
          <li key={JSON.stringify(value)}>
            关联项目经验 · 版本 {value.version}
            <button
              type="button"
              className="button"
              onClick={() =>
                onChange(
                  values.filter(
                    (item) => JSON.stringify(item) !== JSON.stringify(value),
                  ),
                )
              }
            >
              移除此依据
            </button>
          </li>
        ))}
      </ul>
      <label>
        选择受权项目
        <select
          value={project}
          onChange={(event) => {
            setProject(event.target.value);
            setLessonCursor(null);
          }}
        >
          <option value="">请选择</option>
          {projects.data?.items.map((row) => (
            <option key={row.project_id} value={row.project_id}>
              {row.label}
            </option>
          ))}
        </select>
      </label>
      <div className="knowledge-actions">
        {projects.data?.next_cursor && (
          <button
            type="button"
            className="button"
            onClick={() => {
              setCursor(projects.data!.next_cursor);
              setProject("");
            }}
          >
            下一页项目
          </button>
        )}
        {cursor && (
          <button
            type="button"
            className="button"
            onClick={() => {
              setCursor(null);
              setProject("");
            }}
          >
            项目第一页
          </button>
        )}
      </div>
      {projects.error && <p role="alert">{projects.error}</p>}
      {lessons.error && <p role="alert">{lessons.error}</p>}
      {lessons.data?.items.map((value) => (
        <div className="knowledge-statement" key={value.lesson_id}>
          <h4>
            {value.payload.trigger} · v{value.version}
          </h4>
          <p>{value.payload.correction}</p>
          <SourceEvidence sources={value.source_refs} scope={scene} />
          <button
            type="button"
            className="button"
            disabled={
              !value.current ||
              values.length >= 16 ||
              values.some(
                (item) =>
                  JSON.stringify(item) === JSON.stringify(value.reference),
              )
            }
            onClick={() => onChange([...values, value.reference])}
          >
            {value.current ? "加入此经验版本" : "此版本依据已失效"}
          </button>
        </div>
      ))}
      <div className="knowledge-actions">
        {lessons.data?.next_cursor && (
          <button
            type="button"
            className="button"
            onClick={() => setLessonCursor(lessons.data!.next_cursor)}
          >
            下一页经验
          </button>
        )}
        {lessonCursor && (
          <button
            type="button"
            className="button"
            onClick={() => setLessonCursor(null)}
          >
            经验第一页
          </button>
        )}
      </div>
    </fieldset>
  );
}
