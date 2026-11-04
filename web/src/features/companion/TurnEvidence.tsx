import { useState } from "react";
import { useDomainResource } from "../../api/useDomainResource";
import type { Receipt } from "../../api/contracts";
import { ContextSelection } from "../../components/ContextSelection";
import { DialogueFlowEvidence, type DialogueFlow } from "./DialogueFlow";

type TurnEvidenceData = {
  state: string;
  receipt: Receipt | null;
  context_selection?: unknown;
  token_usage?: unknown;
  provider_usage?: unknown;
  flow?: DialogueFlow | null;
};
const states: Record<string, string> = { collecting: "收集消息", queued: "等待回应", running: "正在回应", waiting_external: "等待原任务", waiting_expression: "准备表达", completed: "本轮完成", failed: "本轮未完成", unknown: "结果未确认", cancelled: "已取消" };

export function TurnEvidence({ turnId, selector }: { turnId: string; selector: object }) {
  const [open, setOpen] = useState(false);
  const turn = useDomainResource<TurnEvidenceData>("conversation/turn", { ...selector, turn_id: turnId }, open);
  return <details onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>查看本轮材料与状态</summary>
    {turn.loading && !turn.data && <p role="status">正在读取本轮记录…</p>}
    {turn.error && <p role="alert">{turn.error}</p>}
    {turn.data && <>
      <p>本轮状态：{states[turn.data.state] ?? turn.data.state}{turn.data.receipt ? ` · 执行 ${turn.data.receipt.execution} · 效果 ${turn.data.receipt.effect} · 投递 ${turn.data.receipt.delivery}` : " · 尚无表达回执"}</p>
      <DialogueFlowEvidence flow={turn.data.flow} />
      <ContextSelection selection={turn.data.context_selection} tokenUsage={turn.data.token_usage} providerUsage={turn.data.provider_usage} />
    </>}
  </details>;
}
