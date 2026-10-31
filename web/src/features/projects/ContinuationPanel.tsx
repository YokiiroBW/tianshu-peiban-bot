import { useState } from "react";
import {
  useDomainCommand,
  useDomainResource,
  type DomainTarget,
} from "../../api/useDomainResource";
import { SourceEvidence } from "../../components/SourceEvidence";
import { CommandFeedback } from "../../components/CommandFeedback";
import { EvidencePicker } from "../knowledge/EvidencePicker";
import {
  confirmed,
  lines,
  type Project,
  type ReadUnitRef,
} from "../knowledge/types";
import type {
  Checkout,
  ContinuationCheck,
  ContinuationPack,
  DeclaredState,
  ProjectState,
} from "./types";

export function ContinuationPanel({
  project,
  scope,
  canWrite,
}: {
  project: Project;
  scope: DomainTarget;
  canWrite: boolean;
}) {
  const [worktree, setWorktree] = useState("");
  const [budget, setBudget] = useState(32768);
  const [requested, setRequested] = useState<{
    worktree: string;
    budget: number;
    sequence: number;
  } | null>(null);
  const [actual, setActual] = useState<Checkout | null>(null);
  const [edit, setEdit] = useState(false);
  const state = useDomainResource<ProjectState>("projects/state", {
    ...scope,
    project_id: project.project_id,
  });
  return (
    <section className="panel">
      <h2>项目续作与实际工作目录</h2>
      <p className="muted">
        仅在主动读取时观察已登记 checkout
        的分支、HEAD、改动摘要、最多16个文件与8个提交。不会执行资料中的命令或修改项目文件。
      </p>
      {project.worktrees.length ? (
        <form
          className="knowledge-actions"
          onSubmit={(event) => {
            event.preventDefault();
            setActual(null);
            setRequested((before) => ({
              worktree,
              budget,
              sequence: (before?.sequence ?? 0) + 1,
            }));
          }}
        >
          <label>
            已登记工作目录
            <select
              required
              value={worktree}
              onChange={(event) => {
                setWorktree(event.target.value);
                setActual(null);
              }}
            >
              <option value="">请选择</option>
              {project.worktrees.map((value) => (
                <option key={value.id} value={value.id}>
                  {value.id} · 登记分支 {value.branch}
                </option>
              ))}
            </select>
          </label>
          <label>
            续作包字节预算
            <input
              type="number"
              min={4096}
              max={262144}
              step={1024}
              value={budget}
              onChange={(event) => setBudget(Number(event.target.value))}
            />
          </label>
          <button className="button primary" disabled={!worktree}>
            主动读取此工作目录
          </button>
        </form>
      ) : (
        <p>
          此项目尚未登记受管工作目录。管理员可在项目登记中填写实际 checkout
          与允许读取的文件。
        </p>
      )}
      {requested && (
        <ContinuationResult
          key={requested.sequence}
          projectId={project.project_id}
          scope={scope}
          worktree={requested.worktree}
          budget={requested.budget}
          onObserved={setActual}
        />
      )}
      <div className="section-heading">
        <h3>人工声明的项目进度</h3>
        {canWrite && (
          <button className="button" onClick={() => setEdit(!edit)}>
            {edit ? "收起编辑" : "更新项目进度"}
          </button>
        )}
      </div>
      {state.error && <p role="alert">{state.error}</p>}
      {state.data?.state ? (
        <StateBody value={state.data.state} scope={scope} />
      ) : (
        state.data && (
          <p>
            当前没有有效进度声明。
            {state.data.reason && `状态：${state.data.reason}`}
          </p>
        )
      )}
      {edit && state.data && (
        <StateEditor
          key={state.data.revision}
          project={project}
          scope={scope}
          current={state.data.state}
          revision={state.data.revision}
          actual={actual?.id === worktree ? actual : null}
          onSaved={async () => {
            await state.refresh();
            setEdit(false);
          }}
        />
      )}
    </section>
  );
}

function ContinuationResult({
  projectId,
  scope,
  worktree,
  budget,
  onObserved,
}: {
  projectId: string;
  scope: DomainTarget;
  worktree: string;
  budget: number;
  onObserved: (value: Checkout) => void;
}) {
  const snapshot = useDomainResource<ContinuationPack>(
    "projects/continuation/recover",
    {
      ...scope,
      project_id: projectId,
      worktree_id: worktree,
      budget_bytes: budget,
    },
  );
  const [checkRequested, setCheckRequested] = useState(false);
  return (
    <div className="experience-snapshot">
      {snapshot.loading && <p>正在对登记目录做有界读取…</p>}
      {snapshot.error && <p role="alert">{snapshot.error}</p>}
      {snapshot.data && (
        <>
          <CheckoutBody value={snapshot.data.payload.checkout} />
          <p className="muted">
            包 {snapshot.data.pack_id} · 使用 {snapshot.data.budget.used_bytes}/
            {snapshot.data.budget.limit_bytes} 字节 · 约{" "}
            {Math.ceil(snapshot.data.expires_in / 60)} 分钟有效
          </p>
          <div className="experience-results">
            <section className="experience-card">
              <h4>已登记资料版本</h4>
              <p className="muted">以下为登记状态，未统一复读全部原件。</p>
              <ul>
                {snapshot.data.payload.index.items.map((row) => (
                  <li key={row.document_id}>
                    {row.locator} · v{row.version}
                  </li>
                ))}
              </ul>
              {!snapshot.data.payload.index.coverage.complete && (
                <p>
                  目录还有未列范围：
                  {snapshot.data.payload.index.coverage.missing.join("、")}
                </p>
              )}
            </section>
            <section className="experience-card">
              <h4>当前可引用的研究材料</h4>
              <ul>
                {snapshot.data.payload.notes.items.map((row) => (
                  <li key={row.note_id}>{row.payload.question}</li>
                ))}
                {snapshot.data.payload.lessons.items.map((row) => (
                  <li key={row.lesson_id}>
                    {row.payload.trigger} · v{row.version}
                  </li>
                ))}
              </ul>
              {[
                ...snapshot.data.payload.notes.coverage.missing,
                ...snapshot.data.payload.lessons.coverage.missing,
                ...snapshot.data.payload.omissions,
              ].length > 0 && (
                <p className="muted">
                  省略：
                  {[
                    ...new Set([
                      ...snapshot.data.payload.notes.coverage.missing,
                      ...snapshot.data.payload.lessons.coverage.missing,
                      ...snapshot.data.payload.omissions,
                    ]),
                  ].join("、")}
                </p>
              )}
            </section>
          </div>
          {snapshot.data.payload.declared_state && (
            <section className="experience-card">
              <h4>读取当时的人工进度声明</h4>
              <StateBody
                value={snapshot.data.payload.declared_state}
                scope={scope}
              />
            </section>
          )}
          <SourceEvidence sources={snapshot.data.source_refs} scope={scope} />
          <div className="knowledge-actions">
            <button
              className="button"
              onClick={() => onObserved(snapshot.data!.payload.checkout)}
            >
              将本次HEAD用于进度验证声明
            </button>
            <button className="button" onClick={() => setCheckRequested(true)}>
              按原包核对当前状态
            </button>
          </div>
          {checkRequested && (
            <CheckResult
              projectId={projectId}
              scope={scope}
              pack={snapshot.data}
            />
          )}
        </>
      )}
    </div>
  );
}

function CheckoutBody({ value }: { value: Checkout }) {
  return (
    <section className="experience-card">
      <h3>实际观察：{value.id}</h3>
      <p className="muted">{value.collected_at}</p>
      <dl>
        <dt>实际分支</dt>
        <dd>
          {value.branch ?? "分离HEAD"}
          {value.branch_matches
            ? " · 与登记分支一致"
            : ` · 登记分支为${value.expected_branch}`}
        </dd>
        <dt>实际HEAD</dt>
        <dd>
          <code>{value.head ?? "尚无提交"}</code>
        </dd>
        <dt>工作目录</dt>
        <dd>
          {value.dirty ? "有未提交修改" : "观察时无未提交修改"}
          {value.unborn ? " · 尚无初始提交" : ""}
        </dd>
      </dl>
      <h4>登记文件与资料版本的实际对照</h4>
      <div className="knowledge-table">
        <table>
          <thead>
            <tr>
              <th>文件</th>
              <th>实际状态</th>
              <th>大小</th>
              <th>登记版本</th>
              <th>新鲜度</th>
            </tr>
          </thead>
          <tbody>
            {value.files.map((file) => (
              <tr key={file.locator}>
                <td>{file.locator}</td>
                <td>{file.state}</td>
                <td>{file.size ?? "未知"}</td>
                <td>
                  {file.index
                    ? `v${file.index.version} · ${file.index.state}`
                    : "未登记"}
                </td>
                <td>{file.freshness}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h4>实际最近提交</h4>
      <ol>
        {value.history.map((commit) => (
          <li key={commit.commit}>
            <code>{commit.commit.slice(0, 12)}</code> {commit.subject}
            <small> · {commit.committed_at}</small>
          </li>
        ))}
      </ol>
      <details>
        <summary>改动摘要与完整性</summary>
        <pre>
          {JSON.stringify(
            { counts: value.counts, changes: value.changes },
            null,
            2,
          )}
        </pre>
      </details>
    </section>
  );
}

function CheckResult({
  projectId,
  scope,
  pack,
}: {
  projectId: string;
  scope: DomainTarget;
  pack: ContinuationPack;
}) {
  const check = useDomainResource<ContinuationCheck>(
    "projects/continuation/check",
    {
      ...scope,
      project_id: projectId,
      pack_id: pack.pack_id,
      sha256: pack.sha256,
    },
  );
  return (
    <div role="status">
      {check.error && <p role="alert">{check.error}</p>}
      {check.loading && <p>正在核对原续作包…</p>}
      {check.data && (
        <>
          <p>
            {check.data.valid
              ? "核对时原包仍有效"
              : `原包已有差异：${check.data.differences.join("、")}`}
          </p>
          <p className="muted">
            核对观察于 {check.data.actual_checkout.collected_at}
            。没有生成新包或重放项目操作。
          </p>
          <button className="button" onClick={() => void check.refresh()}>
            再次核对原包
          </button>
        </>
      )}
    </div>
  );
}

function StateBody({
  value,
  scope,
}: {
  value: DeclaredState;
  scope: DomainTarget;
}) {
  return (
    <>
      <p className="muted">
        人工声明 · 修订 {value.revision} · 不代表在此执行过验证
      </p>
      <h4>{value.payload.goal}</h4>
      <dl>
        <dt>约束</dt>
        <dd>{value.payload.constraints.join("；") || "未记录"}</dd>
        <dt>未完成</dt>
        <dd>{value.payload.unfinished.join("；") || "未记录"}</dd>
        <dt>决定</dt>
        <dd>{value.payload.decisions.join("；") || "未记录"}</dd>
      </dl>
      {value.payload.verifications.map((item, index) => (
        <p key={index}>
          {item.declaration.label} · 声明结果 {item.declaration.status} ·
          绑定工作目录 {item.declaration.worktree_id} /{" "}
          <code>{item.actual_binding.head}</code> · 绑定时
          {item.actual_binding.dirty ? "有修改" : "无修改"}
        </p>
      ))}
      <SourceEvidence sources={value.source_refs} scope={scope} />
    </>
  );
}

function StateEditor({
  project,
  scope,
  current,
  revision,
  actual,
  onSaved,
}: {
  project: Project;
  scope: DomainTarget;
  current: DeclaredState | null;
  revision: number;
  actual: Checkout | null;
  onSaved: () => Promise<void>;
}) {
  const [draft, setDraft] = useState({
    goal: current?.payload.goal ?? "",
    constraints: current?.payload.constraints.join("\n") ?? "",
    unfinished: current?.payload.unfinished.join("\n") ?? "",
    decisions: current?.payload.decisions.join("\n") ?? "",
    verification_label: "",
    verification_status: "not_run",
  });
  const [recordVerification, setRecordVerification] = useState(false);
  const [evidence, setEvidence] = useState<ReadUnitRef[]>([]);
  const command = useDomainCommand(
    `project-state:${project.project_id}`,
    "actor",
  );
  return (
    <form
      className="knowledge-form knowledge-editor"
      onSubmit={async (event) => {
        event.preventDefault();
        const verifications =
          recordVerification && actual
            ? [
                {
                  label: draft.verification_label,
                  status: draft.verification_status,
                  worktree_id: actual.id,
                  head: actual.head,
                },
              ]
            : [];
        if (
          confirmed(
            await command.run(
              "projects/state/save",
              {
                ...scope,
                project_id: project.project_id,
                state: {
                  goal: draft.goal,
                  constraints: lines(draft.constraints),
                  unfinished: lines(draft.unfinished),
                  decisions: lines(draft.decisions),
                  verifications,
                },
                evidence,
              },
              revision,
            ),
          )
        )
          await onSaved();
      }}
    >
      <label>
        当前目标
        <textarea
          required
          maxLength={2000}
          value={draft.goal}
          onChange={(event) => setDraft({ ...draft, goal: event.target.value })}
        />
      </label>
      {(
        [
          ["constraints", "约束"],
          ["unfinished", "未完成"],
          ["decisions", "决定"],
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
      <EvidencePicker
        projectId={project.project_id}
        scope={scope}
        values={evidence}
        onChange={setEvidence}
      />
      <label>
        <input
          type="checkbox"
          checked={recordVerification}
          disabled={!actual?.head}
          onChange={(event) => setRecordVerification(event.target.checked)}
        />
        附一项与本次实际HEAD绑定的人工验证声明
      </label>
      {!actual?.head && (
        <p className="muted">
          先主动读取工作目录，点击使用本次HEAD，才可绑定验证声明。
        </p>
      )}
      {recordVerification && actual && (
        <>
          <p>
            绑定 {actual.id} / <code>{actual.head}</code>
            。保存时再次核对HEAD；原有验证声明不自动复用到新修订。
          </p>
          <label>
            验证内容
            <input
              required
              maxLength={1000}
              value={draft.verification_label}
              onChange={(event) =>
                setDraft({ ...draft, verification_label: event.target.value })
              }
            />
          </label>
          <label>
            实际由你执行的结果
            <select
              value={draft.verification_status}
              onChange={(event) =>
                setDraft({ ...draft, verification_status: event.target.value })
              }
            >
              <option value="not_run">未执行</option>
              <option value="passed">已执行，通过</option>
              <option value="failed">已执行，失败</option>
              <option value="partial">仅部分执行</option>
            </select>
          </label>
        </>
      )}
      <button
        className="button primary"
        disabled={command.busy || (recordVerification && !actual?.head)}
      >
        保存进度修订
      </button>
      <CommandFeedback {...command} />
    </form>
  );
}
