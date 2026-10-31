import { useAuth } from "../../app/Auth";
import { useState } from "react";
import {
  useDomainCommand,
  useDomainResource,
  type DomainTarget,
} from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import { SourceEvidence } from "../../components/SourceEvidence";
import { CommandFeedback } from "../../components/CommandFeedback";
import { useMemoryCursor } from "./pagination";
import { time, type Episode, type Reflection } from "./types";

export default function MemoryReview({
  scope,
  personId,
}: {
  scope: DomainTarget;
  personId?: string | null;
}) {
  const { session } = useAuth();
  const manageable = session?.principal?.permissions.some(
    (value) => value === "*" || value === "memory.manage",
  );
  const [collection, setCollection] = useState<"episodes" | "reflections">(
    "episodes",
  );
  const cursor = useMemoryCursor(scope, collection);
  const view = useDomainResource<Page<Episode | Reflection>>(
    `memory/${collection}`,
    {
      ...scope,
      person_id: personId ?? scope.person_id ?? null,
      cursor: cursor.cursor,
      limit: 40,
    },
    !!scope.actor_id,
  );
  const command = useDomainCommand<unknown>("memory-extraction", "actor");
  return (
    <section className="memory-review">
      <div className="section-heading">
        <h3>真实经历与反思</h3>
        <button className="button" onClick={() => void view.refresh()}>
          刷新
        </button>
      </div>
      <div className="models-actions">
        <button
          className="button"
          aria-pressed={collection === "episodes"}
          onClick={() => setCollection("episodes")}
        >
          实际经历
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
        经历保留实际执行结果；反思是带依据的解释和推测，不等于人物事实。
      </p>
      {view.error && <p role="alert">{view.error}</p>}
      {view.loading && <p>正在核验来源…</p>}
      {view.data && (
        <>
          <p className="muted">
            {view.data.coverage.complete
              ? "本批范围已读完"
              : "本批范围存在后续或不可用来源"}
          </p>
          {view.data.items.length === 0 && <p>本批没有可显示的内容。</p>}
          <ul>
            {view.data.items.map((item) => {
              if ("episode_id" in item)
                return (
                  <li key={`${item.episode_id}:${item.revision}`}>
                    <small>
                      {time(item.occurred_at)} · {item.origin_kind} · 修订{" "}
                      {item.revision}
                    </small>
                    <p style={{ whiteSpace: "pre-wrap" }}>{item.summary}</p>
                    <p>
                      提取状态：{item.extraction_state} ·{" "}
                      {item.coverage.complete
                        ? "已覆盖本次范围"
                        : "覆盖存在缺口"}
                    </p>
                    {item.actual_result && (
                      <details>
                        <summary>实际结果与覆盖证据</summary>
                        <pre>{JSON.stringify(item.actual_result, null, 2)}</pre>
                      </details>
                    )}
                    <SourceEvidence sources={item.source_refs} scope={scope} />
                    {manageable &&
                      ["failed", "unknown", "interrupted"].includes(
                        item.extraction_state,
                      ) && (
                        <button
                          className="text-link"
                          disabled={command.busy}
                          onClick={async () => {
                            if (
                              await command.run(
                                "memory/extraction/resume",
                                { ...scope, episode_id: item.episode_id },
                                item.revision,
                              )
                            )
                              await view.refresh();
                          }}
                        >
                          显式继续这次提取
                        </button>
                      )}
                  </li>
                );
              return (
                <li key={`${item.proposal_id}:${item.revision}`}>
                  <small>
                    {time(item.occurred_at)} · {item.kind} · 解释性记录
                  </small>
                  <p style={{ whiteSpace: "pre-wrap" }}>{item.content}</p>
                  {item.counterexamples.length > 0 && (
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
              );
            })}
          </ul>
        </>
      )}
      <div className="models-actions">
        {cursor.previous && (
          <button className="button" onClick={cursor.back}>
            上一批
          </button>
        )}
        {view.data?.next_cursor && (
          <button
            className="button"
            onClick={() => cursor.next(view.data!.next_cursor!)}
          >
            下一批
          </button>
        )}
      </div>
      <CommandFeedback {...command} />
    </section>
  );
}
