import { useState } from "react";
import {
  useDomainCommand,
  useDomainResource,
  type DomainTarget,
} from "../../api/useDomainResource";
import { Drawer } from "../../components/Drawer";
import { SourceEvidence } from "../../components/SourceEvidence";
import { ArtifactList, MediaPreview } from "../../components/MediaPreview";
import { CommandFeedback } from "../../components/CommandFeedback";
import { confirmed } from "./types";
import type { DocumentRow, DocumentRead, Original, ReadUnitRef } from "./types";

export function DocumentReader({
  projectId,
  document,
  scope,
  onClose,
  onEvidence,
  canRemove = false,
  onChanged,
}: {
  projectId: string;
  document: DocumentRow;
  scope: DomainTarget;
  onClose: () => void;
  onEvidence: (value: ReadUnitRef) => void;
  canRemove?: boolean;
  onChanged?: () => Promise<void>;
}) {
  const [page, setPage] = useState<{
    cursor: string | null;
    position?: number;
  }>({ cursor: null });
  const [showOriginal, setShowOriginal] = useState(false);
  const view = useDomainResource<DocumentRead>("projects/documents/read", {
    ...scope,
    project_id: projectId,
    document_id: document.document_id,
    version: document.version,
    expected_hash: document.sha256,
    ...page,
  });
  const original = useDomainResource<Original>(
    "projects/documents/original",
    {
      ...scope,
      project_id: projectId,
      document_id: document.document_id,
      version: document.version,
    },
    showOriginal,
  );
  const command = useDomainCommand(
    `document-remove:${document.document_id}`,
    "actor",
  );
  return (
    <Drawer title={document.locator} open onClose={onClose}>
      <p className="muted">
        版本 {document.version} · {document.kind} ·
        实际读取范围与模型分析分别显示。
      </p>
      {view.loading && <p>正在核对版本与原件…</p>}
      {view.error && <p role="alert">{view.error}</p>}
      {view.data && (
        <section className="knowledge-reader">
          <small>
            {view.data.freshness.state} ·{" "}
            {view.data.spans
              .map((span) => `${span.unit} ${span.start}–${span.end}`)
              .join("、")}
          </small>
          <p style={{ whiteSpace: "pre-wrap" }}>{view.data.text}</p>
          <ArtifactList artifacts={view.data.artifacts} scope={scope} />
          {view.data.consumption && (
            <p className="muted">
              实际提取：{JSON.stringify(view.data.consumption.coverage)} ·{" "}
              {view.data.consumption.missing.join("、") || "本次提取未记录缺口"}{" "}
              · 未调用模型分析。
            </p>
          )}
          <SourceEvidence sources={view.data.source_refs} scope={scope} />
          <div className="knowledge-actions">
            <button
              className="button"
              onClick={() => onEvidence(view.data!.reference)}
            >
              选为笔记/经验依据
            </button>
            {view.data.next_position != null && (
              <button
                className="button"
                onClick={() =>
                  setPage({ ...page, position: view.data!.next_position })
                }
              >
                继续本单元文字
              </button>
            )}
            {view.data.next_cursor && (
              <button
                className="button"
                onClick={() => setPage({ cursor: view.data!.next_cursor })}
              >
                下一个完整单元
              </button>
            )}
            {(page.cursor || page.position) && (
              <button
                className="button"
                onClick={() => setPage({ cursor: null })}
              >
                回到首单元
              </button>
            )}
          </div>
        </section>
      )}
      <details onToggle={(event) => setShowOriginal(event.currentTarget.open)}>
        <summary>查看原件</summary>
        {original.error && <p role="alert">{original.error}</p>}
        {original.data?.media && (
          <MediaPreview media={original.data.media} scope={scope} />
        )}
      </details>
      {canRemove && (
        <div className="knowledge-editor">
          <p className="muted">
            移除本项目的登记版本，不修改外部项目文件。旧版本引用会失效。
          </p>
          <button
            className="button"
            disabled={command.busy}
            onClick={async () => {
              if (
                confirmed(
                  await command.run("projects/documents/remove", {
                    ...scope,
                    project_id: projectId,
                    document_id: document.document_id,
                    expected_version: document.version,
                  }),
                )
              ) {
                await onChanged?.();
                onClose();
              }
            }}
          >
            移除此资料登记
          </button>
          <CommandFeedback {...command} />
        </div>
      )}
    </Drawer>
  );
}
