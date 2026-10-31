import type { ReactNode } from "react";

/** Presentation only: the caller must supply evidence-based text and tone. */
export function StatusRail({
  tone,
  label,
  selected = false,
  children,
}: {
  tone: "blue" | "yellow" | "red" | "gray";
  label: string;
  selected?: boolean;
  children?: ReactNode;
}) {
  return (
    <div
      className={`status-rail tone-${tone}`}
      data-selected={selected || undefined}
    >
      <span className="rail-label">{label}</span>
      {children}
    </div>
  );
}
