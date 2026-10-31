import { useEffect, useId, useRef } from "react";
import type { ReactNode } from "react";
import { X } from "lucide-react";

export function Drawer({
  title,
  open,
  onClose,
  children,
  variant = "drawer",
}: {
  title: ReactNode;
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  variant?: "drawer" | "card";
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current!;
    if (!open) {
      if (element.open) element.close();
      return;
    }
    const trigger =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    element.showModal();
    return () => {
      element.close();
      if (trigger?.isConnected) trigger.focus();
    };
  }, [open]);
  return (
    <dialog
      className={`drawer${variant === "card" ? " drawer-card" : ""}`}
      ref={dialog}
      aria-labelledby={titleId}
      onCancel={(event) => {
        if (event.target !== event.currentTarget) return;
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        if (
          event.clientX < bounds.left ||
          event.clientX > bounds.right ||
          event.clientY < bounds.top ||
          event.clientY > bounds.bottom
        )
          onClose();
      }}
      onKeyDown={(event) => {
        if ((event.target as Element).closest("dialog") !== event.currentTarget)
          return;
        if (event.key === "Escape") {
          event.stopPropagation();
          return;
        }
        if (event.key !== "Tab") return;
        const controls = Array.from(
          event.currentTarget.querySelectorAll<HTMLElement>(
            'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
          ),
        ).filter(
          (element) =>
            element.getClientRects().length > 0 && element.tabIndex >= 0,
        );
        const first = controls[0];
        const last = controls.at(-1);
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }}
    >
      {open && (
        <>
          <header className="drawer-heading">
            <h2 id={titleId}>{title}</h2>
            <button
              className="icon-button"
              type="button"
              aria-label={
                typeof title === "string" ? `关闭${title}` : "关闭卡片"
              }
              onClick={onClose}
            >
              <X aria-hidden="true" />
            </button>
          </header>
          <div className="drawer-content">{children}</div>
        </>
      )}
    </dialog>
  );
}
