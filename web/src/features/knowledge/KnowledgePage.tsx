import { useState } from "react";
import { BookShelf } from "../../components/BookShelf";
import { SourceEvidence } from "../../components/SourceEvidence";
import { useDomainResource } from "../../api/useDomainResource";
import type { Coverage, Page, SourceRef } from "../../api/contracts";
import { ProjectHeader, type ProjectWorkspace } from "./ProjectHeader";
import { useProjectWorkspace } from "./workspace";
import { DocumentReader } from "./DocumentReader";
import { ProjectImport } from "./ProjectImport";
import { ResearchNotes } from "./ResearchNotes";
import type { DocumentRow, ReadUnitRef } from "./types";
import "./knowledge.css";

/** Native project sources, typed research notes, and explicit imports. */
export default function KnowledgePage() {
  const workspace = useProjectWorkspace();
  const [tab, setTab] = useState("documents");
  const [evidence, setEvidence] = useState<{
    project: string;
    units: ReadUnitRef[];
  }>({ project: "", units: [] });
  return (
    <div className="knowledge-page">
      <ProjectHeader workspace={workspace} title="项目资料与研究" />
      {workspace.project && (
        <>
          <nav className="knowledge-tabs" aria-label="项目资料页面">
            {[
              ["documents", "资料目录"],
              ["query", "原件搜索"],
              ["notes", "研究笔记"],
            ].map(([id, label]) => (
              <button
                key={id}
                aria-current={tab === id ? "page" : undefined}
                onClick={() => setTab(id)}
              >
                {label}
              </button>
            ))}
          </nav>
          <div key={workspace.project.project_id}>
            {tab === "documents" ? (
              <Documents
                workspace={workspace}
                onEvidence={(value) => {
                  setEvidence({
                    project: workspace.project!.project_id,
                    units: [value],
                  });
                  setTab("notes");
                }}
              />
            ) : tab === "query" ? (
              <Query workspace={workspace} />
            ) : (
              <ResearchNotes
                projectId={workspace.project.project_id}
                scope={workspace.scene}
                canWrite={workspace.view.data?.can_write ?? false}
                initialUnits={
                  evidence.project === workspace.project.project_id
                    ? evidence.units
                    : []
                }
              />
            )}
          </div>
        </>
      )}
    </div>
  );
}

function Documents({
  workspace,
  onEvidence,
}: {
  workspace: ProjectWorkspace;
  onEvidence: (value: ReadUnitRef) => void;
}) {
  const project = workspace.project!;
  const [cursor, setCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<DocumentRow | null>(null);
  const docs = useDomainResource<Page<DocumentRow>>("projects/documents", {
    ...workspace.scene,
    project_id: project.project_id,
    cursor,
    limit: 32,
  });
  async function refreshed() {
    if (cursor) setCursor(null);
    await Promise.all([
      ...(cursor ? [] : [docs.refresh()]),
      workspace.detail.refresh(),
      workspace.view.refresh(),
    ]);
  }
  return (
    <>
      <section className="panel">
        <div className="section-heading">
          <h2>完整资料目录</h2>
          <button className="button" onClick={() => void docs.refresh()}>
            刷新目录
          </button>
        </div>
        <p className="muted">
          目录列出登记版本；打开原件时会实际复核文件或URL的新鲜度。没有读取的资料不显示为已理解。
        </p>
        {docs.loading && <p>正在读取资料目录…</p>}
        {docs.error && <p role="alert">{docs.error}</p>}
        <BookShelf
          entries={(docs.data?.items ?? []).map((row) => ({
            id: row.document_id,
            title: row.locator,
            subtitle: `${row.kind} · v${row.version}`,
            badge: row.state,
            category: row.source_validation,
            selected: row.document_id === selected?.document_id,
          }))}
          onOpen={(id) =>
            setSelected(
              docs.data?.items.find((row) => row.document_id === id) ?? null,
            )
          }
          empty="当前页没有资料。可显式预览已登记目录或导入一个原件。"
        />
        {docs.data?.coverage.missing.length ? (
          <p role="status">目录缺口：{docs.data.coverage.missing.join("、")}</p>
        ) : null}
        <div className="knowledge-actions">
          {docs.data?.next_cursor && (
            <button
              className="button"
              onClick={() => setCursor(docs.data!.next_cursor)}
            >
              下一页目录
            </button>
          )}
          {cursor && (
            <button className="button" onClick={() => setCursor(null)}>
              第一页
            </button>
          )}
        </div>
      </section>
      {workspace.view.data?.can_write && (
        <section className="panel">
          <h2>导入资料</h2>
          <ProjectImport
            key={project.project_id + ":" + project.revision}
            project={project}
            scope={workspace.scene}
            refreshed={refreshed}
          />
        </section>
      )}
      {selected && (
        <DocumentReader
          key={selected.document_id + ":" + selected.version}
          projectId={project.project_id}
          document={selected}
          scope={workspace.scene}
          onClose={() => setSelected(null)}
          onEvidence={onEvidence}
          canRemove={workspace.view.data?.can_write ?? false}
          onChanged={refreshed}
        />
      )}
    </>
  );
}

type QueryResult = {
  items: {
    document_id: string;
    version: number;
    sha256: string;
    locator: string;
    text: string;
    range: { unit: string; start: number; end: number };
    source_refs: SourceRef[];
  }[];
  next_cursor: string | null;
  coverage: Coverage;
  source_refs: SourceRef[];
  retrieval: string;
};
function Query({ workspace }: { workspace: ProjectWorkspace }) {
  const [draft, setDraft] = useState("");
  const [term, setTerm] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const query = useDomainResource<QueryResult>(
    "projects/query",
    {
      ...workspace.scene,
      project_id: workspace.project!.project_id,
      query: term,
      cursor,
      limit: 8,
    },
    !!term,
  );
  return (
    <section className="panel">
      <h2>在真实原件中搜索</h2>
      <p className="muted">
        每次有界读取原件文字并保留范围。长文继续分页；图片、音视频和PDF请从资料目录按单元读取。
      </p>
      <form
        className="knowledge-actions"
        onSubmit={(event) => {
          event.preventDefault();
          setCursor(null);
          setTerm(draft.trim());
        }}
      >
        <label>
          关键词
          <input
            required
            maxLength={500}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
        </label>
        <button className="button primary" disabled={query.loading}>
          搜索
        </button>
      </form>
      {query.error && <p role="alert">{query.error}</p>}
      {query.loading && <p>正在读取受权原件…</p>}
      {query.data && (
        <>
          <p className="muted">
            范围{query.data.coverage.complete ? "已完成" : "仍有缺口"} ·{" "}
            {query.data.coverage.missing.join("、") || "未报告缺口"}
          </p>
          {query.data.items.map((row) => (
            <article
              className="knowledge-block"
              key={row.document_id + ":" + row.range.start}
            >
              <h3>
                {row.locator} · v{row.version}
              </h3>
              <small>
                {row.range.unit} {row.range.start}–{row.range.end}
              </small>
              <p className="knowledge-source-text">{row.text}</p>
              <SourceEvidence
                sources={row.source_refs}
                scope={workspace.scene}
              />
            </article>
          ))}
          {!query.data.items.length && (
            <p>本次读取范围没有命中；不代表尚未读取的资料中不存在该内容。</p>
          )}
          <div className="knowledge-actions">
            {query.data.next_cursor && (
              <button
                className="button"
                onClick={() => setCursor(query.data!.next_cursor)}
              >
                继续搜索下一段范围
              </button>
            )}
            {cursor && (
              <button className="button" onClick={() => setCursor(null)}>
                从首段重新读取
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}
