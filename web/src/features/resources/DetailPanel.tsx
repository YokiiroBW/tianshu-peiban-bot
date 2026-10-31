import { FileText, RotateCcw, X } from "lucide-react";
import { StatusRail } from "../../components/StatusRail";
import {
  kindLabel,
  libraryState,
  modifiedText,
  sizeText,
  type Entry,
  type EntryDetail,
  type Library,
} from "./api";

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="asset-field">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

/**
 * The published read protocol has no media port, so the page states that in its own words next to
 * the reason the server gives: no thumbnail, no preview, no download — instead of a control that
 * could not work. This is the only capability claim the page makes about itself.
 */
function NoPreview({ reason }: { reason: string }) {
  return (
    <p className="asset-note" role="note">
      <RotateCcw aria-hidden="true" />
      <span>
        <strong className="asset-note-label">暂不提供预览</strong>
        {reason}
      </span>
    </p>
  );
}

/** The library the listing belongs to: its authorized word for itself, and nothing more. */
export function LibrarySummary({ library }: { library: Library }) {
  const rail = libraryState(library);
  return (
    <StatusRail tone={rail.tone} label={rail.label}>
      <div className="asset-library">
        <p className="asset-library-name">{library.display_name}</p>
        <p className="asset-library-detail">{rail.detail}</p>
        <dl className="asset-library-fields">
          <Field label="库标识" value={library.library_id} />
          <Field
            label="索引"
            value={library.index_available ? "可用" : "离线"}
          />
          <Field
            label="原件"
            value={
              library.original_available === "not_verified"
                ? "未验证"
                : library.original_available
            }
          />
          <Field label="授权" value={library.access_level} />
        </dl>
      </div>
    </StatusRail>
  );
}

/**
 * The read-only detail of one entry.
 *
 * Everything here is what an authorized read returned: the authorized name, kind, size, modified
 * time, the origin state and the identifiers. The protocol has no preview or download port, so the
 * panel says so instead of offering a picture or a button that cannot work.
 */
export function DetailPanel({
  detail,
  preview,
  onClose,
}: {
  detail: EntryDetail | null;
  preview: { available: boolean; code: string; reason: string } | null;
  onClose: () => void;
}) {
  if (!detail) {
    return (
      <div className="asset-detail">
        <h2 className="asset-detail-title">条目详情</h2>
        <p className="asset-muted">
          在上面的列表里选择一个条目，这里显示它的只读信息。
        </p>
        {preview && !preview.available && <NoPreview reason={preview.reason} />}
      </div>
    );
  }
  const entry: Entry = detail.entry;
  return (
    <div className="asset-detail">
      <div className="asset-detail-head">
        <h2 className="asset-detail-title">条目详情</h2>
        <button type="button" className="button ghost" onClick={onClose}>
          <X aria-hidden="true" />
          关闭
        </button>
      </div>
      <p className="asset-detail-name">
        <FileText aria-hidden="true" />
        <span>{entry.name}</span>
      </p>
      <dl className="asset-fields">
        <Field label="类型" value={kindLabel(entry.kind, entry.directory)} />
        <Field
          label="大小"
          value={entry.directory ? "文件夹不适用" : sizeText(entry)}
        />
        <Field label="修改时间" value={modifiedText(entry)} />
        <Field label="相对路径" value={entry.relative_path} />
        <Field label="条目标识" value={entry.entry_id} />
        <Field label="库标识" value={entry.library_id} />
        <Field
          label="原件"
          value={
            entry.original_available === "not_verified"
              ? "未验证（只有索引元数据）"
              : entry.original_available
          }
        />
        <Field label="来源状态" value={libraryState(detail.library).label} />
      </dl>
      {!entry.preview.available && <NoPreview reason={entry.preview.reason} />}
    </div>
  );
}
