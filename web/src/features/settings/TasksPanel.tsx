import { useEffect, useState } from "react";
import { useAuth } from "../../app/Auth";
import {
  GLOBAL_SCOPE,
  useDomainCommand,
  useDomainResource,
  useDomainTarget,
  type DomainTarget,
} from "../../api/useDomainResource";
import { commandTarget } from "../../api/client";
import {
  recoverCommand,
  taskHref,
  type ActionView,
  type ExecutionObservation,
  type OriginalCommand,
} from "../../api/commandRecovery";
import type {
  ArtifactRef,
  MediaRef,
  Receipt,
  SourceRef,
} from "../../api/contracts";
import { ArtifactList, MediaPreview } from "../../components/MediaPreview";
import { SourceEvidence } from "../../components/SourceEvidence";
import { CommandFeedback } from "../../components/CommandFeedback";
import { DetailCard } from "../../components/DetailCard";
import {
  ExecutionStatus,
  executionLaneName,
} from "../../components/ExecutionStatus";
import "./tasks.css";

type Query = {
  kind: "action" | "model" | "expression" | "command";
  id: string;
  operation: string;
  capability_id?: string;
};
const states: Record<string, string> = {
  todo: "等待执行",
  doing: "正在执行",
  aborted: "已停止",
  pending: "等待执行",
  queued: "等待资源",
  running: "正在执行",
  succeeded: "执行成功",
  failed: "执行失败",
  cancelled: "已取消",
  unknown: "结果未知",
  confirmed: "效果已确认",
  none: "无外部效果",
  not_started: "尚未提交",
  submitted: "已提交",
  not_requested: "未请求投递",
  delivered: "已投递",
};
function label(value: string) {
  return states[value] ?? value;
}
function originalQuery(): Query {
  const query = new URLSearchParams(window.location.hash.split("?")[1] ?? "");
  return {
    kind: query.has("action")
      ? "action"
      : query.has("attempt")
        ? "model"
        : query.has("expression")
          ? "expression"
          : "command",
    id:
      query.get("action") ??
      query.get("attempt") ??
      query.get("expression") ??
      query.get("client_request_id") ??
      "",
    operation: query.get("operation") ?? "",
    capability_id: query.get("capability_id") ?? undefined,
  };
}
function originalScope(current: DomainTarget): DomainTarget {
  const query = new URLSearchParams(window.location.hash.split("?")[1] ?? "");
  if (
    ["actor_id", "person_id", "conversation_id"].some((key) => query.has(key))
  )
    return {
      actor_id: query.get("actor_id") || null,
      person_id: query.get("person_id") || null,
      conversation_id: query.get("conversation_id") || null,
    };
  return current;
}
export function TasksPanel() {
  const current = useDomainTarget();
  const [query, setQuery] = useState(originalQuery);
  const [scope, setScope] = useState<DomainTarget>(() =>
    originalScope(current),
  );
  useEffect(() => {
    const followLink = () => {
      setQuery(originalQuery());
      setScope(originalScope(current));
    };
    window.addEventListener("hashchange", followLink);
    return () => window.removeEventListener("hashchange", followLink);
  }, [current.actor_id, current.person_id, current.conversation_id]);
  return (
    <section className="tasks-page scroll-page">
      <header className="section-heading">
        <div>
          <h2>任务与真实回执</h2>
          <p>沿原操作身份核对结果。等待结束、执行效果与消息投递分别显示。</p>
        </div>
        <button
          className="button"
          onClick={() => {
            setQuery(originalQuery());
            setScope(originalScope(current));
          }}
        >
          读取链接中的原目标
        </button>
      </header>
      <form
        key={JSON.stringify({ query, scope })}
        className="domain-form panel"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          setQuery({
            kind: String(data.get("kind")) as Query["kind"],
            id: String(data.get("id") ?? "").trim(),
            operation: String(data.get("operation") ?? "").trim(),
            capability_id: String(data.get("capability_id") ?? "") || undefined,
          });
          setScope({
            actor_id: String(data.get("actor") ?? "") || null,
            person_id: String(data.get("person") ?? "") || null,
            conversation_id: String(data.get("conversation") ?? "") || null,
          });
        }}
      >
        <div className="domain-grid">
          <label>
            查询类型
            <select name="kind" defaultValue={query.kind}>
              <option value="action">能力动作 action</option>
              <option value="model">模型执行 attempt</option>
              <option value="expression">公开表达 expression</option>
              <option value="command">原命令 client_request_id</option>
            </select>
          </label>
          <label>
            原标识
            <input name="id" defaultValue={query.id} required />
          </label>
        </div>
        <details>
          <summary>原提交场景与命令信息</summary>
          <div className="domain-grid">
            <label>
              角色
              <input name="actor" defaultValue={scope.actor_id ?? ""} />
            </label>
            <label>
              人物
              <input name="person" defaultValue={scope.person_id ?? ""} />
            </label>
            <label>
              会话
              <input
                name="conversation"
                defaultValue={scope.conversation_id ?? ""}
              />
            </label>
            <label>
              原HTTP操作
              <input
                name="operation"
                defaultValue={query.operation}
                placeholder="例如 images/generate"
              />
            </label>
            <label>
              明确能力ID（capabilities/invoke时）
              <input
                name="capability_id"
                defaultValue={query.capability_id ?? ""}
              />
            </label>
          </div>
          <p className="muted">
            这里只选择服务器需核验的原场景，不能由此指定权限或受众。
          </p>
        </details>
        <button className="button primary">只读核对原记录</button>
      </form>
      {query.id &&
        (query.kind === "action" ? (
          <ActionTask
            key={query.id + JSON.stringify(scope)}
            id={query.id}
            scope={scope}
          />
        ) : query.kind === "model" ? (
          <ModelTask
            key={query.id + JSON.stringify(scope)}
            id={query.id}
            scope={scope}
          />
        ) : query.kind === "expression" ? (
          <ExpressionTask
            key={query.id + JSON.stringify(scope)}
            id={query.id}
            scope={scope}
          />
        ) : (
          <CommandLookup
            key={query.id + query.operation + JSON.stringify(scope)}
            value={{
              client_request_id: query.id,
              operation: query.operation,
              capability_id: query.capability_id,
              target: commandTarget(scope),
            }}
          />
        ))}
      <RuntimeTasks />
    </section>
  );
}
function ReceiptView({
  receipt,
  scope,
}: {
  receipt: Receipt;
  scope: DomainTarget;
}) {
  return (
    <section className="domain-record">
      <dl>
        <dt>原Action</dt>
        <dd>{receipt.action_id ?? "未知"}</dd>
        <dt>原Attempt</dt>
        <dd>{receipt.attempt_id ?? "未知"}</dd>
        <dt>执行</dt>
        <dd>{label(receipt.execution)}</dd>
        <dt>实际效果</dt>
        <dd>{label(receipt.effect)}</dd>
        <dt>消息投递</dt>
        <dd>{label(receipt.delivery)}</dd>
      </dl>
      {receipt.error_code && <p role="alert">{receipt.error_code}</p>}
      <ArtifactList artifacts={receipt.artifacts} scope={scope} />
      {receipt.coverage && !receipt.coverage.complete && (
        <p>覆盖缺口：{receipt.coverage.missing.join("、") || "范围未知"}</p>
      )}
      <details>
        <summary>实际业务结果</summary>
        <pre>{JSON.stringify(receipt.result, null, 2)}</pre>
      </details>
    </section>
  );
}
function ActionTask({ id, scope }: { id: string; scope: DomainTarget }) {
  const view = useDomainResource<ActionView>(
    "capabilities/get",
    { ...scope, action_id: id },
    true,
    5000,
    (value) => ["pending", "running"].includes(value.receipt?.execution ?? ""),
  );
  const control = useDomainCommand("original-action:" + id, "global");
  const row = view.data;
  const revision =
    typeof row?.action.revision === "number" ? row.action.revision : null;
  return (
    <section className="panel">
      <div className="section-heading">
        <h3>原能力动作</h3>
        <button className="button" onClick={() => void view.refresh()}>
          读取实际账本
        </button>
      </div>
      {view.error && <p role="alert">{view.error}</p>}
      {row && (
        <>
          <p>
            {String(row.action.capability_id)} · 结果修订 {row.result_revision}{" "}
            ·{" "}
            {row.result_observed_at
              ? new Date(row.result_observed_at).toLocaleString("zh-CN")
              : "尚无终态观测"}
          </p>
          {row.receipt ? (
            <ReceiptView receipt={row.receipt} scope={scope} />
          ) : (
            <p>原动作存在，当前尚无回执。</p>
          )}
          <ExecutionStatus
            execution={row.execution}
            state={row.receipt?.execution}
          />
          <SourceEvidence
            sources={(row.action.source_refs ?? []) as SourceRef[]}
            scope={scope}
          />
          <div className="domain-actions">
            {revision !== null && (
              <>
                <button
                  className="button"
                  disabled={control.busy}
                  onClick={() =>
                    void control
                      .run(
                        "capabilities/reconcile",
                        { ...scope, action_id: id },
                        revision,
                      )
                      .then(() => void view.refresh())
                  }
                >
                  核对原执行
                </button>
                {row.receipt &&
                  ["pending", "running", "unknown"].includes(
                    row.receipt.execution,
                  ) && (
                    <button
                      className="button"
                      disabled={control.busy}
                      onClick={() =>
                        void control
                          .run(
                            "capabilities/cancel",
                            { ...scope, action_id: id },
                            revision,
                          )
                          .then(() => void view.refresh())
                      }
                    >
                      请求取消后续步骤
                    </button>
                  )}
              </>
            )}
          </div>
          <CommandFeedback {...control} />
        </>
      )}
    </section>
  );
}
function CommandLookup({ value }: { value: OriginalCommand }) {
  const { session } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [answer, setAnswer] = useState<Awaited<
    ReturnType<typeof recoverCommand>
  > | null>(null);
  async function load() {
    if (!session || busy) return;
    setBusy(true);
    setError("");
    try {
      setAnswer(
        await recoverCommand(value, session.csrf, new AbortController().signal),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "原记录暂不可读。");
    } finally {
      setBusy(false);
    }
  }
  const receipt = answer?.action?.receipt;
  return (
    <section className="panel">
      <h3>原命令身份</h3>
      <p>{value.client_request_id}</p>
      <button
        className="button"
        disabled={busy || !value.operation}
        onClick={() => void load()}
      >
        {busy ? "正在读取…" : "只读查询原命令"}
      </button>
      {!value.operation && (
        <p className="muted">按client_request_id查询时请提供原HTTP操作。</p>
      )}
      {error && <p role="alert">{error}</p>}
      {answer &&
        (answer.found ? (
          <>
            <p>已找到原登记。登记本身不代表后续任务完成。</p>
            {answer.action && (
              <ExecutionStatus
                execution={answer.action.execution}
                state={receipt?.execution}
              />
            )}
            {receipt && (
              <ReceiptView
                receipt={receipt}
                scope={value.target ?? GLOBAL_SCOPE}
              />
            )}
            <dl>
              <dt>Action</dt>
              <dd>
                {receipt?.action_id ??
                  answer.command?.action_id ??
                  "未知或不适用"}
              </dd>
              <dt>Attempt</dt>
              <dd>
                {receipt?.attempt_id ??
                  answer.command?.attempt_id ??
                  "未知或不适用"}
              </dd>
              <dt>原命令修订</dt>
              <dd>{answer.command?.revision ?? "不适用"}</dd>
            </dl>
            {(receipt?.action_id ||
              answer.command?.action_id ||
              answer.command?.attempt_id) && (
              <a
                className="button"
                href={taskHref({
                  ...value,
                  action_id: receipt?.action_id ?? answer.command?.action_id,
                  attempt_id: receipt?.attempt_id ?? answer.command?.attempt_id,
                })}
              >
                打开原执行详情
              </a>
            )}
          </>
        ) : (
          <p>本次没有查到原登记；不会据此自动重发。</p>
        ))}
    </section>
  );
}
type ModelReceipt = {
  attempt_id: string;
  intent_id: string;
  status: string;
  dispatch: string;
  provider_id: string;
  model: string;
  protocol: string;
  quota_group: string;
  error_code: string | null;
  started_at: string | null;
  finished_at: string | null;
  usage: {
    input_tokens: number | null;
    output_tokens: number | null;
    cached_input_tokens: number | null;
    cache_write_tokens: number | null;
    metering: string;
  };
};
function ModelTask({ id, scope }: { id: string; scope: DomainTarget }) {
  const view = useDomainResource<{
    found: boolean;
    receipt: ModelReceipt | null;
    output_available: boolean;
  }>(
    "models/attempts/read",
    { ...scope, attempt_id: id },
    true,
    5000,
    (value) => ["queued", "running"].includes(value.receipt?.status ?? ""),
  );
  const row = view.data?.receipt;
  return (
    <section className="panel">
      <div className="section-heading">
        <h3>原模型执行</h3>
        <button className="button" onClick={() => void view.refresh()}>
          读取原回执
        </button>
      </div>
      {view.error && <p role="alert">{view.error}</p>}
      {view.data && !view.data.found && <p>当前场景没有查到这次原模型登记。</p>}
      {row && (
        <>
          <p>
            {row.model} · {row.provider_id} · {row.protocol}
          </p>
          <p>
            {label(row.status)} · {label(row.dispatch)} · 配额组{" "}
            {row.quota_group}
          </p>
          {row.error_code && <p role="alert">{row.error_code}</p>}
          <dl>
            {(
              [
                ["input_tokens", "输入token"],
                ["output_tokens", "输出token"],
                ["cached_input_tokens", "缓存命中输入"],
                ["cache_write_tokens", "缓存写入"],
              ] as const
            ).map(([key, title]) => (
              <div key={key}>
                <dt>{title}</dt>
                <dd>{row.usage[key] ?? "未知"}</dd>
              </div>
            ))}
          </dl>
          <p>
            计量来源：{row.usage.metering} · 原输出当前
            {view.data!.output_available
              ? "可供所属业务读取"
              : "不可用或尚未完成"}
          </p>
          <p className="muted">
            此处仅核对执行元数据，日记、作品或对话正文在所属页面按当前来源权限读取。
          </p>
        </>
      )}
    </section>
  );
}
type ExpressionView = {
  expression_id: string;
  revision: number;
  state: string;
  reason: string | null;
  receipt: Receipt | null;
  artifacts: ArtifactRef[];
  source_refs: SourceRef[];
  render_action_ids?: string[];
  public_parts: { kind: string; text?: string; media?: MediaRef }[];
};
function ExpressionTask({ id, scope }: { id: string; scope: DomainTarget }) {
  const view = useDomainResource<ExpressionView>(
    "conversation/expression",
    { ...scope, expression_id: id },
    true,
    5000,
    (value) =>
      [
        "prepared",
        "waiting_render",
        "ready",
        "waiting_order",
        "delivering",
        "sending",
        "waiting_delivery",
      ].includes(value.state),
  );
  const control = useDomainCommand("original-expression:" + id, "global");
  return (
    <section className="panel">
      <div className="section-heading">
        <h3>原公开表达</h3>
        <button className="button" onClick={() => void view.refresh()}>
          读取表达回执
        </button>
      </div>
      {view.error && <p role="alert">{view.error}</p>}
      {view.data && (
        <>
          <p>
            {view.data.state} · 修订 {view.data.revision}
          </p>
          {view.data.reason && <p>{view.data.reason}</p>}
          {view.data.receipt && (
            <ReceiptView receipt={view.data.receipt} scope={scope} />
          )}
          <ol className="expression-parts">
            {view.data.public_parts.map((part, index) => (
              <li key={index}>
                {part.media ? (
                  <MediaPreview media={part.media} scope={scope} />
                ) : part.kind === "text" ? (
                  <p style={{ whiteSpace: "pre-wrap" }}>{part.text}</p>
                ) : (
                  <span>{part.kind}</span>
                )}
              </li>
            ))}
          </ol>
          <ArtifactList artifacts={view.data.artifacts} scope={scope} />
          <SourceEvidence sources={view.data.source_refs ?? []} scope={scope} />
          {view.data.render_action_ids?.map((action) => (
            <a
              className="text-link"
              key={action}
              href={taskHref({
                client_request_id: "",
                action_id: action,
                target: commandTarget(scope),
              })}
            >
              核对原渲染动作 {action}
            </a>
          ))}
          <button
            className="button"
            disabled={control.busy}
            onClick={() =>
              void control
                .run(
                  "conversation/expression/reconcile",
                  { ...scope, expression_id: id },
                  view.data!.revision,
                )
                .then(() => void view.refresh())
            }
          >
            核对原投递与渲染
          </button>
          <CommandFeedback {...control} />
        </>
      )}
    </section>
  );
}
export type RuntimeTasksView = {
  jobs: (ExecutionObservation & {
    id: number;
    task_name: string;
    queue_name: string;
    status: string;
    scheduled_at: string | null;
    attempts: number;
    abort_requested: boolean;
  })[];
  counts: { queue_name: string; status: string; count: number }[];
  complete: boolean;
  limit: number;
};
export type RuntimeResources = {
  processes: {
    role: string;
    ready: boolean;
    registration: Record<string, unknown>;
    heartbeat_at: string;
  }[];
  broker: Record<string, unknown>;
};
export function RuntimeTasks({
  includeLogs = true,
}: { includeLogs?: boolean } = {}) {
  const { session } = useAuth();
  const canRead =
    session?.principal?.permissions.some(
      (permission) => permission === "*" || permission === "config.publish",
    ) === true;
  const tasks = useDomainResource<RuntimeTasksView>(
    "system/tasks",
    { ...GLOBAL_SCOPE, limit: 50 },
    canRead,
    15000,
  );
  const resources = useDomainResource<RuntimeResources>(
    "system/resources",
    GLOBAL_SCOPE,
    canRead,
    15000,
  );
  if (!canRead) return null;
  return (
    <section className="panel">
      <div className="section-heading">
        <h3>当前运行任务与资源</h3>
        <button
          className="button"
          onClick={() => {
            void tasks.refresh();
            void resources.refresh();
          }}
        >
          读取运行状态
        </button>
      </div>
      {tasks.error && <p role="alert">{tasks.error}</p>}
      {resources.error && <p role="alert">{resources.error}</p>}
      <div className="domain-grid">
        {resources.data?.processes.map((process, index) => (
          <article className="domain-record" key={`${process.role}:${index}`}>
            <strong>{process.role}</strong>
            <p>
              {process.ready ? "报告已就绪" : "尚未就绪"} · 心跳{" "}
              {new Date(process.heartbeat_at).toLocaleString("zh-CN")}
            </p>
            <details>
              <summary>实际注册信息</summary>
              <pre>{JSON.stringify(process.registration, null, 2)}</pre>
            </details>
          </article>
        ))}
      </div>
      <div className="domain-grid">
        {tasks.data?.counts.map((row) => (
          <p key={row.queue_name + row.status}>
            {executionLaneName(row.queue_name)} · {label(row.status)} · {row.count}项
          </p>
        ))}
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>任务</th>
              <th>执行通道</th>
              <th>状态</th>
              <th>计划时间</th>
              <th>执行次数</th>
            </tr>
          </thead>
          <tbody>
            {tasks.data?.jobs.map((job) => (
              <tr key={job.id}>
                <td>
                  {job.task_name} <small>#{job.id}</small>
                </td>
                <td>{executionLaneName(job.execution_lane)}</td>
                <td>
                  <ExecutionStatus
                    execution={{ ...job, job_id: job.id, job_state: job.status }}
                    state={job.status}
                    compact
                  />
                  {job.abort_requested ? " · 已请求停止" : ""}
                </td>
                <td>
                  {job.scheduled_at
                    ? new Date(job.scheduled_at).toLocaleString("zh-CN")
                    : "即时"}
                </td>
                <td>{job.attempts}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {tasks.data && !tasks.data.complete && (
        <p className="muted">
          这里只显示最近 {tasks.data.limit}{" "}
          个持久作业；日志和原执行查询可继续核对，不能视为全部历史。
        </p>
      )}
      {resources.data && (
        <DetailCard title="模型与资源Broker实际快照">
          <pre>{JSON.stringify(resources.data.broker, null, 2)}</pre>
        </DetailCard>
      )}
      {includeLogs && <RuntimeLogs />}
    </section>
  );
}
function localTime(value: Date) {
  return new Date(value.getTime() - value.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}
function RuntimeLogs() {
  const [range, setRange] = useState<{
    start_ns: string;
    end_ns: string;
    role: string | null;
  } | null>(null);
  const logs = useDomainResource<{
    streams: { stream: Record<string, string>; values: [string, string][] }[];
    coverage: { complete: boolean; missing: string[] };
    next_end_ns: string | null;
  }>("system/logs", { ...GLOBAL_SCOPE, ...range, limit: 200 }, range !== null);
  return (
    <DetailCard title="真实运行日志">
      <form
        className="domain-form"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          setRange({
            start_ns: (
              BigInt(new Date(String(data.get("start"))).getTime()) * 1000000n
            ).toString(),
            end_ns: (
              BigInt(new Date(String(data.get("end"))).getTime()) * 1000000n
            ).toString(),
            role: String(data.get("role") ?? "") || null,
          });
        }}
      >
        <div className="domain-grid">
          <label>
            开始（本地时间）
            <input
              type="datetime-local"
              name="start"
              defaultValue={localTime(new Date(Date.now() - 3600000))}
              required
            />
          </label>
          <label>
            结束（本地时间）
            <input
              type="datetime-local"
              name="end"
              defaultValue={localTime(new Date())}
              required
            />
          </label>
          <label>
            实际进程
            <select name="role">
              <option value="">全部</option>
              <option value="interactive">interactive</option>
              <option value="model-broker">model-broker</option>
              <option value="worker">worker</option>
            </select>
          </label>
        </div>
        <button className="button" disabled={logs.loading}>
          读取日志范围
        </button>
      </form>
      {logs.error && <p role="alert">{logs.error}</p>}
      {logs.data && (
        <>
          <p>
            {logs.data.coverage.complete
              ? "本次日志范围未触及返回上限"
              : "当前范围尚未完整返回；密集同纳秒记录可缩小时间范围核对"}
            {logs.data.coverage.missing.length
              ? ` · ${logs.data.coverage.missing.join("、")}`
              : ""}
          </p>
          {logs.data.streams.map((stream, index) => (
            <article key={index}>
              <small>
                {Object.entries(stream.stream)
                  .map(([key, value]) => `${key}=${value}`)
                  .join(" · ")}
              </small>
              <pre>
                {stream.values
                  .map(
                    ([stamp, line]) =>
                      `${new Date(Number(BigInt(stamp) / 1000000n)).toLocaleString("zh-CN")} ${line}`,
                  )
                  .join("\n")}
              </pre>
            </article>
          ))}
          {logs.data.next_end_ns && !logs.data.coverage.complete && range && (
            <button
              className="button"
              onClick={() =>
                setRange({
                  ...range,
                  end_ns: (BigInt(logs.data!.next_end_ns!) - 1n).toString(),
                })
              }
            >
              读取更早日志
            </button>
          )}
        </>
      )}
    </DetailCard>
  );
}
