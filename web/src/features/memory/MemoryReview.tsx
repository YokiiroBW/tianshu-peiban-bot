import { useState } from "react";
import {
  useDomainResource,
  type DomainTarget,
} from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import { SourceEvidence } from "../../components/SourceEvidence";
import { useMemoryCursor } from "./pagination";
import { CoverageNote } from "./EvidenceDetails";
import { EpisodeProcessing } from "./ConsolidationStatus";
import {
  currentMemory,
  episodeOrigin,
  showValue,
  time,
  type Episode,
  type Reflection,
  type MemoryTimeQuery,
} from "./types";

export default function MemoryReview({
  scope,
  personId,
  query = currentMemory,
  identities = {},
}: {
  scope: DomainTarget;
  personId?: string | null;
  query?: MemoryTimeQuery;
  identities?: Record<string, string>;
}) {
  const [collection, setCollection] = useState<"episodes" | "reflections">(
    "episodes",
  );
  const cursor = useMemoryCursor(
    { ...scope, personId, ...(collection === "reflections" ? query : {}) },
    collection,
  );
  const view = useDomainResource<Page<Episode | Reflection>>(
    `memory/${collection}`,
    {
      ...scope,
      person_id: personId ?? scope.person_id ?? null,
      ...(collection === "reflections" ? query : {}),
      cursor: cursor.cursor,
      limit: 40,
    },
    !!scope.actor_id,
  );
  return (
    <section className="memory-review">
      <div className="section-heading">
        <h3>经历与反思</h3>
        <button
          className="button"
          disabled={view.loading}
          onClick={() => void view.refresh()}
        >
          刷新
        </button>
      </div>
      <div className="models-actions">
        <button
          className="button"
          aria-pressed={collection === "episodes"}
          onClick={() => setCollection("episodes")}
        >
          经历记录
        </button>
        <button
          className="button"
          aria-pressed={collection === "reflections"}
          onClick={() => setCollection("reflections")}
        >
          反思与推测
        </button>
      </div>
      <p className="muted">
        人类输入、观察、工具结果和角色主观活动保留各自来源。反思是可修订的解释，不等于人物事实。
      </p>
      {collection === "episodes" && query.time_mode !== "current" && (
        <p>
          经历列表显示当前可读原件和当前处理状态；时间选择用于事实、事项和反思。
        </p>
      )}
      {view.error && <p role="alert">{view.error}</p>}
      {view.loading && <p role="status">正在核验来源…</p>}
      {view.data && (
        <>
          <CoverageNote coverage={view.data.coverage} />
          {!view.data.items.length && <p>本批没有可显示的内容。</p>}
          <ul>
            {view.data.items.map((item) =>
              "episode_id" in item ? (
                <li key={`${item.episode_id}:${item.revision}`}>
                  <small>
                    {time(item.occurred_at)} · 修订 {item.revision} · 系统记录于{" "}
                    {time(item.recorded_at)}
                  </small>
                  <p className="memory-prewrap">{item.summary}</p>
                  <p className="muted">{episodeOrigin(item)}</p>
                  <p className="muted">
                    原作者：
                    {item.speaker_id
                      ? (identities[item.speaker_id] ?? item.speaker_id)
                      : "未知（来源未证明唯一作者）"}
                    {item.person_id
                      ? ` · 相关人物：${identities[item.person_id] ?? item.person_id}`
                      : ""}
                  </p>
                  <CoverageNote coverage={item.coverage} />
                  {item.extraction_coverage && (
                    <CoverageNote coverage={item.extraction_coverage} />
                  )}
                  <EpisodeProcessing
                    episodeId={item.episode_id}
                    revision={item.revision}
                    initial={item.consolidation}
                    extractionState={item.extraction_state}
                    scope={scope}
                    refresh={view.refresh}
                    identities={identities}
                  />
                  {item.actual_result && (
                    <details>
                      <summary>原记录与实际回执</summary>
                      <pre>{showValue(item.actual_result)}</pre>
                    </details>
                  )}
                  <SourceEvidence sources={item.source_refs} scope={scope} />
                </li>
              ) : (
                <li key={`${item.proposal_id}:${item.revision}`}>
                  <small>
                    {time(item.occurred_at)} · {item.kind} · 可修订的解释 ·
                    系统记录于 {time(item.recorded_at)}
                  </small>
                  <p className="memory-prewrap">{item.content}</p>
                  {!!Object.keys(item.conditions).length && (
                    <details>
                      <summary>适用条件</summary>
                      <pre>{showValue(item.conditions)}</pre>
                    </details>
                  )}
                  {!!item.counterexamples.length && (
                    <details>
                      <summary>反例与限制</summary>
                      <ul>
                        {item.counterexamples.map((text, index) => (
                          <li key={index}>{text}</li>
                        ))}
                      </ul>
                    </details>
                  )}
                  <SourceEvidence sources={item.source_refs} scope={scope} />
                </li>
              ),
            )}
          </ul>
        </>
      )}
      <div className="models-actions">
        {cursor.previous && (
          <button
            className="button"
            disabled={view.loading}
            onClick={cursor.back}
          >
            上一批
          </button>
        )}
        {view.data?.next_cursor && (
          <button
            className="button"
            disabled={view.loading}
            onClick={() => cursor.next(view.data!.next_cursor!)}
          >
            下一批
          </button>
        )}
      </div>
    </section>
  );
}
