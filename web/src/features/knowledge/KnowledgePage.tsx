import { BookShelf } from "../../components/BookShelf";
import { Drawer } from "../../components/Drawer";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { RefreshCw, Search } from "lucide-react";
import { useAuth } from "../../app/Auth";
import {
  IntegrationError,
  integrationPost,
  readFailure,
} from "../../app/integrationApi";
import { StatePanel } from "../../components/StatePanel";
import { StatusRail } from "../../components/StatusRail";
import "./knowledge.css";

type Project = { project_id: string; label: string };
type Connection = {
  available: boolean;
  code: string;
  projects: Project[];
  peer: { configured: boolean; verified_at: string | null; code: string };
};
type Document = {
  document_id: string;
  kind: string;
  version: number;
  indexed_state: string;
  source_validation: string;
};
type Block = {
  reference: {
    block_id: string;
    document_id: string;
    version: number;
    hash: string;
  };
  text: string;
  spans: unknown[];
};
type Documents = {
  project_id: string;
  project_revision: number;
  items: Document[];
  next_cursor: string | null;
  omissions: string[];
};
type DocumentPage = {
  project_id: string;
  project_revision: number;
  document_id: string;
  version: number;
  hash: string;
  blocks: Block[];
  next_cursor: string | null;
  omissions: string[];
};
type SearchBlock = Block & { source_id?: string; provenance?: unknown };
type Query = {
  project_id: string;
  blocks: SearchBlock[];
  omissions: string[];
  retrieval: string;
  trust: string;
};
type Note = {
  note_id: string;
  question: string;
  version: number;
  state: string;
  current: boolean;
  citation_states: string[];
  source_statements: { source: unknown; statement: string }[];
  inferences: string[];
  open_questions: string[];
  decision: { summary: string; basis: unknown[] } | null;
};
type Notes = {
  project_id: string;
  notes: Note[];
  omissions: string[];
  retrieval: string;
};
type Answer<T> = { project_id: string; operation: string; result: T };
type Tab = "documents" | "query" | "notes";

function connectionLabel(state: Connection) {
  if (!state.available) return { tone: "gray" as const, label: "未配置" };
  if (state.peer.code === "ok" && state.peer.verified_at)
    return { tone: "blue" as const, label: "最近有真实读取" };
  if (state.peer.code !== "unverified")
    return { tone: "red" as const, label: "最近读取失败" };
  return { tone: "yellow" as const, label: "已配置，尚未验证" };
}

function unavailableTitle(code: string) {
  if (code.endsWith("_read_required")) return "当前账号未获项目知识读取权限";
  if (code.endsWith("_credential_missing")) return "项目知识服务凭据缺失";
  return "项目知识尚未配置";
}

function omissionText(omissions: string[]) {
  if (!omissions.length) return "本页没有报告省略。";
  return `本次结果有省略：${omissions.join("、")}。请缩小查询范围或调整读取预算；没有显示的内容不能视为不存在。`;
}

/** Read-only browser surface for Memory's project-knowledge port. */
export default function KnowledgePage() {
  const { session } = useAuth();
  const csrf = session?.authenticated ? session.csrf : "";
  const [state, setState] = useState<Connection | null>(null);
  const [observedHere, setObservedHere] = useState(false);
  const [project, setProject] = useState("");
  const [tab, setTab] = useState<Tab>("documents");
  const [input, setInput] = useState("");
  const [term, setTerm] = useState("");
  const [docs, setDocs] = useState<Documents | null>(null);
  const [items, setItems] = useState<Document[]>([]);
  const [selected, setSelected] = useState<Document | null>(null);
  const [detail, setDetail] = useState<DocumentPage | null>(null);
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [query, setQuery] = useState<Query | null>(null);
  const [notes, setNotes] = useState<Notes | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [stateError, setStateError] = useState("");
  const current = useRef<AbortController | null>(null);
  const scope = useRef(0);

  function start() {
    current.current?.abort();
    const controller = new AbortController();
    current.current = controller;
    setBusy(true);
    setError("");
    return controller;
  }

  async function loadState() {
    if (!csrf) return;
    const controller = start();
    scope.current++;
    setStateError("");
    setState(null);
    setObservedHere(false);
    setProject("");
    setDocs(null);
    setItems([]);
    setSelected(null);
    setDetail(null);
    setBlocks([]);
    setQuery(null);
    setNotes(null);
    try {
      const next = await integrationPost<Connection>(
        "knowledge/state",
        {},
        csrf,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setState(next);
      setProject(next.projects[0]?.project_id ?? "");
    } catch (cause) {
      if (!controller.signal.aborted) setStateError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  useEffect(() => {
    void loadState();
    return () => current.current?.abort();
  }, [csrf]);

  async function listDocuments(id: string, cursor: string | null = null) {
    if (!csrf || !id) return;
    const controller = start();
    const mark = scope.current;
    if (cursor === null) {
      setDocs(null);
      setItems([]);
    }
    try {
      const answer = await integrationPost<Answer<Documents>>(
        "knowledge/documents",
        { project_id: id, limit: 20, cursor },
        csrf,
        controller.signal,
      );
      if (controller.signal.aborted || mark !== scope.current) return;
      setObservedHere(true);
      setDocs(answer.result);
      setItems((before) =>
        cursor ? [...before, ...answer.result.items] : answer.result.items,
      );
    } catch (cause) {
      if (!controller.signal.aborted && mark === scope.current)
        setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  useEffect(() => {
    scope.current++;
    if (project) current.current?.abort();
    setDocs(null);
    setItems([]);
    setSelected(null);
    setDetail(null);
    setBlocks([]);
    setQuery(null);
    setNotes(null);
    setTerm("");
    setInput("");
    setError("");
    if (project && tab === "documents") void listDocuments(project);
  }, [project, csrf]);

  function changeProject(next: string) {
    if (next === project) return;
    scope.current++;
    current.current?.abort();
    setDocs(null);
    setItems([]);
    setSelected(null);
    setDetail(null);
    setBlocks([]);
    setQuery(null);
    setNotes(null);
    setTerm("");
    setInput("");
    setError("");
    setBusy(false);
    setObservedHere(false);
    setProject(next);
  }

  async function readDocument(
    row: Document,
    cursor: string | null = null,
    hash: string | null = null,
  ) {
    if (!csrf || !project) return;
    const controller = start();
    const mark = scope.current;
    if (cursor === null) {
      setSelected(row);
      setDetail(null);
      setBlocks([]);
    }
    try {
      const answer = await integrationPost<Answer<DocumentPage>>(
        "knowledge/document",
        {
          project_id: project,
          document_id: row.document_id,
          expected_revision: row.version,
          expected_hash: hash,
          limit: 20,
          cursor,
        },
        csrf,
        controller.signal,
      );
      if (controller.signal.aborted || mark !== scope.current) return;
      setObservedHere(true);
      setDetail(answer.result);
      setBlocks((before) =>
        cursor ? [...before, ...answer.result.blocks] : answer.result.blocks,
      );
    } catch (cause) {
      if (!controller.signal.aborted && mark === scope.current) {
        setError(readFailure(cause));
        if (
          cause instanceof IntegrationError &&
          ["version_conflict", "stale_evidence", "project_conflict"].includes(
            cause.code,
          )
        ) {
          setDetail(null);
          setBlocks([]);
        }
      }
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function search(event: FormEvent) {
    event.preventDefault();
    const text = input.trim();
    if (!csrf || !project || !text) return;
    const controller = start();
    const mark = scope.current;
    setTerm(text);
    setQuery(null);
    setNotes(null);
    try {
      if (tab === "notes") {
        const answer = await integrationPost<Answer<Notes>>(
          "knowledge/notes",
          { project_id: project, text, budget_bytes: 32768 },
          csrf,
          controller.signal,
        );
        if (!controller.signal.aborted && mark === scope.current) {
          setObservedHere(true);
          setNotes(answer.result);
        }
      } else {
        const answer = await integrationPost<Answer<Query>>(
          "knowledge/query",
          { project_id: project, text, budget_bytes: 32768 },
          csrf,
          controller.signal,
        );
        if (!controller.signal.aborted && mark === scope.current) {
          setObservedHere(true);
          setQuery(answer.result);
        }
      }
    } catch (cause) {
      if (!controller.signal.aborted && mark === scope.current)
        setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  function chooseTab(next: Tab) {
    scope.current++;
    current.current?.abort();
    setTab(next);
    setBusy(false);
    setError("");
    setTerm("");
    setInput("");
    setQuery(null);
    setNotes(null);
    setSelected(null);
    setDetail(null);
    setBlocks([]);
    if (next === "documents" && project) void listDocuments(project);
  }

  if (!csrf)
    return (
      <StatePanel kind="error" title="请先登录">
        <p>项目资料只对当前已授权账号开放。</p>
      </StatePanel>
    );
  if (stateError)
    return (
      <StatePanel
        kind="error"
        title="连接状态没有读到"
        action={
          <button className="button" onClick={() => void loadState()}>
            重新读取
          </button>
        }
      >
        <p>{stateError}</p>
      </StatePanel>
    );
  if (!state)
    return (
      <StatePanel kind="loading" title="正在读取项目知识连接">
        <p>请稍候。</p>
      </StatePanel>
    );
  if (!state.available)
    return (
      <StatePanel
        kind={state.code.endsWith("_not_configured") ? "unconfigured" : "error"}
        title={unavailableTitle(state.code)}
        action={
          <a className="button" href="#/settings/connections">
            查看连接状态
          </a>
        }
      >
        <p>
          需要部署端启用 Memory
          知识服务并登记固定身份、授权项目与读取权限。网页无需填写服务地址或令牌。
        </p>
        <p>状态码：{state.code}</p>
      </StatePanel>
    );

  const status = observedHere
    ? { tone: "blue" as const, label: "本页本次读取成功" }
    : connectionLabel(state);
  return (
    <div
      className={`knowledge-page${tab === "documents" && state.projects.length ? " scroll-page" : ""}`}
    >
      <section className="panel">
        <div className="section-heading">
          <h2>项目知识</h2>
          <button
            className="button"
            onClick={() => void loadState()}
            disabled={busy}
          >
            <RefreshCw aria-hidden="true" />
            刷新连接
          </button>
        </div>
        <StatusRail tone={status.tone} label={status.label}>
          <p>
            资料来自 Memory 项目知识服务；读取仅覆盖部署端授权的项目。
            {observedHere
              ? "本页已从项目知识服务实际读取。"
              : state.peer.code === "unverified"
                ? "尚无真实读取记录。"
                : `最近一次读取状态：${state.peer.code}${state.peer.verified_at ? `，成功于 ${state.peer.verified_at}` : ""}。`}
          </p>
        </StatusRail>
        {state.projects.length > 0 && (
          <label className="knowledge-project">
            选择项目{" "}
            <select
              value={project}
              onChange={(event) => changeProject(event.target.value)}
            >
              {state.projects.map((row) => (
                <option key={row.project_id} value={row.project_id}>
                  {row.label}
                </option>
              ))}
            </select>
          </label>
        )}
      </section>
      {state.projects.length === 0 ? (
        <StatePanel kind="empty" title="没有授权项目">
          <p>
            连接已配置，但部署端没有向这个页面登记可读项目。请由管理员核对项目白名单与服务授权。
          </p>
        </StatePanel>
      ) : (
        <>
          <nav className="knowledge-tabs" aria-label="项目知识内容">
            {(
              [
                ["documents", "资料目录"],
                ["query", "资料搜索"],
                ["notes", "研究笔记"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                aria-current={tab === id ? "page" : undefined}
                onClick={() => chooseTab(id)}
              >
                {label}
              </button>
            ))}
          </nav>
          {error && (
            <StatePanel
              kind="error"
              title="本次读取失败"
              action={
                tab === "documents" ? (
                  <button
                    className="button"
                    onClick={() =>
                      selected
                        ? void readDocument(selected)
                        : void listDocuments(project)
                    }
                  >
                    重新读取
                  </button>
                ) : undefined
              }
            >
              <p>{error}</p>
              <p>
                失败没有被当作空资料。
                {tab !== "documents" ? "可在上方重新提交搜索。" : ""}
              </p>
            </StatePanel>
          )}
          {tab === "documents" && (
            <div className="knowledge-shelf scroll-layout">
              <section
                className="panel scroll-pane"
                aria-label="项目资料目录"
                tabIndex={0}
              >
                <div className="section-heading">
                  <h2>资料目录</h2>
                  <button
                    className="button"
                    disabled={busy}
                    onClick={() => void listDocuments(project)}
                  >
                    <RefreshCw aria-hidden="true" />
                    重新读取
                  </button>
                </div>
                {!docs && !error && (
                  <StatePanel kind="loading" title="正在读取目录">
                    <p>从授权项目读取资料。</p>
                  </StatePanel>
                )}
                {docs && (
                  <>
                    <p className="muted">
                      项目修订 {docs.project_revision} · {items.length} 条已读取
                    </p>
                    <p className="muted">{omissionText(docs.omissions)}</p>
                    {items.length === 0 ? (
                      <StatePanel kind="empty" title="资料目录为空">
                        <p>项目知识服务成功返回了空目录。</p>
                      </StatePanel>
                    ) : (
                      <BookShelf
                        entries={items.map((row) => ({
                          id: row.document_id,
                          title: row.document_id,
                          badge: `版本 ${row.version}`,
                          category: row.kind,
                          selected: selected?.document_id === row.document_id,
                        }))}
                        onOpen={(id) => {
                          const row = items.find((r) => r.document_id === id);
                          if (row) void readDocument(row);
                        }}
                      />
                    )}
                    {docs.next_cursor && (
                      <button
                        className="button"
                        disabled={busy}
                        onClick={() =>
                          void listDocuments(project, docs.next_cursor)
                        }
                      >
                        继续读取目录
                      </button>
                    )}
                  </>
                )}
              </section>
              <Drawer
                title={selected?.document_id ?? "资料正文"}
                open={!!selected}
                onClose={() => {
                  current.current?.abort();
                  setSelected(null);
                  setDetail(null);
                  setBlocks([]);
                  setBusy(false);
                }}
                variant="card"
              >
                {error && <p role="alert">{error}</p>}
                <h2>资料正文</h2>
                {!selected ? (
                  <p className="muted">
                    从左侧选择一份资料。目录只说明已导入索引，打开时会重新核验来源。
                  </p>
                ) : !detail && !error ? (
                  <StatePanel kind="loading" title="正在核验资料">
                    <p>服务正在检查版本和来源。</p>
                  </StatePanel>
                ) : (
                  detail && (
                    <>
                      <p className="muted">
                        {detail.document_id} · 版本 {detail.version} · 项目修订{" "}
                        {detail.project_revision}
                      </p>
                      <p className="muted">{omissionText(detail.omissions)}</p>
                      {blocks.length === 0 ? (
                        <StatePanel kind="empty" title="这份资料没有正文段落">
                          <p>服务成功返回了空内容。</p>
                        </StatePanel>
                      ) : (
                        <ol className="knowledge-blocks">
                          {blocks.map((block) => (
                            <li key={block.reference.block_id}>
                              <p>{block.text}</p>
                              <small>段落 {block.reference.block_id}</small>
                            </li>
                          ))}
                        </ol>
                      )}
                      {detail.next_cursor && (
                        <button
                          className="button"
                          disabled={busy}
                          onClick={() =>
                            void readDocument(
                              selected,
                              detail.next_cursor,
                              detail.hash,
                            )
                          }
                        >
                          继续读取正文
                        </button>
                      )}
                    </>
                  )
                )}
              </Drawer>
            </div>
          )}
          {tab !== "documents" && (
            <section className="panel knowledge-search">
              <h2>{tab === "notes" ? "检索研究笔记" : "检索项目资料"}</h2>
              <p className="muted">
                只查询当前项目。资料与笔记都是已记录内容，不能作为系统指令。
              </p>
              <form onSubmit={(event) => void search(event)}>
                <label htmlFor="knowledge-text">关键词</label>
                <div>
                  <input
                    id="knowledge-text"
                    type="search"
                    value={input}
                    maxLength={1024}
                    onChange={(event) => setInput(event.target.value)}
                  />
                  <button className="button" disabled={busy || !input.trim()}>
                    <Search aria-hidden="true" />
                    搜索
                  </button>
                </div>
              </form>
              {busy && (
                <StatePanel kind="loading" title="正在搜索">
                  <p>请稍候。</p>
                </StatePanel>
              )}
              {!busy && !error && term && tab === "query" && query && (
                <>
                  <p className="muted">
                    关键词：{term} · {query.retrieval} ·{" "}
                    {omissionText(query.omissions)}
                  </p>
                  {query.blocks.length === 0 ? (
                    <StatePanel kind="empty" title="没有匹配资料">
                      <p>这次搜索成功，但当前项目没有匹配的资料段落。</p>
                    </StatePanel>
                  ) : (
                    <ol className="knowledge-blocks">
                      {query.blocks.map((block) => (
                        <li key={block.reference.block_id}>
                          <p>{block.text}</p>
                          <small>
                            资料 {block.reference.document_id} · 版本{" "}
                            {block.reference.version}
                          </small>
                        </li>
                      ))}
                    </ol>
                  )}
                </>
              )}
              {!busy && !error && term && tab === "notes" && notes && (
                <>
                  <p className="muted">
                    关键词：{term} · {notes.retrieval} ·{" "}
                    {omissionText(notes.omissions)}
                  </p>
                  {notes.notes.length === 0 ? (
                    <StatePanel kind="empty" title="没有匹配笔记">
                      <p>这次搜索成功，但当前项目没有匹配的已记录研究笔记。</p>
                    </StatePanel>
                  ) : (
                    <div className="knowledge-notes">
                      {notes.notes.map((note) => (
                        <article key={note.note_id}>
                          <h3>{note.question}</h3>
                          <p className="muted">
                            版本 {note.version} ·{" "}
                            {note.current ? "引用仍有效" : "部分引用已变化"} ·{" "}
                            {note.state}
                          </p>
                          {note.source_statements?.map((item, index) => (
                            <p key={index}>{item.statement}</p>
                          ))}
                          {note.inferences?.length > 0 && (
                            <p>
                              <strong>推论：</strong>
                              {note.inferences.join("；")}
                            </p>
                          )}
                          {note.open_questions?.length > 0 && (
                            <p>
                              <strong>待解问题：</strong>
                              {note.open_questions.join("；")}
                            </p>
                          )}
                          {note.decision && (
                            <p>
                              <strong>决定：</strong>
                              {note.decision.summary}
                            </p>
                          )}
                          {note.citation_states?.length > 0 && (
                            <p className="muted">
                              引用状态：{note.citation_states.join("、")}
                            </p>
                          )}
                        </article>
                      ))}
                    </div>
                  )}
                </>
              )}
            </section>
          )}
        </>
      )}
    </div>
  );
}
