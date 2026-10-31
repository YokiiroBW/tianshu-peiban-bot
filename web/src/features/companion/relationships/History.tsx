import type { Event } from "./api";

const kinds: Record<string, string> = {
  automatic: "互动结算",
  manual_adjustment: "人工调整",
  management: "关系管理",
  natural_decay: "自然衰减",
  source_correction: "来源纠正",
  legacy_import: "历史接管",
};
const operations: Record<string, string> = {
  set_binding: "修改关系与称呼",
  set_freeze: "修改冻结状态",
  adjust_affinity: "人工调整",
};
export function History({ items, more }: { items: Event[]; more: boolean }) {
  return (
    <section aria-label="好感原因历史">
      <h3>最近变化</h3>
      {!items.length ? (
        <p>暂无已记录的变化。</p>
      ) : (
        <ol className="relationship-history">
          {items.map((item) => (
            <li key={item.id}>
              <div>
                <strong>
                  {operations[item.operation ?? ""] ??
                    kinds[item.kind] ??
                    "关系变化"}
                </strong>
                <span>
                  {item.delta > 0 ? "+" : ""}
                  {item.delta}
                </span>
              </div>
              <time dateTime={item.at}>
                {new Date(item.at).toLocaleString("zh-CN")}
              </time>
              {item.reason && <p>{item.reason}</p>}
              {item.kind === "manual_adjustment" && !item.reason && (
                <p>这条历史记录未保留原因。</p>
              )}
              {!item.valid && <p>来源已失效，当前分数已由记忆服务重新核算。</p>}
              {item.outcome.startsWith("rejected") && <p>未计入好感。</p>}
            </li>
          ))}
        </ol>
      )}
      {more && <p>显示最近 20 条变化。</p>}
    </section>
  );
}
