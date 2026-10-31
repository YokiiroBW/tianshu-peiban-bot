import { useState, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { Drawer } from "./Drawer";

/** A compact entry for settings or secondary detail, with content in a modal card. */
export function DetailCard({
  title,
  label,
  children,
  className = "",
  open: controlledOpen,
  onOpenChange,
}: {
  title: ReactNode;
  label?: ReactNode;
  children: ReactNode;
  className?: string;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const [localOpen, setOpen] = useState(false);
  const open = controlledOpen ?? localOpen;
  const change = (value: boolean) => {
    setOpen(value);
    onOpenChange?.(value);
  };
  return (
    <div className={`detail-card ${className}`}>
      <button
        type="button"
        className="button detail-card-trigger"
        aria-haspopup="dialog"
        onClick={() => change(true)}
      >
        <span>{label ?? title}</span>
        <ChevronRight aria-hidden="true" />
      </button>
      <Drawer
        title={title}
        variant="card"
        open={open}
        onClose={() => change(false)}
      >
        {open && <div className="detail-card-content">{children}</div>}
      </Drawer>
    </div>
  );
}
