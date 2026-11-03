import {
  useDomainResource,
  useDomainTarget,
} from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import { SourceEvidence } from "../../components/SourceEvidence";
import { CurioSectionHead } from "../../components/curio/OrientalWidgets";
import { sanitizeInternalText } from "../../design/humanize";
import type { Episode } from "./domainTypes";
import { timeLabel } from "./domainTypes";
import { useMemoryCursor } from "../memory/pagination";
import { CoverageNote } from "../memory/EvidenceDetails";
import { EpisodeProcessing } from "../memory/ConsolidationStatus";
import { episodeOrigin } from "../memory/types";

export function EpisodeTimeline() {
  const scope = useDomainTarget();
  const cursor = useMemoryCursor(scope, "episodes");
  const page = useDomainResource<Page<Episode> & { watermark: string }>(
    "memory/episodes",
    { ...scope, cursor: cursor.cursor, limit: 40 },
  );

  return (
    <section className="curio-card life-card life-experiences">
      <CurioSectionHead
        seal="忆"
        title={<span className="life-card-title">经历记录</span>}
        subtitle="人类输入、观察、执行结果和角色活动保留各自来源与覆盖。"
        action={
          <button
            className="button curio-btn"
            disabled={page.loading}
            onClick={() => void page.refresh()}
          >
            ↻ 刷新经历
          </button>
        }
      />
      {page.error && <p role="alert">{sanitizeInternalText(page.error)}</p>}
      {page.loading && !page.data && <p role="status">正在读取经历…</p>}
      {page.data && <CoverageNote coverage={page.data.coverage} />}

      <ol className="life-timeline curio-episode-grid">
        {page.data?.items.map((item) => (
          <li
            key={`${item.episode_id}:${item.revision}`}
            className="curio-episode-card"
          >
            <header className="curio-episode-head">
              <time className="timeline-time">
                {timeLabel(item.occurred_at)}
              </time>
              <span className="celadon-tag">{episodeOrigin(item)}</span>
            </header>
            <div className="curio-episode-body">
              <strong className="curio-episode-summary">
                {item.summary || "一段多模态输入"}
              </strong>
              <CoverageNote coverage={item.coverage} />
              <EpisodeProcessing
                episodeId={item.episode_id}
                revision={item.revision}
                initial={item.consolidation}
                extractionState={item.extraction_state}
                scope={scope}
                refresh={page.refresh}
              />
              <SourceEvidence sources={item.source_refs} scope={scope} />
            </div>
          </li>
        ))}
      </ol>

      {page.data && !page.data.items.length && (
        <p className="muted">当前可读范围还没有经历。</p>
      )}
      <div className="domain-actions">
        {cursor.previous && (
          <button
            className="button curio-btn"
            disabled={page.loading}
            onClick={cursor.back}
          >
            上一批经历
          </button>
        )}
        {page.data?.next_cursor && (
          <button
            className="button curio-btn"
            disabled={page.loading}
            onClick={() => cursor.next(page.data!.next_cursor!)}
          >
            继续读取经历
          </button>
        )}
      </div>
    </section>
  );
}
