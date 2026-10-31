import { DetailCard } from "../../components/DetailCard";
import type { Rule, RuleContext, RuleResult } from "./types";
import { reasonWords, sourceWords } from "./wording";
import { ruleFields, ruleOperations } from "./ruleFields";
type TraceRule = {
  rule_id: string;
  field: string;
  op: string;
  result: string;
  reason: string;
  negate?: boolean;
};
type TraceGroup = {
  group_id: string;
  list_kind: string;
  result: string;
  reason: string;
  rules: TraceRule[];
};
function groups(trace: unknown): TraceGroup[] {
  if (!trace || typeof trace !== "object") return [];
  const entries = (trace as Record<string, unknown>).trace;
  return Array.isArray(entries) ? (entries as TraceGroup[]) : [];
}

export function RuleResults({
  results,
  revision,
  context,
}: {
  results: RuleResult[];
  revision: number;
  context?: RuleContext;
}) {
  return (
    <section className="media-rule-results" aria-label="规则试算结果">
      <h4>试算结果 · 规则版本 {revision}</h4>
      {context && (
        <div className="media-rule-context">
          <p className="muted">
            以下为整个视频的事实，所有分 P 共用；总时长不是单 P 时长。
          </p>
          <p>
            来源：
            {context.source
              ? `${sourceWords[context.source.kind] ?? context.source.kind}${context.source.kind === "watch_later" ? "" : ` · ${context.source.id}`}`
              : "未选择来源"}{" "}
            ·{" "}
            {context.member === true
              ? "已核实属于该来源"
              : context.member === false
                ? "已核实不在该来源"
                : "尚未核实成员"}
          </p>
          <dl className="media-facts">
            {ruleFields
              .filter((field) => field.kind !== "text")
              .map((field) => {
                const fact = context.fields[field.value];
                const available = fact?.available && fact.value !== null;
                const value = !available
                  ? "未知（来源未提供可用值）"
                  : field.kind === "boolean"
                    ? fact.value
                      ? "是"
                      : "否"
                    : field.kind === "date"
                      ? new Date(String(fact.value)).toLocaleString("zh-CN", {
                          hour12: false,
                        })
                      : String(fact.value);
                return (
                  <div key={field.value}>
                    <dt>{field.label}</dt>
                    <dd>{value}</dd>
                  </div>
                );
              })}
          </dl>
        </div>
      )}
      {!results.length && <p className="muted">这次预览没有可试算的分 P。</p>}
      {results.map((result) => (
        <article key={result.cid} className="media-rule-result">
          <p>
            <strong>分 P {result.cid}</strong> ·{" "}
            {result.decision === "download"
              ? "匹配"
              : result.decision === "skip"
                ? "跳过"
                : "试算失败"}
          </p>
          <p
            className={
              result.requires_rule_attention ? "media-warning" : "muted"
            }
          >
            {reasonWords[result.reason] ?? result.reason}
          </p>
          <DetailCard title="查看逐条规则解释">
            {groups(result.trace).length ? (
              groups(result.trace).map((group, index) => (
                <div className="media-rule-trace" key={group.group_id}>
                  <p>
                    <strong>
                      {group.list_kind === "blacklist" ? "黑名单" : "白名单"}组{" "}
                      {index + 1}
                    </strong>{" "}
                    · {reasonWords[group.result] ?? group.result}
                  </p>
                  <p className="muted">
                    {reasonWords[group.reason] ?? group.reason}
                  </p>
                  <ol>
                    {(group.rules ?? []).map((rule) => (
                      <li key={rule.rule_id}>
                        {rule.negate ? "取反（NOT） · " : ""}
                        {ruleFields.find((field) => field.value === rule.field)
                          ?.label ?? rule.field}{" "}
                        · {ruleOperations[rule.op as Rule["op"]] ?? rule.op}：
                        {reasonWords[rule.result] ?? rule.result}（
                        {reasonWords[rule.reason] ?? rule.reason}）
                      </li>
                    ))}
                  </ol>
                </div>
              ))
            ) : (
              <p className="muted">没有需要逐条执行的规则。</p>
            )}
            <DetailCard title="诊断数据">
              <pre>{JSON.stringify(result.trace, null, 2)}</pre>
            </DetailCard>
          </DetailCard>
        </article>
      ))}
    </section>
  );
}
