import { useState } from "react";
import { useDomainResource, type DomainTarget } from "../api/useDomainResource";
import type { Coverage } from "../api/contracts";
import { MediaPreview, type MediaRef } from "./MediaPreview";
import {
  humanizeSourceKind,
  sanitizeInternalText,
} from "../design/humanize";

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

const representationLabels: Record<string, string> = {
  text: "正文原件",
  transcript: "语音转写",
  summary: "内容摘要",
  metadata: "元数据记录",
  image: "图像原件",
  audio: "音频原件",
  video: "视频原件",
};

function describeSourceId(sourceId: string): string {
  const prefix = sourceId.split(/[_:]/)[0] ?? "";
  const kindLabel = humanizeSourceKind(prefix);
  return kindLabel;
}

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
  const readableLabel = describeSourceId(source.source_id);

  return (
    <details
      className="curio-evidence-item-details"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="curio-evidence-item-summary">
        <span className="celadon-tag">{readableLabel}</span>
        <span>查看原文依据 · 修订 {source.revision}</span>
      </summary>
      {view.loading && <p className="muted">正在复核并读取原文…</p>}
      {view.error && <p role="alert">{sanitizeInternalText(view.error)}</p>}
      {view.data && (
        <div className="curio-evidence-item-body">
          {view.data.content && (
            <blockquote
              className="curio-evidence-quote"
              style={{ whiteSpace: "pre-wrap" }}
            >
              {view.data.content}
            </blockquote>
          )}
          {view.data.media && (
            <MediaPreview media={view.data.media} scope={scope} />
          )}
          <p className="muted">
            表示方式：
            {representationLabels[view.data.representation] ??
              sanitizeInternalText(view.data.representation)}{" "}
            ·{" "}
            {view.data.coverage.complete
              ? "本次范围已取齐"
              : `读取范围存在缺口：${view.data.coverage.missing.map((m) => sanitizeInternalText(m)).join("、") || "还有后续材料"}`}
          </p>
          <div className="domain-actions">
            {position !== start && (
              <button
                type="button"
                className="text-link ink-link"
                onClick={() => setPosition(start)}
              >
                回到本段开头
              </button>
            )}
            {view.data.next_position !== null && (
              <button
                type="button"
                className="text-link ink-link"
                onClick={() => setPosition(view.data!.next_position!)}
              >
                读取下一段原文
              </button>
            )}
          </div>
        </div>
      )}
    </details>
  );
}

/**
 * 原文凭据聚合卷轴栏 (Curio Evidence Scroll Bar)
 * 当单条手记/经历关联多条 source_refs 时，主视图聚合成单条「📜 原文凭据：共 N 处对话与观测记录 · 核对原文依据 ▾」，
 * 彻底消除十几行重复「▶ 查看原文依据 · 修订 1」刷屏现象。
 */
export function SourceEvidence({
  sources,
  scope,
}: {
  sources: SourceRef[];
  scope?: DomainTarget;
}) {
  const [groupOpen, setGroupOpen] = useState(false);

  if (!sources?.length) {
    return <p className="muted">没有提供可展开的原文依据。</p>;
  }

  // 对完全相同的 source_ref 去重
  const uniqueSources = sources.filter(
    (item, idx, arr) =>
      arr.findIndex(
        (other) =>
          other.source_id === item.source_id &&
          other.revision === item.revision &&
          JSON.stringify(other.locator ?? null) ===
            JSON.stringify(item.locator ?? null),
      ) === idx,
  );

  if (uniqueSources.length === 1) {
    return (
      <div className="source-evidence curio-evidence-container">
        <Evidence source={uniqueSources[0]} scope={scope} />
      </div>
    );
  }

  return (
    <div className="source-evidence curio-evidence-container">
      <div className="evidence-bar">
        <span className="evidence-bar-summary">
          📜 原文凭据：共 {uniqueSources.length} 处关联记录
        </span>
        <button
          type="button"
          className="ink-link evidence-toggle-btn"
          aria-expanded={groupOpen}
          onClick={() => setGroupOpen((prev) => !prev)}
        >
          {groupOpen
            ? "收起原文依据 ▴"
            : `核对原文依据 (${uniqueSources.length}) ▾`}
        </button>
      </div>
      {groupOpen && (
        <div
          className="curio-evidence-drawer"
          role="region"
          aria-label="原文依据列表"
        >
          {uniqueSources.map((source) => (
            <Evidence
              key={JSON.stringify(source)}
              source={source}
              scope={scope}
            />
          ))}
        </div>
      )}
    </div>
  );
}
