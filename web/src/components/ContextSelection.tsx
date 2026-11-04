type Props = {
  selection: unknown;
  tokenUsage: unknown;
  providerUsage: unknown;
};

function show(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "未记录";
}

/** These are recorded request facts, not a judgement of the reply or user feedback. */
export function ContextSelection({ selection, tokenUsage, providerUsage }: Props) {
  return <section aria-label="本轮材料与用量">
    <h4>本轮实际材料</h4>
    {selection == null ? <p>本轮未记录选材清单；不能据此判断选材为空。</p> :
      Array.isArray(selection) && selection.length === 0 ? <p>本轮记录的选材清单为空。</p> :
      Array.isArray(selection) ? <ol>{selection.map((item, index) => <li key={index}><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{show(item)}</pre></li>)}</ol> : <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{show(selection)}</pre>}
    <p className="muted">清单中的选择、舍弃和原因以原记录为准；进入上下文不代表回复已送达或用户认可。</p>
    <details><summary>上下文预算记录</summary>{tokenUsage == null ? <p>本轮未记录上下文预算。</p> : <pre>{show(tokenUsage)}</pre>}</details>
    <details><summary>供应商实际用量</summary>{providerUsage == null ? <p>供应商用量未记录，不能按零计算。</p> : <pre>{show(providerUsage)}</pre>}</details>
    <p className="muted">本记录未提供明确用户反馈，不能根据没有投诉推断认可。</p>
  </section>;
}
