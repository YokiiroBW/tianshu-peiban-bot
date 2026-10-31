import { useState } from "react";
import { useDomainResource } from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import { SourceEvidence } from "../../components/SourceEvidence";
import type { Episode } from "./domainTypes";
import { timeLabel } from "./domainTypes";

export function EpisodeTimeline() {
  const [cursor, setCursor] = useState<string | null>(null);
  const page = useDomainResource<Page<Episode> & { watermark: string }>(
    "memory/episodes",
    { cursor, limit: 40 },
  );
  return (
    <section className="life-card life-experiences">
      <div className="section-heading">
        <div>
          <h3 className="life-card-title">已发生经历</h3>
          <p className="muted">实际输入、观察与执行结果；主观认识另外保留。</p>
        </div>
        <button className="button" onClick={() => void page.refresh()}>
          刷新经历
        </button>
      </div>
      {page.error && <p role="alert">{page.error}</p>}
      {page.loading && !page.data && <p>正在读取经历…</p>}
      <ol className="life-timeline">
        {page.data?.items.map((item) => (
          <li key={`${item.episode_id}:${item.revision}`}>
            <time>{timeLabel(item.occurred_at)}</time>
            <div>
              <strong>{item.summary || "一段多模态输入"}</strong>
              <p className="muted">
                {(
                  {
                    human: "实际发言",
                    observation: "实际观察",
                    tool_result: "软件执行结果",
                    actor: "角色的表达与生活",
                  } as Record<string, string>
                )[item.origin_kind] ?? item.origin_kind}
              </p>
              {item.coverage && !item.coverage.complete && (
                <p>
                  记录范围有缺口：
                  {item.coverage.missing.join("、") || "覆盖尚未完整确认"}
                </p>
              )}
              {item.extraction_state && (
                <small>记忆整理：{item.extraction_state}</small>
              )}
              <SourceEvidence sources={item.source_refs} />
            </div>
          </li>
        ))}
      </ol>
      {page.data && !page.data.items.length && (
        <p className="muted">当前可读范围还没有经历。</p>
      )}
      <div className="domain-actions">
        {cursor && (
          <button className="button" onClick={() => setCursor(null)}>
            回到首批
          </button>
        )}
        {page.data?.next_cursor && (
          <button
            className="button"
            onClick={() => setCursor(page.data!.next_cursor)}
          >
            继续读取经历
          </button>
        )}
      </div>
    </section>
  );
}
