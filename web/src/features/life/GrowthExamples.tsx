import { useDomainCommand, useDomainResource, type DomainTarget } from "../../api/useDomainResource";
import type { SourceRef } from "../../api/contracts";
import { SourceEvidence } from "../../components/SourceEvidence";
import { CommandFeedback } from "../../components/CommandFeedback";
import { useMemoryCursor } from "../memory/pagination";
import { time } from "../memory/types";
import { humanizeCode } from "../../design/humanize";

type Example = { id: string; rule_id: string; rule_revision: number; semantic_revision: number; turn_id: string; expression_id: string | null; input_message_ids: (string | number)[]; output_message_ids: (string | number)[]; state: string; reason: string | null; revision: number; exposures: number; delivered_uses: number; source_refs: SourceRef[]; created_at: string };
type ExamplePage = { items: Example[]; next_cursor: string | null; missing: string[] };

export function GrowthExamples({ scope, canReview }: { scope: DomainTarget; canReview: boolean }) {
  const cursor = useMemoryCursor(scope, "growth-examples");
  const view = useDomainResource<ExamplePage>("companion/growth/examples/view", { ...scope, cursor: cursor.cursor, limit: 20 }, !!scope.actor_id);
  const action = useDomainCommand("growth-example", "actor");
  return <section aria-label="表达样例与使用记录">
    <h3>表达样例与使用记录</h3>
    <p className="muted">选入材料、实际投递与明确反馈是不同事实；没有反馈不表示认可。</p>
    {view.error && <p role="alert">{view.error}</p>}
    {view.loading && !view.data && <p role="status">正在读取样例…</p>}
    {view.data?.missing?.length ? <p>部分来源不可读取：{view.data.missing.join("、")}</p> : null}
    {view.data?.items.map((item) => <article className="domain-record" key={item.id}>
      <p>状态：{humanizeCode(item.state, "样例状态尚未确认")} {item.reason ? `· ${humanizeCode(item.reason, "处理原因尚未说明")}` : ""} · 规则版本 {item.rule_revision} · 语义版本 {item.semantic_revision}</p>
      <p>进入上下文 {item.exposures} 次 · 已投递 {item.delivered_uses} 次 · {time(item.created_at)}</p>
      <p>已关联原对话 · 输入消息 {item.input_message_ids.length} 条 · 输出消息 {item.output_message_ids.length} 条</p>
      <SourceEvidence sources={item.source_refs ?? []} scope={scope} />
      {item.expression_id && <a href={`#/system/tasks?expression=${encodeURIComponent(item.expression_id)}`}>查看原表达</a>}
      {canReview && item.state !== "rejected" && <button className="button" disabled={action.busy} onClick={() => {
        void action.run("companion/growth/examples/reject", { ...scope, id: item.id }, item.revision).then((result) => { if (result) void view.refresh(); });
      }}>撤回此表达样例</button>}
    </article>)}
    {view.data && !view.data.items.length && <p>当前范围没有可读的表达样例。</p>}
    <div className="domain-actions">{cursor.previous && <button className="button" onClick={cursor.back}>上一批样例</button>}{view.data?.next_cursor && <button className="button" onClick={() => cursor.next(view.data!.next_cursor!)}>下一批样例</button>}</div>
    <CommandFeedback {...action} />
  </section>;
}
