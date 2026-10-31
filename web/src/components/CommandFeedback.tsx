import { taskHref } from "../api/commandRecovery";
import type { CommandTarget } from "../api/client";
import type { CommandResult } from "../api/contracts";

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
  return (
    <div aria-live="polite">
      {busy && <p role="status">正在提交并读取回执…</p>}
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      {result && (
        <div className="command-feedback" role="status">
          <p>
            {result.receipt
              ? (states[result.receipt.execution] ?? result.receipt.execution)
              : "变更已记录；后续任务状态以实际结果为准"}
            {result.receipt?.effect === "unknown" ? " · 外部效果未知" : ""}
            {result.receipt?.delivery === "delivered"
              ? " · 已投递"
              : result.receipt?.delivery === "unknown"
                ? " · 投递结果未知"
                : ""}
          </p>
          {result.receipt?.error_code && <p>{result.receipt.error_code}</p>}
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
  );
}
