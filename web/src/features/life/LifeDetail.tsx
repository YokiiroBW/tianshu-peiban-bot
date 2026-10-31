import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { X, ArrowUpRight } from "lucide-react";

export type LifeDetail = {
  title: string;
  body: string;
  meta: string;
  status: string;
};

/** Keep the full source text; compact cards and previews never replace it. */
export function DetailTrigger({
  detail,
  children,
  onOpen,
  className = "",
}: {
  detail: LifeDetail;
  children: ReactNode;
  onOpen: (detail: LifeDetail) => void;
  className?: string;
}) {
  const id = useId();
  const [dismissed, setDismissed] = useState(false);
  return (
    <div
      className={`life-detail-trigger ${className}`}
      onMouseLeave={() => setDismissed(false)}
    >
      <button
        type="button"
        className="life-detail-button"
        aria-haspopup="dialog"
        aria-describedby={dismissed ? undefined : id}
        onFocus={() => setDismissed(false)}
        onKeyDown={(event) => {
          if (event.key === "Escape") setDismissed(true);
        }}
        onClick={() => {
          setDismissed(true);
          onOpen(detail);
        }}
      >
        {children}
      </button>
      {!dismissed && (
        <div id={id} role="tooltip" className="life-preview">
          <strong>{detail.title}</strong>
          <p>{detail.body}</p>
          <small>
            点击阅读完整内容 <ArrowUpRight size={13} aria-hidden="true" />
          </small>
        </div>
      )}
    </div>
  );
}

export function LifeDetailDialog({
  detail,
  onClose,
}: {
  detail: LifeDetail | null;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    if (detail) dialog.current?.showModal();
    else dialog.current?.close();
  }, [detail]);
  return (
    <dialog
      ref={dialog}
      className="life-detail-dialog"
      aria-labelledby={titleId}
      onClose={onClose}
      onClick={(event) => {
        if (event.target === event.currentTarget) dialog.current?.close();
      }}
    >
      {detail && (
        <article>
          <div className="life-dialog-heading">
            <span className="life-eyebrow">生活片段</span>
            <button
              type="button"
              className="life-icon-button"
              aria-label="关闭详情"
              autoFocus
              onClick={() => dialog.current?.close()}
            >
              <X aria-hidden="true" />
            </button>
          </div>
          <p className="life-dialog-meta">
            {detail.meta} · {detail.status}
          </p>
          <h2 id={titleId}>{detail.title}</h2>
          <p className="life-dialog-body">{detail.body}</p>
        </article>
      )}
    </dialog>
  );
}
