import { taskHref } from "../api/commandRecovery";
import type { CommandTarget } from "../api/client";
import type { CommandResult } from "../api/contracts";
import { humanizeCode } from "../design/humanize";
import { useEffect, useState } from "react";
import { Notification } from "./Notifications";

const states: Record<string, string> = {
  pending: "已受理，等待执行",
  running: "正在执行",
  succeeded: "执行完成",
  failed: "执行失败",
  cancelled: "已取消",
  unknown: "结果尚未确认",
};

export function CommandFeedback({
  busy,
  error,
  result,
  operation,
  target,
}: {
  busy?: boolean;
  error?: string;
  result?: CommandResult<unknown> | null;
  operation?: string;
  target?: CommandTarget;
}) {
  const [dismissed, setDismissed] = useState<{
    error?: string;
    result?: CommandResult<unknown> | null;
  } | null>(null);
  useEffect(() => {
    setDismissed(null);
    const receipt = result?.receipt;
    if (
      !busy &&
      !error &&
      receipt?.execution === "succeeded" &&
      receipt.effect !== "unknown" &&
      receipt.delivery !== "unknown"
    ) {
      const timer = window.setTimeout(
        () => setDismissed({ error, result }),
        6000,
      );
      return () => window.clearTimeout(timer);
    }
  }, [busy, error, result]);
  if (
    !busy &&
    ((!error && !result) ||
      (dismissed?.error === error && dismissed?.result === result))
  )
    return null;
  return (
    <Notification>
      <div className="notification-card" aria-live="polite">
        {!busy && (
          <button
            type="button"
            className="text-link notification-dismiss"
            onClick={() => setDismissed({ error, result })}
            aria-label="收起提示"
          >
            ×
          </button>
        )}
        {busy && <p role="status">正在提交并读取回执…</p>}
        {error && (
          <p role="alert" className="error-text">
            {humanizeCode(error, "操作未完成，请查看原任务")}
          </p>
        )}
        {result && (
          <div className="command-feedback" role="status">
            <p>
              {result.receipt
                ? (states[result.receipt.execution] ?? "执行状态尚未确认")
                : "变更已记录；后续任务状态以实际结果为准"}
              {result.receipt?.effect === "unknown" ? " · 外部效果未知" : ""}
              {result.receipt?.delivery === "delivered"
                ? " · 已投递"
                : result.receipt?.delivery === "unknown"
                  ? " · 投递结果未知"
                  : ""}
            </p>
            {result.receipt?.error_code && (
              <p>
                {humanizeCode(
                  result.receipt.error_code,
                  "操作未完成，请查看原任务",
                )}
              </p>
            )}
            {(result.action_id || result.attempt_id) && (
              <a
                className="text-link"
                href={taskHref({ ...result, operation, target })}
              >
                查看原任务
              </a>
            )}
          </div>
        )}
      </div>
    </Notification>
  );
}
