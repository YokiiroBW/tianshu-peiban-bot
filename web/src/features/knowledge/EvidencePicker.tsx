import { useState } from "react";
import {
  useDomainResource,
  type DomainTarget,
} from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import { DocumentReader } from "./DocumentReader";
import type { DocumentRow, ReadUnitRef } from "./types";

export const unitKey = (value: ReadUnitRef) => JSON.stringify(value);
export const unitLabel = (value: ReadUnitRef) =>
  `已读资料单元 · 版本 ${value.version}`;

/** References are selected only after an actual versioned document read. */
export function EvidencePicker({
  projectId,
  scope,
  values,
  onChange,
}: {
  projectId: string;
  scope: DomainTarget;
  values: ReadUnitRef[];
  onChange: (values: ReadUnitRef[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<DocumentRow | null>(null);
  const docs = useDomainResource<Page<DocumentRow>>(
    "projects/documents",
    { ...scope, project_id: projectId, cursor, limit: 24 },
    open,
  );
  return (
    <fieldset className="knowledge-evidence">
      <legend>已读原件单元</legend>
      <p className="muted">
        先打开资料并读取单元，再选择依据；保存时会重新核对版本与来源。
      </p>
      {values.length === 0 && <p>尚未选取依据。</p>}
      <ul>
        {values.map((value) => (
          <li key={unitKey(value)}>
            <span>{unitLabel(value)}</span>
            <button
              type="button"
              className="button"
              onClick={() =>
                onChange(
                  values.filter((item) => unitKey(item) !== unitKey(value)),
                )
              }
            >
              移除选择
            </button>
          </li>
        ))}
      </ul>
      <button type="button" className="button" onClick={() => setOpen(!open)}>
        {open ? "收起资料选择" : "打开资料选择依据"}
      </button>
      {open && (
        <div className="knowledge-evidence-docs">
          {docs.error && <p role="alert">{docs.error}</p>}
          {docs.loading && <p>正在读取资料目录…</p>}
          {docs.data?.items.map((row) => (
            <button
              type="button"
              className="button"
              key={row.document_id}
              onClick={() => setSelected(row)}
            >
              {row.locator} · v{row.version}
            </button>
          ))}
          {docs.data?.next_cursor && (
            <button
              type="button"
              className="button"
              onClick={() => setCursor(docs.data!.next_cursor)}
            >
              下一页资料
            </button>
          )}
          {cursor && (
            <button
              type="button"
              className="button"
              onClick={() => setCursor(null)}
            >
              回到第一页
            </button>
          )}
          {docs.data && !docs.data.items.length && <p>本页没有可读资料。</p>}
        </div>
      )}
      {selected && (
        <DocumentReader
          key={selected.document_id + ":" + selected.version}
          projectId={projectId}
          document={selected}
          scope={scope}
          onClose={() => setSelected(null)}
          onEvidence={(value) => {
            if (!values.some((item) => unitKey(item) === unitKey(value)))
              onChange([...values, value]);
            setSelected(null);
          }}
        />
      )}
    </fieldset>
  );
}
