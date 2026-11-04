import { useState } from "react";
import { ProjectRegistry } from "./ProjectRegistry";
import { useProjectWorkspace } from "./workspace";

export type ProjectWorkspace = ReturnType<typeof useProjectWorkspace>;
export function ProjectHeader({
  workspace,
  title,
}: {
  workspace: ProjectWorkspace;
  title: string;
}) {
  const [edit, setEdit] = useState<"new" | "current" | null>(null);
  const { view, detail, project, card } = workspace;
  return (
    <section className="panel knowledge-header">
      <div className="section-heading">
        <h2>{title}</h2>
        <button
          className="button"
          onClick={() => void Promise.all([view.refresh(), detail.refresh()])}
        >
          刷新项目
        </button>
      </div>
      {view.error && <p role="alert">{view.error}</p>}
      {view.loading && <p>正在读取受权项目…</p>}
      <div className="knowledge-actions">
        <label>
          项目
          <select
            value={card?.project_id ?? ""}
            onChange={(event) => {
              workspace.select(event.target.value);
              setEdit(null);
            }}
          >
            <option value="">请选择项目</option>
            {view.data?.items.map((row) => (
              <option key={row.project_id} value={row.project_id}>
                {row.label}
                {row.enabled ? "" : "（已停用）"}
              </option>
            ))}
          </select>
        </label>
        {view.data?.next_cursor && (
          <button
            className="button"
            onClick={() => workspace.next(view.data!.next_cursor)}
          >
            下一页项目
          </button>
        )}
        {workspace.cursor && (
          <button className="button" onClick={() => workspace.next(null)}>
            回到第一页
          </button>
        )}
        {view.data?.can_manage && (
          <>
            <button
              className="button"
              onClick={() => setEdit(edit === "new" ? null : "new")}
            >
              登记项目
            </button>
            {project && (
              <button
                className="button"
                onClick={() => setEdit(edit === "current" ? null : "current")}
              >
                编辑登记范围
              </button>
            )}
          </>
        )}
      </div>
      {detail.error && <p role="alert">{detail.error}</p>}
      {project && (
        <p className="muted">
          登记修订 {project.revision} · 内容修订 {project.content_revision} ·
          {project.conversation_id ? `所属会话 ${project.conversation_id}` : "当前工作区"}
        </p>
      )}
      {view.data && !view.data.items.length && (
        <p>当前范围没有已登记项目。管理员可登记受管目录。</p>
      )}
      {edit && (
        <ProjectRegistry
          key={
            edit === "new"
              ? "new"
              : project?.project_id + ":" + project?.revision
          }
          project={edit === "current" ? (project ?? null) : null}
          roots={view.data?.allowed_roots ?? []}
          onSaved={async () => {
            await Promise.all([view.refresh(), detail.refresh()]);
            setEdit(null);
          }}
        />
      )}
    </section>
  );
}
