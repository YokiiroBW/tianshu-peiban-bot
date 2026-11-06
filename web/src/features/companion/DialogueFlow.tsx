import { readableRecord } from "../../design/readableRecord";
type FlowStage = { state: string; [key: string]: unknown };
type FlowRound = {
  round: number;
  attempt_id: string | null;
  accepted?: FlowStage;
  parsed?: FlowStage;
  applied?: FlowStage;
  expression?: FlowStage;
  delivered?: FlowStage;
};
export type DialogueFlow = {
  input?: { state: string; input_id?: string; event_id?: string; revision?: number; received_at?: string } | null;
  coverage?: "recorded" | "not_recorded";
  rounds?: FlowRound[];
};

const stages = [
  ["accepted", "模型接收"],
  ["parsed", "结果解析"],
  ["applied", "业务应用"],
  ["expression", "表达"],
  ["delivered", "投递"],
] as const;
const states: Record<string, string> = {
  accepted: "已接收", parsed: "已解析", succeeded: "已完成", failed: "失败", pending: "等待中",
  unknown: "未知", not_applicable: "不适用", not_recorded: "未记录",
};

/** Render recorded facts independently; a completed round does not prove delivery. */
export function DialogueFlowEvidence({ flow }: { flow?: DialogueFlow | null }) {
  return <section aria-label="本轮处理阶段">
    <h4>本轮处理阶段</h4>
    {!flow ? <p>历史记录没有处理阶段明细。</p> : <>
      <p>输入：{flow.input?.state ? (states[flow.input.state] ?? flow.input.state) : "未记录"}{flow.input?.received_at ? ` · ${flow.input.received_at}` : ""}</p>
      {flow.coverage === "not_recorded" && <p>历史记录没有完整阶段明细。</p>}
      {!flow.rounds?.length && <p>没有记录模型轮次。</p>}
      {flow.rounds?.map((round, index) => <article key={`${round.round}:${round.attempt_id ?? index}`}>
        <p>模型轮次 {round.round}{round.attempt_id ? " · 已关联执行记录" : " · 尝试未记录"}</p>
        <ul>{stages.map(([key, label]) => {
          const stage = round[key];
          return <li key={key}><strong>{label}：</strong>{stage?.state ? (states[stage.state] ?? stage.state) : "未记录"}</li>;
        })}</ul>
        <details><summary>阶段原始记录</summary><pre>{readableRecord(round)}</pre></details>
      </article>)}
    </>}
  </section>;
}
