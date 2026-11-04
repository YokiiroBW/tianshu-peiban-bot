import { useEffect, useState } from "react";
import {
  useDomainResource,
  useDomainCommand,
  actorScope,
} from "../../api/useDomainResource";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { CommandFeedback } from "../../components/CommandFeedback";
import { SourceEvidence } from "../../components/SourceEvidence";
import { CurioSectionHead } from "../../components/curio/OrientalWidgets";
import {
  dedupeTitleAndBody,
  sanitizeInternalText,
} from "../../design/humanize";
import type { GrowthItem, GrowthView } from "./domainTypes";
import { GrowthRuleHistory } from "./GrowthRuleHistory";
import { GrowthRuleEditor } from "./GrowthRuleEditor";
import { GrowthExamples } from "./GrowthExamples";
import { DetailCard } from "../../components/DetailCard";
import { useMemoryCursor } from "../memory/pagination";
import { showValue, time } from "../memory/types";
import "../multimodal/multimodal.css";
import "../memory/memory.css";

function reviewReason(code: string) {
  if (code === "generation_request_expired")
    return "原请求在截止前未登记模型，本批尚未形成成长记录。";
  if (code.includes("budget") || code.includes("context_mandatory"))
    return "当前材料超出预算，处理停在下列原始经历；未跳过该条或宣称已经整理完成。";
  if (code.includes("source") || code.includes("episode"))
    return "部分原始经历依据缺失或已变化，本批尚未写入新的成长记录。";
  return "本批材料或模型结果尚未满足落库条件；已经保存的成长记录仍可查看。";
}

export default function GrowthPage() {
  const { actor } = useRoleWorkspace();
  const [editing, setEditing] = useState<GrowthItem | null>(null);
  useEffect(() => setEditing(null), [actor]);
  const scope = actorScope(actor);
  const cursor = useMemoryCursor(scope, "growth");
  const view = useDomainResource<GrowthView>(
    "companion/growth/view",
    { ...scope, cursor: cursor.cursor },
    !!actor,
  );
  const action = useDomainCommand(`growth:${actor}`, "actor");
  const review = view.data?.review;
  const materialMissing = new Set([
    ...(review?.material_missing ?? []),
    ...Object.values(review?.batch?.material_coverage ?? {}).flatMap(
      (coverage) => coverage.missing,
    ),
  ]);
  const discoveryOnly = materialMissing.has(
    "discovery_metadata_only_body_not_read",
  );
  const unassessedContent = [...materialMissing].some((reason) =>
    reason.endsWith("_not_assessed"),
  );
  const sourcePartial = [...materialMissing].some(
    (reason) =>
      reason !== "discovery_metadata_only_body_not_read" &&
      !reason.endsWith("_not_assessed"),
  );

  return (
    <div className="domain-workspace">
      <header className="page-top-bar domain-toolbar">
        <div className="page-identity">
          <span className="role-seal-avatar">悟</span>
          <div className="page-title-wrap">
            <p className="eyebrow">经历、手记与成长</p>
            <h2>这次变化来自哪里</h2>
          </div>
        </div>
        <button
          className="button curio-btn"
          disabled={!actor || view.loading}
          onClick={() => void view.refresh()}
        >
          读取变化
        </button>
      </header>
      {!actor && <p>先选择一位角色。</p>}
      {view.error && (
        <p role="alert" className="error-text">
          {view.error}
        </p>
      )}
      {view.loading && !view.data && (
        <p role="status">正在读取角色的手记与变化…</p>
      )}
      {view.data && (
        <section className="curio-card panel">
          <CurioSectionHead
            seal="省"
            title="角色手记、兴趣与表达变化"
            subtitle="手记表达角色当时的理解，事实与推断保留各自依据。想象活动只支持主观反思，不能证明现实活动或真实工具成功。"
          />
          {review && (
            <section
              className="memory-consolidation curio-consolidation-bar"
              aria-label="成长整理进度"
            >
              <h3>整理进度与固定材料</h3>
              <p>
                待处理经历版本：{review.pending_inputs ?? "尚未取得"} ·
                待观察原请求：{review.pending_original_requests ?? "尚未取得"}
              </p>
              {review.retry_summary &&
                (review.retry_summary.waiting > 0 ||
                  review.retry_summary.active > 0 ||
                  review.retry_summary.blocked > 0) && (
                  <div role="status" aria-label="成长自动重试">
                    <p>
                      等待重试 {review.retry_summary.waiting} 批 · 正在处理重试{" "}
                      {review.retry_summary.active} 批 · 停止自动重试 {review.retry_summary.blocked} 批。
                      暂时失败的材料已单独保留，后续经历可以继续整理。
                    </p>
                    {review.retry_summary.next_retry_at && (
                      <p>
                        下一次重试：{time(review.retry_summary.next_retry_at)}
                      </p>
                    )}
                  </div>
                )}
              {!!review.retry_batches?.length && (
                <details>
                  <summary>重试批次与处理次数</summary>
                  {review.retry_batches.map((retry) => (
                    <section key={retry.retry_key}>
                      <p>
                        {
                          {
                            waiting: retry.waiting_on_original
                              ? "等待原执行结果"
                              : "等待自动重试",
                            active: "正在处理",
                            completed: "已完成",
                            blocked: "已停止自动重试",
                          }[retry.state]
                        }
                        · 已安排尝试 {retry.attempt_number} 次
                        {retry.due_at && ` · 下次 ${time(retry.due_at)}`}
                      </p>
                      {retry.state === "blocked" && (
                        <p>
                          此批未自动重试，原因保留为{" "}
                          {retry.last_error ?? "尚未取得"}
                          。后续经历不受此批阻挡。
                        </p>
                      )}
                      {retry.outcome === "historical_only" && (
                        <p>
                          保留了较新经历形成的当前状态；此批只补充历史手记，原判断仍可追溯。
                        </p>
                      )}
                      <p className="muted">执行记录：{retry.attempt_id}</p>
                      {view.data?.can_review && retry.can_request_review && (
                        <button
                          className="button"
                          disabled={action.busy}
                          onClick={async () => {
                            if (
                              await action.run(
                                "companion/growth/review",
                                { ...scope, retry_key: retry.retry_key },
                                review.revision,
                              )
                            )
                              await view.refresh();
                          }}
                        >
                          重新整理这批材料
                        </button>
                      )}
                    </section>
                  ))}
                </details>
              )}
              {!!materialMissing.size && (
                <div aria-label="反思材料范围">
                  {!!review.partial_inputs && (
                    <p>
                      有 {review.partial_inputs}{" "}
                      条经历按有限材料整理，保留了未评估范围。
                    </p>
                  )}
                  {discoveryOnly && (
                    <p>
                      检索经历只说明发现了候选；这类记录不包含正文阅读，不能视为已读完或形成读后结论。
                    </p>
                  )}
                  {unassessedContent && (
                    <p>
                      部分动作结果的完整内容尚未纳入反思。原始结果仍保留，可从经历依据追溯。
                    </p>
                  )}
                  {sourcePartial && (
                    <p>部分原始结果仍有未覆盖的素材范围，相关限制继续保留。</p>
                  )}
                </div>
              )}
              {review.legacy_state && review.legacy_state !== "complete" && (
                <p role="status">
                  正在
                  {review.legacy_state === "requests"
                    ? "登记原请求引用清单"
                    : "接续旧处理位置后的经历版本"}
                  ，完成后继续原执行；当前进度不表示执行失败。
                </p>
              )}
              {review.batch ? (
                <details open>
                  <summary>
                    {review.batch.legacy_material
                      ? "已固定的原请求材料"
                      : "本次固定经历批次"}
                  </summary>
                  <p>
                    材料标识：{sanitizeInternalText(review.batch.material_key)}
                  </p>
                  {review.batch.inputs?.length ? (
                    <ul>
                      {review.batch.inputs.map(([id, revision]) => (
                        <li key={`${id}:${revision}`}>
                          {sanitizeInternalText(id)} · v{revision}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p>此原请求未提供可展示的逐条版本清单。</p>
                  )}
                  <p>
                    {review.batch.has_more
                      ? "固定批次之后仍有后续材料。"
                      : "后续材料以当前待处理集合为准。"}
                  </p>
                  <details>
                    <summary>固定位置与水位</summary>
                    <pre>{showValue(review.batch)}</pre>
                  </details>
                </details>
              ) : (
                <p>当前没有固定执行中的批次。</p>
              )}
              {review.recovery === "observe_original_attempt" ? (
                <div role="status">
                  <p>
                    原请求仍在排队、执行或等待状态确认，暂不重复提交。
                    可恢复的中断会保留原记录，并另行安排重试。
                  </p>
                  <p className="muted">
                    原 attempt：{review.attempt_id ?? "尚未取得"}
                  </p>
                  <button
                    className="button"
                    disabled={view.loading}
                    onClick={() => void view.refresh()}
                  >
                    读取原执行状态
                  </button>
                </div>
              ) : (
                review.last_error && (
                  <p role="status">{reviewReason(review.last_error)}</p>
                )
              )}
              {review.blocked_material && (
                <details open>
                  <summary>停留的原始材料</summary>
                  <p>
                    经历：
                    {sanitizeInternalText(review.blocked_material.episode_id) ||
                      "尚未取得"}{" "}
                    · 版本 {review.blocked_material.revision ?? "未知"}
                  </p>
                  <pre>{showValue(review.blocked_material)}</pre>
                </details>
              )}
              {!!review.legacy_missing?.length && (
                <p>
                  原执行恢复仍有 {review.legacy_missing.length}{" "}
                  项材料或请求信息缺失。
                </p>
              )}
              {view.data.can_review && review.can_request_review && (
                <button
                  className="button"
                  disabled={action.busy}
                  onClick={async () => {
                    if (
                      await action.run(
                        "companion/growth/review",
                        scope,
                        review.revision,
                      )
                    )
                      await view.refresh();
                  }}
                >
                  重新读取材料并整理
                </button>
              )}
              {review.last_reviewed_at && (
                <p className="muted">
                  最近整理记录：{time(review.last_reviewed_at)}
                </p>
              )}
              {(review.last_error || review.legacy_missing?.length) && (
                <details>
                  <summary>整理诊断</summary>
                  <pre>{showValue(review)}</pre>
                </details>
              )}
            </section>
          )}
          {!!view.data.missing.length && (
            <p className="muted">
              当前读取的部分依据不可用，未展示未经授权的来源。
            </p>
          )}
          {!view.data.items.length && !view.data.reflections.length && (
            <p className="muted">当前可读范围还没有成长记录。</p>
          )}
          <div className="domain-grid curio-growth-grid">
            {[...view.data.items, ...view.data.reflections].map((item) => {
              const { displayTitle, displayBody } = dedupeTitleAndBody(
                item.title,
                item.content,
              );
              return (
                <article
                  className="curio-card domain-record"
                  key={`${item.kind}:${item.id}:${item.revision}`}
                >
                  <header className="section-head">
                    <strong>{displayTitle}</strong>
                    <span className="celadon-tag badge">
                      {
                        {
                          reflection: "角色手记",
                          interest: "兴趣",
                          style: "表达习惯",
                        }[item.kind]
                      }
                    </span>
                  </header>
                  {displayBody && <p>{displayBody}</p>}
                  <p className="muted">状态：{item.state === "revoked" ? "已撤回" : item.state === "active" ? "当前有效" : item.state} · 版本 {item.revision}</p>
                  {item.reason && (
                    <p className="muted">
                      {item.reason === "explicit_user_change"
                        ? "用户明确调整"
                        : sanitizeInternalText(item.reason)}
                    </p>
                  )}
                  {item.occurred_at && (
                    <time className="meta-tag" dateTime={item.occurred_at}>
                      {time(item.occurred_at)}
                    </time>
                  )}
                  {!!Object.keys(item.conditions).length && (
                    <DetailCard title="适用条件">
                      <pre>{showValue(item.conditions)}</pre>
                    </DetailCard>
                  )}
                  {!!item.counterexamples.length && (
                    <DetailCard title="反例与限制">
                      <ul>
                        {item.counterexamples.map((value, index) => (
                          <li key={index}>{sanitizeInternalText(value)}</li>
                        ))}
                      </ul>
                    </DetailCard>
                  )}
                  <DetailCard title="允许查看的经历依据">
                    <SourceEvidence sources={item.evidence} scope={scope} />
                  </DetailCard>
                  {["style", "interest"].includes(item.kind) && <GrowthRuleHistory id={item.id} scope={scope} />}
                  {item.can_modify && ["style", "interest"].includes(item.kind) && item.state !== "revoked" && <button className="button" onClick={() => setEditing(structuredClone(item))}>修订这项变化</button>}
                  {editing?.id === item.id && <GrowthRuleEditor key={`${editing.id}:${editing.revision}`} item={editing} scope={scope} onClose={() => setEditing(null)} onSaved={async () => { setEditing(null); await view.refresh(); }} />}
                  {item.can_modify &&
                    ["style", "interest"].includes(item.kind) && (
                      <button
                        className="button"
                        disabled={action.busy}
                        onClick={async () => {
                          if (
                            await action.run(
                              item.state === "revoked"
                                ? "companion/growth/restore"
                                : "companion/growth/revoke",
                              { ...scope, id: item.id },
                              item.revision,
                            )
                          )
                            await view.refresh();
                        }}
                      >
                        {item.state === "revoked"
                          ? "恢复这项变化"
                          : "撤回这项变化"}
                      </button>
                    )}
                </article>
              );
            })}
          </div>
          <div className="domain-actions">
            {cursor.previous && (
              <button
                className="button"
                disabled={view.loading}
                onClick={cursor.back}
              >
                上一批变化
              </button>
            )}
            {view.data.next_cursor && (
              <button
                className="button"
                disabled={view.loading}
                onClick={() => cursor.next(view.data!.next_cursor!)}
              >
                继续读取变化
              </button>
            )}
          </div>
          <GrowthExamples scope={scope} canReview={view.data.can_review} />
          <CommandFeedback {...action} />
        </section>
      )}
    </div>
  );
}
