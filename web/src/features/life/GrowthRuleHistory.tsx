import { useState } from "react";
import { useDomainResource, type DomainTarget } from "../../api/useDomainResource";
import type { SourceRef } from "../../api/contracts";
import { SourceEvidence } from "../../components/SourceEvidence";
import { showValue, time } from "../memory/types";

type History = {
  id: string;
  current_state: string;
  current_reason: string | null;
  revisions: { rule_id: string; revision: number; semantic_revision: number; action: string; snapshot: unknown; source_refs: SourceRef[]; proposal_id: string | null; attempt_id: string | null; created_at: string }[];
};
export function GrowthRuleHistory({ id, scope }: { id: string; scope: DomainTarget }) {
  const [open, setOpen] = useState(false);
  const view = useDomainResource<History>("companion/growth/history", { ...scope, id }, open);
  return <details onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>规则历史与依据</summary>
    {view.error && <p role="alert">{view.error}</p>}
    {view.loading && !view.data && <p role="status">正在读取规则历史…</p>}
    {view.data && <>
      <p>当前状态：{view.data.current_state}{view.data.current_reason ? ` · ${view.data.current_reason}` : ""}</p>
      {!view.data.revisions.length && <p>没有返回历史修订。</p>}
      {view.data.revisions.map((item) => <article className="domain-record" key={`${item.rule_id}:${item.revision}`}>
        <p>修订 {item.revision} · 语义版本 {item.semantic_revision} · {item.action} · {time(item.created_at)}</p>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{showValue(item.snapshot)}</pre>
        <SourceEvidence sources={item.source_refs ?? []} scope={scope} />
        {item.attempt_id && <a href={`#/system/tasks?attempt=${encodeURIComponent(item.attempt_id)}`}>查看原尝试</a>}
      </article>)}
    </>}
  </details>;
}
