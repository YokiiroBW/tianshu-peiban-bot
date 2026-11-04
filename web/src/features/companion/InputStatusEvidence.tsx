import type { SourceRef } from "../../api/contracts";
import type { DomainTarget } from "../../api/useDomainResource";
import { SourceEvidence } from "../../components/SourceEvidence";

type ContextAttempt = {
  attempt_id: string | null;
  round: number | null;
  dispatch: string;
  selected?: boolean | null;
};

type RevisionEvidence = {
  rule_id: string;
  revision: number;
  semantic_revision: number;
  action: "revise" | "retract";
  proposal_id: string;
  attempt_id: string;
};

export type InputStatus = {
  input_id: string | number;
  event_id: string;
  event_revision: number | null;
  context: { selection: string; attempts: ContextAttempt[] };
  learning: {
    state: string;
    revision_state: string;
    episode_id?: string | null;
    episode_revision?: number | null;
    source_refs?: SourceRef[];
    revision_evidence?: RevisionEvidence[];
    reason?: string | null;
  };
  reply_compliance: string | null;
};

const selections: Record<string, string> = {
  selected: "已选入上下文",
  not_selected: "未选入上下文",
  not_recorded: "未记录是否选入上下文",
};
const dispatches: Record<string, string> = {
  submitted: "原请求已提交模型",
  not_started: "尚未开始提交",
  unknown: "提交状态未知",
  not_recorded: "提交状态未记录",
};
const learningStates: Record<string, string> = {
  pending: "待处理",
  waiting: "等待处理",
  failed: "处理失败",
  reviewed: "已审阅",
  unavailable: "当前不可读取",
  not_recorded: "未记录",
};
const revisionStates: Record<string, string> = {
  applied: "持久修订已应用",
  unresolved: "修订结果未决",
  not_recorded: "持久修订结果未记录",
};

export function needsLearningRefresh(items?: InputStatus[] | null): boolean {
  return !!items?.some((item) =>
    ["pending", "waiting"].includes(item.learning?.state),
  );
}

function Attempts({ attempts }: { attempts?: ContextAttempt[] }) {
  if (!attempts?.length) return <p>模型请求提交状态未记录。</p>;
  return (
    <ul>
      {attempts.map((attempt, index) => (
        <li key={`${attempt.attempt_id ?? "unknown"}:${index}`}>
          {attempt.round != null ? `轮次 ${attempt.round}` : "轮次未记录"} ·
          尝试 {attempt.attempt_id ?? "未记录"} ·{" "}
          {attempt.selected === true
            ? "这条输入已进入该轮冻结请求"
            : attempt.selected === false
              ? "这条输入未进入该轮冻结请求"
              : "该轮选材未记录"}{" "}
          · {dispatches[attempt.dispatch] ?? attempt.dispatch}
        </li>
      ))}
    </ul>
  );
}

function InputStatusRecord({ item, scope }: { item: InputStatus; scope: DomainTarget }) {
  const learning = item.learning;
  const hasLearningDetail = !!(
    learning?.episode_id || learning?.reason || learning?.source_refs?.length
  );
  return (
    <article>
      <p>
        输入 {item.input_id} · 事件 {item.event_id}
        {item.event_revision != null ? ` · 版本 ${item.event_revision}` : " · 版本未记录"}
      </p>
      <p>选材：{selections[item.context?.selection] ?? item.context?.selection ?? "未记录"}</p>
      <Attempts attempts={item.context?.attempts} />
      <p>
        学习处理：{learningStates[learning?.state] ?? learning?.state ?? "未记录"} ·{" "}
        {revisionStates[learning?.revision_state] ?? learning?.revision_state ?? "持久修订结果未记录"}
      </p>
      {!!learning?.revision_evidence?.length && (
        <details open>
          <summary>规则修订依据</summary>
          <ul>
            {learning.revision_evidence.map((evidence) => (
              <li key={`${evidence.rule_id}:${evidence.revision}:${evidence.proposal_id}`}>
                {evidence.action === "revise" ? "修订" : "撤回"}规则 {evidence.rule_id} ·
                版本 {evidence.revision} · 语义版本 {evidence.semantic_revision} ·
                提案 {evidence.proposal_id} · 原执行 {evidence.attempt_id}
              </li>
            ))}
          </ul>
        </details>
      )}
      <p>
        回复是否遵守：
        {!item.reply_compliance || item.reply_compliance === "not_recorded" ? "未记录" : item.reply_compliance}。
      </p>
      {hasLearningDetail && (
        <details>
          <summary>学习状态记录</summary>
          {learning.episode_id && (
            <p>关联经历：{learning.episode_id} · 版本 {learning.episode_revision ?? "未记录"}</p>
          )}
          {learning.reason && <p>原因：{learning.reason}</p>}
          {!!learning.source_refs?.length && <SourceEvidence sources={learning.source_refs} scope={scope} />}
        </details>
      )}
    </article>
  );
}

/** Selection, dispatch, durable learning and observed reply are separate facts. */
export function InputStatusEvidence({ items, scope }: { items?: InputStatus[] | null; scope: DomainTarget }) {
  return (
    <section aria-label="本轮输入与学习状态">
      <h4>本轮输入与学习状态</h4>
      {items == null ? (
        <p>历史记录未保存输入与学习状态。</p>
      ) : !items.length ? (
        <p>本轮没有返回输入状态记录。</p>
      ) : (
        items.map((item) => (
          <InputStatusRecord key={`${item.input_id}:${item.event_id}:${item.event_revision}`} item={item} scope={scope} />
        ))
      )}
      <p className="muted">选入上下文或提交模型请求不证明实际回复已经遵守；审阅也不代表持久修订已经完成。</p>
    </section>
  );
}
