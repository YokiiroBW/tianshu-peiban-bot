import { useState } from "react";
import { useDomainResource } from "../api/useDomainResource";
import type { Coverage } from "../api/contracts";
import { MediaPreview, type MediaRef } from "./MediaPreview";

export type SourceRef = { source_id: string; revision: number; locator?: Record<string, unknown> };
type Projection = { content: string | null; representation: string; coverage: Coverage; next_position: object | null; media: MediaRef | null };
function Evidence({ source }: { source: SourceRef }) {
  const [open, setOpen] = useState(false);
  const view = useDomainResource<Projection>("sources/read", { source_ref: source, requested: { position: 0, max_characters: 2400 } }, open);
  return <details onToggle={(event) => setOpen(event.currentTarget.open)}><summary>查看原文依据 · 修订 {source.revision}</summary>
    {view.loading && <p>正在复核并读取原文…</p>}{view.error && <p role="alert">{view.error}</p>}
    {view.data && <>{view.data.content && <blockquote style={{ whiteSpace: "pre-wrap" }}>{view.data.content}</blockquote>}{view.data.media && <MediaPreview media={view.data.media} />}
      <p className="muted">表示方式：{view.data.representation} · {view.data.coverage.complete ? "本次范围已取齐" : `读取范围存在缺口：${view.data.coverage.missing.join("、") || "还有后续材料"}`}</p></>}
  </details>;
}
export function SourceEvidence({ sources }: { sources: SourceRef[] }) {
  return sources.length ? <div className="source-evidence">{sources.map((source) => <Evidence key={`${source.source_id}:${source.revision}`} source={source} />)}</div> : <p className="muted">没有提供可展开的原文依据。</p>;
}
