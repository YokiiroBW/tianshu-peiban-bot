import type { ReactNode } from "react";
import { CircleAlert, Inbox, LoaderCircle, Unplug } from "lucide-react";

type Props = {
  kind: "unconfigured" | "empty" | "error" | "loading";
  title: string;
  children: ReactNode;
  action?: ReactNode;
};
export function StatePanel({ kind, title, children, action }: Props) {
  const Icon = {
    unconfigured: Unplug,
    empty: Inbox,
    error: CircleAlert,
    loading: LoaderCircle,
  }[kind];
  return (
    <section
      className={`state-panel state-${kind}`}
      role={
        kind === "error" ? "alert" : kind === "loading" ? "status" : undefined
      }
      aria-busy={kind === "loading" || undefined}
    >
      <span className="state-icon">
        <Icon aria-hidden="true" />
      </span>
      <h2>{title}</h2>
      <div className="state-copy">{children}</div>
      {action && <div className="state-action">{action}</div>}
    </section>
  );
}
