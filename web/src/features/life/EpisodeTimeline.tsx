import { useState } from "react";
import { useDomainResource } from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import { SourceEvidence } from "../../components/SourceEvidence";
import type { Episode } from "./domainTypes";
import { timeLabel } from "./domainTypes";

const coverageReasons: Record<string, string> = {
  coverage_unknown: "本次记录没有提供素材覆盖范围，不能据此认定已读完或看完。",
  source_unavailable: "部分原始依据已不可用。",
  observation_analysis_unavailable: "观察分析尚未形成可核验的材料。",
};
const extractionStates: Record<string, string> = {
  no_original_material:
    "这条记录没有可用于整理的原始材料，未从中抽取人物事实。",
  pending: "等待整理",
  queued: "等待整理",
  running: "正在整理",
  extracting: "正在整理",
  completed: "本批整理已完成",
  completed_with_missing: "本批整理已结束，部分原始材料仍有缺失",
  complete: "本批整理已完成",
  extracted: "本批整理已完成",
  partial: "部分材料已整理，仍有后续内容",
  failed: "本次整理未完成",
  no_extractable_text: "当前材料没有可抽取的文字内容",
  source_unavailable: "原始依据已不可用",
};

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
                  {item.coverage.missing.length
                    ? item.coverage.missing
                        .map(
                          (reason) =>
                            coverageReasons[reason] ??
                            "部分素材的覆盖范围尚未确认。",
                        )
                        .join(" ")
                    : "覆盖范围尚未完整确认。"}
                </p>
              )}
              {item.extraction_state && (
                <small>
                  记忆整理：
                  {extractionStates[item.extraction_state] ??
                    "整理状态尚未确认"}
                </small>
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
