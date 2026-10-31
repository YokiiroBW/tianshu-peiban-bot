import type { Episode, InterestItem, MemorySource } from "./actorMemoryTypes";
import { showState } from "./runtimeApi";

export const memoryLabels: Record<string, string> = {
  episode: "经历",
  understanding: "认识",
  preference: "偏好",
  reflection: "反思",
  autobiography: "自传",
  active: "当前有效",
  superseded: "已被替代",
  expired: "已淡出",
  withdrawn: "已撤回",
  unavailable: "来源不可用",
  like: "喜欢",
  dislike: "不喜欢",
  mixed: "复杂感受",
  curious: "好奇",
  unspecified: "未标注倾向",
  digital_action: "实际数字行动",
  observation: "旁听记录",
  reported: "转述",
  fictional: "虚构剧情",
  participant: "参与者",
  observer: "观察者",
  informed: "获知者",
  observed: "已观察",
};
export const memoryLabel = (value: string) =>
  memoryLabels[value] ?? showState(value);
export const memoryTime = (value: string | null) =>
  value ? new Date(value).toLocaleString("zh-CN") : "时间未确认";

export function ActorSources({ sources }: { sources: MemorySource[] }) {
  return (
    <details className="life-evidence">
      <summary>记忆来源（{sources.length}）</summary>
      <ul>
        {sources.map((source) => (
          <li key={`${source.producer}:${source.object_id}:${source.revision}`}>
            {source.producer} · {source.object_id} · 版本 {source.revision}
          </li>
        ))}
      </ul>
    </details>
  );
}

export function EpisodeEvidence({ episode }: Episode) {
  const fact = episode.fact;
  return (
    <section className="life-record" aria-label="经历详情">
      <h4>真实记录与阅读范围</h4>
      <p>
        {memoryLabel(fact.provenance)} · {memoryLabel(fact.relation)} ·{" "}
        {memoryLabel(fact.outcome)}
      </p>
      <p>{fact.summary}</p>
      <p className="muted">
        发生于 {memoryTime(fact.occurred_at)} · 获知于{" "}
        {memoryTime(fact.learned_at)}
      </p>
      {fact.content_reads.length ? (
        fact.content_reads.map((read) => (
          <div
            key={`${read.content_ref.object_id}:${read.content_ref.revision}`}
          >
            <ActorSources sources={[read.content_ref]} />
            <ul>
              {read.coverage.map((range, index) => (
                <li key={index}>
                  {range.start}–{range.end}{" "}
                  {(
                    {
                      characters: "字符",
                      pages: "页",
                      seconds: "秒",
                      items: "项",
                    } as Record<string, string>
                  )[range.unit] ?? range.unit}
                  {" · "}
                  {range.complete ? "此范围完整" : "部分读取"}
                  {range.gaps.length > 0 && ` · 缺口：${range.gaps.join("、")}`}
                </li>
              ))}
            </ul>
          </div>
        ))
      ) : (
        <p className="muted">本经历没有实读范围记录，不代表看过完整原文。</p>
      )}
    </section>
  );
}

export function InterestEvidence({ item }: { item: InterestItem }) {
  return (
    <article className="life-record">
      <h4>
        {item.topic || "未标注主题"} · {memoryLabel(item.stance)}
      </h4>
      <p>{item.text}</p>
      <p className="muted">独立依据 {item.support_count} 项</p>
      <ActorSources sources={item.source_refs} />
      <details>
        <summary>兴趣变化（{item.history.length}）</summary>
        <ul>
          {item.history.map((row) => (
            <li key={`${row.ref.id}:${row.ref.revision}`}>
              {memoryTime(row.recorded_at)} · {memoryLabel(row.stance)} ·{" "}
              {memoryLabel(row.state)}
            </li>
          ))}
        </ul>
        {item.history_omitted && (
          <p className="muted">还有未在本次预算内展示的变化。</p>
        )}
      </details>
    </article>
  );
}
