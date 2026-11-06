import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { humanizeCode } from "../design/humanize";
import "./notifications.css";

const NotificationTarget = createContext<HTMLDivElement | null>(null);

export function NotificationProvider({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<HTMLDivElement | null>(null);
  useEffect(() => {
    const host = document.createElement("div");
    host.className = "notification-region";
    host.setAttribute("aria-label", "操作通知");
    const relocate = () => {
      // Keep notifications interactive above native modal cards as well.
      const dialogs = Array.from(
        document.querySelectorAll<HTMLDialogElement>("dialog[open]"),
      );
      const focusedDialog = document.activeElement?.closest("dialog[open]");
      const parent = focusedDialog ?? dialogs.at(-1) ?? document.body;
      if (host.parentElement !== parent) parent.appendChild(host);
    };
    relocate();
    setTarget(host);
    const observer = new MutationObserver(relocate);
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["open"],
    });
    return () => {
      observer.disconnect();
      host.remove();
    };
  }, []);
  return (
    <NotificationTarget.Provider value={target}>
      {children}
    </NotificationTarget.Provider>
  );
}

/** Presentation only: the caller still owns operation state and recovery. */
export function Notification({ children }: { children: ReactNode }) {
  const target = useContext(NotificationTarget);
  return target ? createPortal(children, target) : null;
}

export function Notice({
  children,
  error = false,
}: {
  children: ReactNode;
  error?: boolean;
}) {
  const [dismissed, setDismissed] = useState<ReactNode>();
  if (!children || dismissed === children) return null;
  return (
    <Notification>
      <div className="notification-card" role={error ? "alert" : "status"}>
        <button
          type="button"
          className="text-link notification-dismiss"
          aria-label="收起提示"
          onClick={() => setDismissed(children)}
        >
          ×
        </button>
        {typeof children === "string"
          ? humanizeCode(
              children,
              error ? "操作未完成，请核对原任务" : "操作状态已更新",
            )
          : children}
      </div>
    </Notification>
  );
}
