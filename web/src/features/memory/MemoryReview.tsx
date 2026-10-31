import { useState } from "react";

type ReviewEntry = {
  semantic_group_id: string;
  text: string;
  truncated: boolean;
  effective_at: string | null;
  state: string | null;
  valid_until: string | null;
  overdue?: boolean;
};

export type MemoryReviewData = {
  periods: Record<string, { count: number; items: ReviewEntry[] }>;
  open_agreements: { count: number; items: ReviewEntry[] };
  portrait: ReviewEntry | null;
  truncated: boolean;
};

const states: Record<string, string> = {
  considering: "考虑中",
  confirmed: "已确认",
  unknown: "进展未明",
};

function Entry({ item }: { item: ReviewEntry }) {
  return (
    <li>
      {item.effective_at && (
        <small>{new Date(item.effective_at).toLocaleDateString("zh-CN")}</small>
      )}
      <p>
        {item.text}
        {item.truncated && "…"}
      </p>
      {item.state && states[item.state] && <small>{states[item.state]}</small>}
      {item.valid_until && (
        <p className="muted">
          约定期限：{new Date(item.valid_until).toLocaleString("zh-CN")}
        </p>
      )}
      {item.overdue && (
        <p className="muted">约定日期已过，完成情况仍需确认。</p>
      )}
    </li>
  );
}

export default function MemoryReview({ review }: { review: MemoryReviewData }) {
  const [days, setDays] = useState("7");
  const period = review.periods[days];
  return (
    <section className="memory-review" aria-label="记忆回顾">
      <p className="muted">
        回顾已保存的经历与表达，查看尚未结束的约定。
        {review.truncated && "本次只核验了部分记录，回顾和数量可能不完整。"}
      </p>
      <div className="memory-review-grid">
        <section>
          <div className="section-heading">
            <h3>近期经历与表达</h3>
            <label>
              <span className="sr-only">回顾时间范围</span>
              <select
                aria-label="回顾时间范围"
                value={days}
                onChange={(event) => setDays(event.target.value)}
              >
                <option value="7">最近 7 天</option>
                <option value="30">最近 30 天</option>
              </select>
            </label>
          </div>
          <p className="muted">
            共 {period?.count ?? 0} 条，展示最近 {period?.items.length ?? 0}{" "}
            条。
          </p>
          {period?.items.length ? (
            <ul>
              {period.items.map((item) => (
                <Entry key={item.semantic_group_id} item={item} />
              ))}
            </ul>
          ) : (
            <p>这个时间范围暂无可回顾的内容。</p>
          )}
        </section>
        <section>
          <h3>尚未结束的约定</h3>
          <p className="muted">
            共 {review.open_agreements.count} 条；完成或取消后会移出这里。
          </p>
          {review.open_agreements.items.length ? (
            <ul>
              {review.open_agreements.items.map((item) => (
                <Entry key={item.semantic_group_id} item={item} />
              ))}
            </ul>
          ) : (
            <p>本次核验范围没有未结束的约定。</p>
          )}
        </section>
        <section>
          <h3>当前人物画像</h3>
          {review.portrait ? (
            <ul>
              <Entry item={review.portrait} />
            </ul>
          ) : (
            <p>尚未形成有效画像。</p>
          )}
          <p className="muted">画像是可修订的归纳，具体来源见下方记忆条目。</p>
        </section>
      </div>
    </section>
  );
}
