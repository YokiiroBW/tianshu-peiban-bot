import { useState } from "react";
import { useDomainResource, type DomainTarget } from "../api/useDomainResource";
import type { Coverage } from "../api/contracts";
import { MediaPreview, type MediaRef } from "./MediaPreview";

export type SourceRef = {
  source_id: string;
  revision: number;
  locator?: Record<string, unknown>;
};
type Projection = {
  content: string | null;
  representation: string;
  coverage: Coverage;
  next_position: number | null;
  media: MediaRef | null;
};
function Evidence({
  source,
  scope,
}: {
  source: SourceRef;
  scope?: DomainTarget;
}) {
  const [open, setOpen] = useState(false);
  const start =
    typeof source.locator?.start === "number" ? source.locator.start : 0;
  const [position, setPosition] = useState(start);
  const view = useDomainResource<Projection>(
    "sources/read",
    {
      ...(scope
        ? {
            actor_id: scope.actor_id,
            person_id: scope.person_id ?? null,
            conversation_id: scope.conversation_id ?? null,
          }
        : {}),
      source_ref: source,
      requested: { position, max_characters: 2400 },
    },
    open,
  );
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>查看原文依据 · 修订 {source.revision}</summary>
      {view.loading && <p>正在复核并读取原文…</p>}
      {view.error && <p role="alert">{view.error}</p>}
      {view.data && (
        <>
          {view.data.content && (
            <blockquote style={{ whiteSpace: "pre-wrap" }}>
              {view.data.content}
            </blockquote>
          )}
          {view.data.media && (
            <MediaPreview media={view.data.media} scope={scope} />
          )}
          <p className="muted">
            表示方式：{view.data.representation} ·{" "}
            {view.data.coverage.complete
              ? "本次范围已取齐"
              : `读取范围存在缺口：${view.data.coverage.missing.join("、") || "还有后续材料"}`}
          </p>
          <div className="domain-actions">
            {position !== start && (
              <button
                type="button"
                className="text-link"
                onClick={() => setPosition(start)}
              >
                回到本段开头
              </button>
            )}
            {view.data.next_position !== null && (
              <button
                type="button"
                className="text-link"
                onClick={() => setPosition(view.data!.next_position!)}
              >
                读取下一段原文
              </button>
            )}
          </div>
        </>
      )}
    </details>
  );
}
export function SourceEvidence({
  sources,
  scope,
}: {
  sources: SourceRef[];
  scope?: DomainTarget;
}) {
  return sources.length ? (
    <div className="source-evidence">
      {sources.map((source) => (
        <Evidence key={JSON.stringify(source)} source={source} scope={scope} />
      ))}
    </div>
  ) : (
    <p className="muted">没有提供可展开的原文依据。</p>
  );
}
