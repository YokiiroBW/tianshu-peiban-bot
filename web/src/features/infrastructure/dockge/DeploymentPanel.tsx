import { useEffect, useState } from "react";
import { time } from "../presentation";
import { OperationStatus } from "./Operations";
import { controlReasons, MaintenanceNotice } from "./presentation";
import { useControlRequest } from "./useControlRequest";
import type { ControlStack, Plan, StackDetail } from "./types";

type Command = ReturnType<typeof useControlRequest>;
type Intent = "create" | "configure" | "update" | "restore";
const labels: Record<Intent, string> = {
  create: "创建部署",
  configure: "修改部署",
  update: "更新镜像",
  restore: "恢复配置版本",
};

function PreviewList({
  label,
  values,
  reasonCodes = false,
}: {
  label: string;
  values: string[];
  reasonCodes?: boolean;
}) {
  return (
    <section>
      <h4>{label}</h4>
      {values.length ? (
        <ul className="infra-compact-list">
          {values.map((value, i) => (
            <li className="infra-mono" key={i}>
              {reasonCodes ? controlReasons([value]) : value}
            </li>
          ))}
        </ul>
      ) : (
        <p className="dockge-source">预览未列出变更</p>
      )}
    </section>
  );
}
export function PlanReview({
  plan,
  usable,
  busy,
  used,
  execute,
}: {
  plan: Plan;
  usable: boolean;
  busy: boolean;
  used: boolean;
  execute: () => void;
}) {
  const [expired, setExpired] = useState(
    () =>
      !Number.isFinite(Date.parse(plan.expires_at)) ||
      Date.parse(plan.expires_at) <= Date.now(),
  );
  useEffect(() => {
    const remaining = Date.parse(plan.expires_at) - Date.now();
    setExpired(!Number.isFinite(remaining) || remaining <= 0);
    if (!Number.isFinite(remaining) || remaining <= 0) return;
    const timer = window.setTimeout(() => setExpired(true), remaining);
    return () => window.clearTimeout(timer);
  }, [plan.expires_at]);
  return (
    <section className="dockge-plan" aria-label="部署计划预览">
      <h3>具体变更预览 · {plan.project_name}</h3>
      <p className="dockge-source">
        计划 · {plan.plan_id} · 有效至 {time(plan.expires_at)}
      </p>
      <MaintenanceNotice value={plan.maintenance} />
      <PreviewList label="配置变更" values={plan.changes} />
      <pre className="dockge-code" tabIndex={0} aria-label="配置差异">
        {plan.diff || "预览没有配置文本差异。"}
      </pre>
      <PreviewList label="镜像" values={plan.images} />
      <PreviewList label="发布端口" values={plan.ports} />
      <PreviewList label="目录与卷" values={plan.mounts} />
      {plan.warnings.length > 0 && (
        <div className="infra-warning">
          <PreviewList label="注意事项" values={plan.warnings} reasonCodes />
        </div>
      )}
      {plan.blockers.length > 0 && (
        <div className="infra-error" role="alert">
          <PreviewList
            label="无法执行的原因"
            values={plan.blockers}
            reasonCodes
          />
        </div>
      )}
      {(!usable || expired) && (
        <p className="infra-warning" role="status">
          输入、节点或配置版本已变化，或计划已经过期。请重新预览。
        </p>
      )}
      <p className="dockge-source">
        按预览修改目标配置，保留数据目录与卷。恢复不会回滚已发生的数据写入。
      </p>
      <button
        className="button button-primary"
        disabled={
          busy ||
          used ||
          !usable ||
          expired ||
          !plan.executable ||
          plan.blockers.length > 0 ||
          plan.maintenance.required
        }
        onClick={execute}
      >
        {used ? "此计划已提交" : "执行此计划"}
      </button>
    </section>
  );
}

export function DeploymentPanel({
  nodeId,
  connectionRevision,
  detail,
  intent,
  restoreVersion,
  available,
  command,
}: {
  nodeId: string;
  connectionRevision: number;
  detail?: StackDetail & { stack: ControlStack };
  intent: Intent;
  restoreVersion?: string;
  available: boolean;
  command: Command;
}) {
  // Drafts bind to the exact version from which editing started. Polling never
  // replaces a user's draft or silently rebases it onto an external edit.
  const [base, setBase] = useState(() => ({
    revision: detail?.stack.revision,
    connection: connectionRevision,
    content: detail?.compose_content ?? "",
  }));
  const [content, setContent] = useState(base.content);
  const [project, setProject] = useState("");
  const [previewSignature, setPreviewSignature] = useState("");
  const [usedPlan, setUsedPlan] = useState("");
  const preview = useControlRequest(
    `${nodeId}/preview/${detail?.stack.stack_id ?? "create"}/${intent}/${restoreVersion ?? ""}`,
    false,
  );
  const signature = JSON.stringify([
    intent,
    content,
    project,
    restoreVersion,
    base.revision,
    base.connection,
  ]);
  const changed =
    base.connection !== connectionRevision ||
    (detail && base.revision !== detail.stack.revision);
  useEffect(() => {
    // A tab can open while the post-operation readback is still arriving. An
    // untouched form has no draft to preserve; edited or previewed input keeps
    // its original version and must be explicitly reloaded.
    if (
      !changed ||
      content !== base.content ||
      previewSignature ||
      preview.busy ||
      command.busy
    )
      return;
    const next = {
      revision: detail?.stack.revision,
      connection: connectionRevision,
      content: detail?.compose_content ?? "",
    };
    setBase(next);
    setContent(next.content);
  }, [
    base.content,
    changed,
    command.busy,
    connectionRevision,
    content,
    detail,
    preview.busy,
    previewSignature,
  ]);
  const plan =
    preview.operation?.kind === "plan" &&
    preview.operation.state === "observed" &&
    preview.operation.result &&
    "plan_id" in preview.operation.result
      ? (preview.operation.result as Plan)
      : null;
  const editing = intent === "configure" || intent === "create";
  const unreadable = intent === "configure" && detail?.compose_content == null;
  const requestPreview = () => {
    setPreviewSignature(signature);
    void preview.send("plan", {
      node_id: nodeId,
      expected_connection_revision: base.connection,
      action: intent,
      ...(intent === "create"
        ? { project_name: project, compose_content: content }
        : {
            stack_id: detail!.stack.stack_id,
            expected_revision: base.revision,
            ...(intent === "configure" ? { compose_content: content } : {}),
            ...(intent === "restore"
              ? { restore_version_id: restoreVersion }
              : {}),
          }),
    });
  };
  return (
    <section aria-label={labels[intent]}>
      <h3>{labels[intent]}</h3>
      {detail?.stack.maintenance.required && (
        <MaintenanceNotice value={detail.stack.maintenance} />
      )}
      {editing && (
        <div className="infra-form dockge-form">
          {intent === "create" && (
            <label>
              应用项目名
              <input
                value={project}
                maxLength={64}
                placeholder="部署端允许的新项目名"
                onChange={(event) => setProject(event.target.value)}
              />
            </label>
          )}
          <label>
            Compose 配置
            <textarea
              spellCheck={false}
              value={content}
              onChange={(event) => setContent(event.target.value)}
              readOnly={unreadable}
            />
          </label>
          <p className="dockge-source">
            仅编辑此应用的 Compose。保留原样的秘密占位符；现有 .env 由节点保留。
          </p>
          {unreadable && (
            <p className="infra-warning">
              未取得可编辑的脱敏配置，请刷新详情。
            </p>
          )}
        </div>
      )}
      {intent === "update" && (
        <p>预览本应用的镜像更新、端口和挂载，核对后执行。</p>
      )}
      {intent === "restore" && (
        <p className="infra-mono">恢复版本 · {restoreVersion}</p>
      )}
      {changed && (
        <div className="infra-notice" role="status">
          <p>当前配置版本已变化，草稿仍绑定原版本。</p>
          <button
            className="button"
            disabled={preview.busy || command.busy}
            onClick={() => {
              const next = {
                revision: detail?.stack.revision,
                connection: connectionRevision,
                content: detail?.compose_content ?? "",
              };
              setBase(next);
              setContent(next.content);
              setPreviewSignature("");
            }}
          >
            载入当前配置
          </button>
        </div>
      )}
      <button
        className="button"
        disabled={
          !available ||
          Boolean(changed) ||
          unreadable ||
          preview.busy ||
          command.busy ||
          (editing && !content.trim()) ||
          (intent === "create" && !project.trim()) ||
          (intent === "restore" && !restoreVersion)
        }
        onClick={requestPreview}
      >
        {preview.busy ? "正在生成预览" : `预览${labels[intent]}`}
      </button>
      {(!plan || preview.busy || preview.error) && (
        <OperationStatus
          {...preview}
          recover={() => {
            void preview.recover();
          }}
        />
      )}
      {plan && (
        <PlanReview
          plan={plan}
          usable={
            !changed &&
            available &&
            signature === previewSignature &&
            plan.connection_revision === connectionRevision &&
            (!detail || plan.expected_revision === detail.stack.revision)
          }
          busy={command.busy || preview.busy}
          used={usedPlan === plan.plan_id}
          execute={async () => {
            const submitted = await command.send("execute", {
              node_id: nodeId,
              expected_connection_revision: connectionRevision,
              plan_id: plan.plan_id,
            });
            if (submitted) setUsedPlan(plan.plan_id);
          }}
        />
      )}
      {detail && !available && (
        <p className="dockge-source">
          {controlReasons(
            detail.stack.allowed_actions[intent]?.reason_codes ?? [],
          ) || "此应用暂不允许这项配置操作。"}
        </p>
      )}
    </section>
  );
}
