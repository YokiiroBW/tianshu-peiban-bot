import { useState } from "react";
import type { Coverage, Page } from "../../api/contracts";
import {
  useDomainResource,
  type DomainTarget,
} from "../../api/useDomainResource";
import { SourceEvidence } from "../../components/SourceEvidence";
import { DetailCard } from "../../components/DetailCard";
import {
  humanizeScopeTarget,
  sanitizeInternalText,
  shortHashTag,
} from "../../design/humanize";
import { useMemoryCursor } from "./pagination";
import {
  assertionNames,
  evidenceKinds,
  showValue,
  time,
  type EvidenceUnit,
  type Matter,
  type RevisionDetails,
  type SemanticLink,
} from "./types";

const statuses: Record<string, string> = {
  current: "当前版本",
  historical: "历史版本",
  superseded: "已被替代",
  disputed: "存在分歧",
  active: "有效",
  revoked: "已撤回",
  corrected: "已纠正",
  retired: "已退出当前认识",
  legacy: "旧记录",
  create: "新增",
  correct: "纠正",
  transition: "状态变化",
  reinforce: "补充依据",
  supersedes: "替代",
  contradicts: "分歧",
  supports: "支持",
  counterexample: "反例",
  about_matter: "关联事项",
};

export const memoryLabel = (value: string) =>
  statuses[value] ?? sanitizeInternalText(value);

const missingNames: Record<string, string> = {
  coverage_unknown: "本次记录没有提供素材覆盖范围，不能据此认定已读完或看完。",
  observation_analysis_unavailable: "观察分析尚未形成可核验的材料。",
  legacy_recorded_time_unknown: "部分旧记录的首次记录时间未知",
  valid_time_unknown: "部分证据的有效时间未知",
  reflection_valid_time_unknown: "反思的适用时间未知",
  matter_effective_time_inferred_from_report: "事项时间依据报告时间解释",
  completion_basis_unknown: "完成依据尚未确认",
  unavailable_sources: "部分来源不可用",
  source_unavailable: "部分来源不可用",
  ranked_selection: "这是相关候选，不能视为完整列表",
  recent_processing_window: "只显示最近处理窗口，更多记录请读取经历分页",
  subjects_unavailable: "部分主体当前不可读取",
  pipeline_unavailable: "尚未取得整理状态",
  recorded_at_unknown: "系统记录时间未知",
  valid_from_unknown: "有效起始时间未知",
};

export function CoverageNote({ coverage }: { coverage?: Coverage | null }) {
  if (!coverage) return <p className="muted">本次覆盖范围未知。</p>;
  return (
    <div className="memory-coverage curio-coverage-note">
      <p className="muted">
        {coverage.complete ? "本次读取范围完整" : "本次读取仍有后续或证据缺项"}
        {coverage.total != null ? ` · 共 ${coverage.total} 项` : ""}
      </p>
      {!!coverage.missing.length && (
        <ul className="curio-missing-list">
          {coverage.missing.map((reason) => (
            <li key={reason}>
              {missingNames[reason] ?? "部分材料或时间依据尚未完整确认"}
            </li>
          ))}
        </ul>
      )}
      {!!coverage.missing.length && (
        <DetailCard title="覆盖诊断">
          <pre>{showValue(coverage)}</pre>
        </DetailCard>
      )}
    </div>
  );
}

type DetailFields = RevisionDetails & {
  revision: number;
  subject_id: string | null;
  speaker_id?: string | null;
  assertion?: string;
  polarity?: boolean | null;
  state: string;
  conditions: Record<string, unknown>;
  valid_from?: string | null;
  valid_until?: string | null;
  occurred_at?: string | null;
  due_at?: string | null;
  interpretation?: boolean;
  completion_basis?: Matter["completion_basis"];
};

export function EvidenceDetails({
  item,
  scope,
  identities = {},
}: {
  item: DetailFields;
  scope: DomainTarget;
  identities?: Record<string, string>;
}) {
  return (
    <div className="memory-evidence-details">
      <dl className="memory-evidence-facts">
        <div>
          <dt>主体</dt>
          <dd>
            {item.subject_id
              ? humanizeScopeTarget(item.subject_id, identities)
              : "共享事项或未指定主体"}
          </dd>
        </div>
        <div>
          <dt>原作者</dt>
          <dd>
            {item.speaker_id
              ? humanizeScopeTarget(item.speaker_id, identities)
              : "未知（来源未证明唯一作者）"}
          </dd>
        </div>
        {item.assertion && (
          <div>
            <dt>陈述依据</dt>
            <dd>
              {assertionNames[item.assertion] ?? item.assertion}
              {item.polarity == null
                ? ""
                : item.polarity
                  ? " · 肯定"
                  : " · 否定"}
            </dd>
          </div>
        )}
        <div>
          <dt>状态与版本</dt>
          <dd>
            {memoryLabel(item.state)} ·{" "}
            {item.current_status
              ? memoryLabel(item.current_status)
              : "版本状态未知"}{" "}
            · v{item.revision}
            {item.head_revision != null && item.head_revision !== item.revision
              ? `（当前 v${item.head_revision}）`
              : ""}
          </dd>
        </div>
        <div>
          <dt>事件 / 观察时间</dt>
          <dd>{time(item.occurred_at ?? item.observed_at)}</dd>
        </div>
        <div>
          <dt>有效起始</dt>
          <dd>{time(item.valid_from)}</dd>
        </div>
        <div>
          <dt>有效截止</dt>
          <dd>{time(item.valid_until)}</dd>
        </div>
        <div>
          <dt>系统记录时间</dt>
          <dd>{time(item.recorded_at)}</dd>
        </div>
        {"due_at" in item && (
          <div>
            <dt>约定时间</dt>
            <dd>{time(item.due_at)}</dd>
          </div>
        )}
        <div>
          <dt>修订原因</dt>
          <dd>
            {item.change_reason || "未记录"}
            {item.change_kind ? ` · ${memoryLabel(item.change_kind)}` : ""}
          </dd>
        </div>
      </dl>
      {!!Object.keys(item.conditions).length && (
        <details>
          <summary>完整适用条件</summary>
          <pre>{showValue(item.conditions)}</pre>
        </details>
      )}
      {item.interpretation && (
        <p className="muted">
          这是可修订的解释，仍须结合依据、条件与反例理解。
        </p>
      )}
      {"completion_basis" in item && item.state === "completed" && (
        <div className="memory-completion-basis">
          <p>
            {item.completion_basis?.kind === "human_report"
              ? "完成依据：人类报告完成；未据此宣称天枢执行成功。"
              : item.completion_basis?.kind === "action_receipt"
                ? "完成依据：真实动作执行回执。"
                : "完成依据未知，不能据此确认真实执行成功。"}
          </p>
          {item.completion_basis && (
            <details>
              <summary>完成依据详情</summary>
              <pre>{showValue(item.completion_basis)}</pre>
              {!!item.completion_basis.source_refs?.length && (
                <SourceEvidence
                  sources={item.completion_basis.source_refs}
                  scope={scope}
                />
              )}
            </details>
          )}
        </div>
      )}
      {!!item.counterexample_refs?.length && (
        <details>
          <summary>反例依据</summary>
          <SourceEvidence sources={item.counterexample_refs} scope={scope} />
        </details>
      )}
      {!!item.derivation_refs?.length && (
        <details>
          <summary>派生依赖</summary>
          <SourceEvidence sources={item.derivation_refs} scope={scope} />
        </details>
      )}
      {!!item.support_groups?.length && (
        <details>
          <summary>独立支持组</summary>
          {item.support_groups.map((refs, index) => (
            <div key={index}>
              <p>支持组 {index + 1}</p>
              <SourceEvidence sources={refs} scope={scope} />
            </div>
          ))}
        </details>
      )}
    </div>
  );
}

function RelationEndpoint({
  item,
  fallback,
  scope,
  identities,
}: {
  item?: EvidenceUnit | null;
  fallback: SemanticLink["from_ref"];
  scope: DomainTarget;
  identities: Record<string, string>;
}) {
  return (
    <section className="memory-relation-endpoint">
      <p>
        <strong>
          {evidenceKinds[fallback.kind] ?? fallback.kind} ·{" "}
          {shortHashTag(fallback.id)} · v{fallback.revision}
        </strong>
      </p>
      {item ? (
        <>
          <p className="memory-prewrap">{item.content}</p>
          <EvidenceDetails item={item} scope={scope} identities={identities} />
          <SourceEvidence sources={item.source_refs} scope={scope} />
          <CoverageNote coverage={item.coverage} />
        </>
      ) : (
        <p>当前没有可读取的完整端点依据。</p>
      )}
    </section>
  );
}

export function EvidenceRelations({
  relations = [],
  scope,
  identities = {},
}: {
  relations?: SemanticLink[];
  scope: DomainTarget;
  identities?: Record<string, string>;
}) {
  if (!relations.length) return null;
  return (
    <details className="memory-relations">
      <summary>分歧、替代与关联（{relations.length}）</summary>
      {relations.map((relation) => (
        <article key={relation.link_id}>
          <h4>
            {memoryLabel(relation.relation_kind)} ·{" "}
            {memoryLabel(relation.state)}
          </h4>
          <p className="muted">
            关系有效时间：{time(relation.valid_from)} 至{" "}
            {time(relation.valid_until)} · 记录于 {time(relation.recorded_at)}
          </p>
          <div className="memory-relation-pair">
            <RelationEndpoint
              item={relation.from_evidence}
              fallback={relation.from_ref}
              scope={scope}
              identities={identities}
            />
            <RelationEndpoint
              item={relation.to_evidence}
              fallback={relation.to_ref}
              scope={scope}
              identities={identities}
            />
          </div>
          <SourceEvidence sources={relation.source_refs} scope={scope} />
        </article>
      ))}
    </details>
  );
}

function HistoryPage({
  kind,
  id,
  scope,
  identities,
}: {
  kind: "claims" | "matters";
  id: string;
  scope: DomainTarget;
  identities: Record<string, string>;
}) {
  const cursor = useMemoryCursor({ ...scope, id }, `${kind}/history`);
  const view = useDomainResource<Page<EvidenceUnit>>(`memory/${kind}/history`, {
    ...scope,
    [kind === "claims" ? "claim_id" : "matter_id"]: id,
    cursor: cursor.cursor,
    limit: 12,
  });
  return (
    <div className="memory-history">
      {view.error && <p role="alert">{view.error}</p>}
      {view.loading && <p role="status">正在读取已授权修订历史…</p>}
      {view.data && (
        <>
          <CoverageNote coverage={view.data.coverage} />
          <ol>
            {view.data.items.map((item) => (
              <li key={`${item.id}:${item.revision}`}>
                <p className="memory-prewrap">{item.content}</p>
                <EvidenceDetails
                  item={item}
                  scope={scope}
                  identities={identities}
                />
                <EvidenceRelations
                  relations={item.relations}
                  scope={scope}
                  identities={identities}
                />
                <SourceEvidence sources={item.source_refs} scope={scope} />
                <CoverageNote coverage={item.coverage} />
              </li>
            ))}
          </ol>
          {!view.data.items.length && <p>当前没有可读取的修订历史。</p>}
        </>
      )}
      <div className="models-actions">
        {cursor.previous && (
          <button
            className="button"
            disabled={view.loading}
            onClick={cursor.back}
          >
            上一批修订
          </button>
        )}
        {view.data?.next_cursor && (
          <button
            className="button"
            disabled={view.loading}
            onClick={() => cursor.next(view.data!.next_cursor!)}
          >
            下一批修订
          </button>
        )}
      </div>
    </div>
  );
}

export function EvidenceHistory({
  kind,
  id,
  scope,
  identities = {},
}: {
  kind: "claims" | "matters";
  id: string;
  scope: DomainTarget;
  identities?: Record<string, string>;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        type="button"
        className="text-link ink-link"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {open ? "收起修订历史" : "查看修订历史"}
      </button>
      {open && (
        <HistoryPage
          kind={kind}
          id={id}
          scope={scope}
          identities={identities}
        />
      )}
    </div>
  );
}
