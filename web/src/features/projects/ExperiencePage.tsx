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
import "./experience.css";

type Checkout = { id: string; label: string };
type Project = { project_id: string; label: string; checkouts?: Checkout[] };
type Connection = {
  available: boolean;
  code: string;
  projects: Project[];
  peer: { configured: boolean; verified_at: string | null; code: string };
};
type Lesson = {
  lesson_id: string;
  version: number;
  trigger: string;
  symptom: string;
  cause: string;
  correction: string;
  verification: string;
  scope: { platform: string; language: string; framework: string };
  evidence: unknown[];
};
type Experience = {
  entry_id: string;
  version: number;
  title: string;
  rule: string;
  applicability: string[];
  excludes: string[];
  counterexamples: string[];
  recheck_after: string | null;
  evidence: unknown[] | "protected";
};
type SearchResult = {
  lessons?: Lesson[];
  entries?: Experience[];
  omissions: string[];
  retrieval: string;
  trust: string;
};
type Continuation = {
  handle: string;
  expires_in: number;
  status: string;
  checkout: {
    id: string;
    branch: string | null;
    head: string | null;
    dirty: boolean | null;
    collected_at: string;
  };
  index: { total: number; listed: number; truncated: boolean };
  state: {
    version: number;
    current: boolean;
    stale_evidence: unknown[];
    goal: string;
    constraints: string[];
    unfinished: string[];
  } | null;
  omissions: string[];
  budget: { limit_bytes: number; used_bytes: number; over_budget: boolean };
  revision: number;
};
type Check = {
  valid: boolean;
  reason: string;
  differences: string[];
  observed: boolean;
  checkout_id: string;
  checked_at: string;
};
type Envelope<T> = { project_id: string; operation: string; result: T };
type Tab = "lessons" | "experiences" | "continuation";

function omissionText(items: string[]) {
  return items.length
    ? `本次读取省略：${items.join("、")}。没有显示的条目不能视为不存在。`
    : "本次读取没有报告省略；结果仍只匹配所输入的关键词。";
}

function connectionRail(state: Connection) {
  if (state.peer.verified_at && state.peer.code === "ok")
    return { tone: "blue" as const, label: "最近有真实读取" };
  if (state.peer.code !== "unverified")
    return { tone: "red" as const, label: "最近读取失败" };
  return { tone: "yellow" as const, label: "已配置，尚未验证" };
}

function unavailableTitle(code: string) {
  if (code === "knowledge_read_required") return "当前账号未获项目知识读取权限";
  if (code === "knowledge_credential_missing") return "项目知识服务凭据缺失";
  return "项目知识尚未配置";
}

/** Search existing project lessons and read a registered checkout only on request. */
export default function ExperiencePage() {
  const { session } = useAuth();
  const csrf = session?.authenticated ? session.csrf : "";
  const [state, setState] = useState<Connection | null>(null);
  const [stateError, setStateError] = useState("");
  const [projectId, setProjectId] = useState("");
  const [tab, setTab] = useState<Tab>("lessons");
  const [text, setText] = useState("");
  const [searched, setSearched] = useState("");
  const [result, setResult] = useState<SearchResult | null>(null);
  const [checkoutId, setCheckoutId] = useState("");
  const [snapshot, setSnapshot] = useState<Continuation | null>(null);
  const [check, setCheck] = useState<Check | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);

  function start() {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    return controller;
  }

  async function loadState() {
    if (!csrf) return;
    const controller = start();
    setState(null);
    setStateError("");
    setResult(null);
    setSnapshot(null);
    setCheck(null);
    try {
      const next = await integrationPost<Connection>(
        "knowledge/state",
        {},
        csrf,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setState(next);
      setCheckoutId("");
      setProjectId((before) =>
        next.projects?.some((item) => item.project_id === before)
          ? before
          : (next.projects?.[0]?.project_id ?? ""),
      );
    } catch (cause) {
      if (!controller.signal.aborted) setStateError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  useEffect(() => {
    void loadState();
    return () => active.current?.abort();
  }, [csrf]);

  useEffect(() => {
    if (projectId) active.current?.abort();
    setResult(null);
    setSearched("");
    setSnapshot(null);
    setCheck(null);
    setError("");
    setBusy(false);
  }, [projectId, tab]);

  function changeScope(nextProjectId: string, nextTab: Tab) {
    if (nextProjectId === projectId && nextTab === tab) return;
    active.current?.abort();
    setResult(null);
    setSearched("");
    setSnapshot(null);
    setCheck(null);
    setError("");
    setBusy(false);
    if (nextProjectId !== projectId) setCheckoutId("");
    setProjectId(nextProjectId);
    setTab(nextTab);
  }

  function changeCheckout(next: string) {
    if (next === checkoutId) return;
    active.current?.abort();
    setSnapshot(null);
    setCheck(null);
    setError("");
    setBusy(false);
    setCheckoutId(next);
  }

  const project = state?.projects?.find(
    (item) => item.project_id === projectId,
  );
  const checkouts = project?.checkouts ?? [];
  const checkout = checkouts.find((item) => item.id === checkoutId);

  async function search(event: FormEvent) {
    event.preventDefault();
    const term = text.trim();
    if (!csrf || !project || !term || tab === "continuation") return;
    const controller = start();
    setResult(null);
    setSearched(term);
    try {
      const answer = await integrationPost<Envelope<SearchResult>>(
        `knowledge/${tab}`,
        { project_id: project.project_id, text: term, budget_bytes: 8192 },
        csrf,
        controller.signal,
      );
      if (!controller.signal.aborted) setResult(answer.result);
    } catch (cause) {
      if (!controller.signal.aborted) setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function recover(event: FormEvent) {
    event.preventDefault();
    const term = text.trim();
    if (!csrf || !project || !checkout || !term) return;
    const controller = start();
    setSnapshot(null);
    setCheck(null);
    try {
      const answer = await integrationPost<Envelope<Continuation>>(
        "knowledge/continuation",
        {
          project_id: project.project_id,
          checkout_id: checkout.id,
          text: term,
          budget_bytes: 16384,
        },
        csrf,
        controller.signal,
      );
      if (!controller.signal.aborted) setSnapshot(answer.result);
    } catch (cause) {
      if (!controller.signal.aborted) setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function checkCurrent() {
    if (!csrf || !project || !snapshot) return;
    const controller = start();
    setCheck(null);
    try {
      const answer = await integrationPost<Envelope<Check>>(
        "knowledge/continuation-check",
        { project_id: project.project_id, handle: snapshot.handle },
        csrf,
        controller.signal,
      );
      if (!controller.signal.aborted) setCheck(answer.result);
    } catch (cause) {
      if (controller.signal.aborted) return;
      if (
        cause instanceof IntegrationError &&
        cause.code === "continuation_handle_expired"
      )
        setSnapshot(null);
      setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  if (!csrf) return null;
  if (stateError)
    return (
      <StatePanel kind="error" title="项目经验连接读取失败">
        <p>{stateError}</p>
        <button className="button" onClick={() => void loadState()}>
          重新读取
        </button>
      </StatePanel>
    );
  if (!state)
    return (
      <StatePanel kind="loading" title="正在读取项目权限">
        <p>请稍候。</p>
      </StatePanel>
    );
  if (!state.available)
    return (
      <StatePanel
        kind={state.code.endsWith("_not_configured") ? "unconfigured" : "error"}
        title={unavailableTitle(state.code)}
      >
        <p>
          请由部署管理员核对项目知识服务与当前账号授权。状态码：{state.code}。
        </p>
      </StatePanel>
    );
  if (!state.projects.length)
    return (
      <StatePanel kind="empty" title="当前没有可浏览项目">
        <p>知识服务已配置，但当前账号没有获准的项目。</p>
      </StatePanel>
    );

  const rail =
    !error && (result || snapshot)
      ? { tone: "blue" as const, label: "本页本次读取成功" }
      : connectionRail(state);
  const rows = tab === "lessons" ? result?.lessons : result?.entries;
  return (
    <div className="experience-page">
      <StatusRail tone={rail.tone} label={rail.label}>
        <p>
          错题与经验只做关键词检索；交接只读取明确登记的
          checkout。成功读取的内容仍需核对来源与当前状态。
        </p>
      </StatusRail>
      <section className="panel">
        <div className="section-heading">
          <h2>项目经验与交接</h2>
          <button
            className="button"
            disabled={busy}
            onClick={() => void loadState()}
          >
            <RefreshCw aria-hidden="true" />
            刷新项目权限
          </button>
        </div>
        <label className="experience-project">
          项目
          <select
            value={projectId}
            onChange={(event) => changeScope(event.target.value, tab)}
          >
            {state.projects.map((item) => (
              <option key={item.project_id} value={item.project_id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <div className="experience-tabs" role="group" aria-label="经验读取类型">
          {(
            [
              ["lessons", "项目错题"],
              ["experiences", "已审阅经验"],
              ["continuation", "交接快照"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              aria-pressed={tab === key}
              onClick={() => changeScope(projectId, key)}
            >
              {label}
            </button>
          ))}
        </div>
      </section>

      {tab !== "continuation" ? (
        <section className="panel experience-search">
          <h2>{tab === "lessons" ? "检索项目错题" : "检索已审阅经验"}</h2>
          <p className="muted">
            输入关键词后读取匹配结果。空结果仅表示本次关键词未命中，不代表项目没有错题或经验。
          </p>
          <form onSubmit={(event) => void search(event)}>
            <label>
              检索关键词
              <input
                value={text}
                maxLength={1024}
                required
                onChange={(event) => setText(event.target.value)}
              />
            </label>
            <button className="button primary" disabled={busy || !text.trim()}>
              <Search aria-hidden="true" />
              搜索
            </button>
          </form>
          {error && (
            <StatePanel kind="error" title="本次检索未完成">
              <p>{error}</p>
            </StatePanel>
          )}
          {busy && (
            <StatePanel kind="loading" title="正在检索">
              <p>请稍候。</p>
            </StatePanel>
          )}
          {result && (
            <>
              <p className="muted">
                关键词“{searched}” · {omissionText(result.omissions)}
              </p>
              {rows?.length ? (
                <div className="experience-results">
                  {tab === "lessons"
                    ? result.lessons?.map((item) => (
                        <LessonCard key={item.lesson_id} item={item} />
                      ))
                    : result.entries?.map((item) => (
                        <ExperienceCard key={item.entry_id} item={item} />
                      ))}
                </div>
              ) : (
                <StatePanel kind="empty" title="本次关键词没有匹配条目">
                  <p>可换关键词重试；省略和来源状态以本次回答为准。</p>
                </StatePanel>
              )}
            </>
          )}
        </section>
      ) : (
        <section className="panel experience-continuation">
          <h2>主动读取交接快照</h2>
          <p className="muted">
            这会对已登记 checkout 做一次有界的 Git
            与文件只读观察。快照是观察当时的记录，核对按钮会再次检查当前差异。
          </p>
          {checkouts.length === 0 ? (
            <StatePanel
              kind="unconfigured"
              title="此项目未登记可读取的 checkout"
            >
              <p>请由部署管理员完成明确登记；网页不能输入本机路径。</p>
            </StatePanel>
          ) : (
            <form onSubmit={(event) => void recover(event)}>
              <label>
                已登记 checkout
                <select
                  value={checkoutId}
                  required
                  onChange={(event) => changeCheckout(event.target.value)}
                >
                  <option value="">请选择</option>
                  {checkouts.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                交接线索关键词
                <input
                  value={text}
                  maxLength={1024}
                  required
                  onChange={(event) => setText(event.target.value)}
                />
              </label>
              <button
                className="button primary"
                disabled={busy || !checkout || !text.trim()}
              >
                读取此 checkout 的交接
              </button>
            </form>
          )}
          {error && (
            <StatePanel kind="error" title="交接读取或核对未完成">
              <p>{error}</p>
            </StatePanel>
          )}
          {busy && (
            <StatePanel kind="loading" title="正在读取交接">
              <p>请稍候。</p>
            </StatePanel>
          )}
          {snapshot && (
            <ContinuationCard
              snapshot={snapshot}
              check={check}
              checking={busy}
              onCheck={() => void checkCurrent()}
            />
          )}
        </section>
      )}
    </div>
  );
}

function LessonCard({ item }: { item: Lesson }) {
  return (
    <article className="experience-card">
      <h3>{item.trigger}</h3>
      <p className="muted">
        版本 {item.version} · {item.scope.platform} / {item.scope.language} /{" "}
        {item.scope.framework} · {item.evidence.length} 项来源引用
      </p>
      <dl>
        <div>
          <dt>症状</dt>
          <dd>{item.symptom}</dd>
        </div>
        <div>
          <dt>原因</dt>
          <dd>{item.cause}</dd>
        </div>
        <div>
          <dt>修正</dt>
          <dd>{item.correction}</dd>
        </div>
        <div>
          <dt>验证</dt>
          <dd>{item.verification}</dd>
        </div>
      </dl>
    </article>
  );
}

function ExperienceCard({ item }: { item: Experience }) {
  return (
    <article className="experience-card">
      <h3>{item.title}</h3>
      <p className="muted">
        已审阅经验 · 版本 {item.version} · 来源
        {Array.isArray(item.evidence)
          ? `${item.evidence.length} 项引用`
          : "由服务端保护"}
      </p>
      <p>{item.rule}</p>
      {item.applicability.length > 0 && (
        <p>
          <strong>适用：</strong>
          {item.applicability.join("；")}
        </p>
      )}
      {item.excludes.length > 0 && (
        <p>
          <strong>排除：</strong>
          {item.excludes.join("；")}
        </p>
      )}
      {item.counterexamples.length > 0 && (
        <p>
          <strong>反例：</strong>
          {item.counterexamples.join("；")}
        </p>
      )}
      {item.recheck_after && (
        <p className="muted">建议复核时间：{item.recheck_after}</p>
      )}
    </article>
  );
}

function ContinuationCard({
  snapshot,
  check,
  checking,
  onCheck,
}: {
  snapshot: Continuation;
  check: Check | null;
  checking: boolean;
  onCheck: () => void;
}) {
  return (
    <article className="experience-card">
      <h3>交接快照 · {snapshot.checkout.id}</h3>
      <p className="muted">
        观察于 {snapshot.checkout.collected_at} · 分支{" "}
        {snapshot.checkout.branch ?? "未报告"} · 提交{" "}
        {snapshot.checkout.head?.slice(0, 12) ?? "未报告"} · 工作区
        {snapshot.checkout.dirty === null
          ? "状态未报告"
          : snapshot.checkout.dirty
            ? "有未提交修改"
            : "无未提交修改"}
      </p>
      <p>
        索引 {snapshot.index.listed}/{snapshot.index.total} 项
        {snapshot.index.truncated ? "（已截断）" : ""} · 项目修订{" "}
        {snapshot.revision}
      </p>
      <p className="muted">
        {omissionText(snapshot.omissions)}
        {snapshot.budget.over_budget ? " 已达到读取预算。" : ""}
      </p>
      {snapshot.state ? (
        <>
          <StatusRail
            tone={snapshot.state.current ? "yellow" : "red"}
            label={
              snapshot.state.current ? "声明在观察时未见过期" : "声明来源已过期"
            }
          >
            <p>
              这是版本 {snapshot.state.version}{" "}
              的项目声明；请点击下方按钮再次核对当前 checkout。
            </p>
          </StatusRail>
          <p>
            <strong>目标：</strong>
            {snapshot.state.goal}
          </p>
          {snapshot.state.constraints.length > 0 && (
            <p>
              <strong>约束：</strong>
              {snapshot.state.constraints.join("；")}
            </p>
          )}
          {snapshot.state.unfinished.length > 0 && (
            <p>
              <strong>未完成：</strong>
              {snapshot.state.unfinished.join("；")}
            </p>
          )}
          {snapshot.state.stale_evidence.length > 0 && (
            <p className="muted">
              {snapshot.state.stale_evidence.length} 项来源证据已过期。
            </p>
          )}
        </>
      ) : (
        <p className="muted">
          尚无已登记的项目交接声明；本次只观察到 checkout 与索引状态。
        </p>
      )}
      <p className="muted">
        核对句柄仅在当前登录会话保留约 {Math.ceil(snapshot.expires_in / 60)}
        分钟；到期后请重新主动读取。
      </p>
      <button
        className="button"
        type="button"
        disabled={checking}
        onClick={onCheck}
      >
        核对当前状态（只读一次）
      </button>
      {check && (
        <StatusRail
          tone={check.valid ? "blue" : "red"}
          label={check.valid ? "核对时通过" : "当前状态有差异或无法确认"}
        >
          <p>
            {check.checked_at} · {check.reason}
            {check.differences.length
              ? ` · 差异：${check.differences.join("、")}`
              : ""}
            {!check.observed ? " · 未完成现场观察" : ""}
          </p>
        </StatusRail>
      )}
    </article>
  );
}
