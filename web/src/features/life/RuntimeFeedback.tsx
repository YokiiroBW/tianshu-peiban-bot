import { showState } from "./runtimeApi";
import type { manage_response } from "./runtimeTypes";
const receipts: Record<string, string> = {
  "chapter.review": "审阅意见已记录",
  "chapter.revise": "原稿修订已保存",
  "chapter.publish": "当前原稿已发布",
  "image.request": "创作任务已登记",
  "reading.open": "阅读进度已建立",
};

export function RuntimeFeedback({
  busy,
  error,
  receipt,
}: {
  busy?: boolean;
  error?: string;
  receipt?: manage_response | null;
}) {
  return (
    <>
      {busy && <p role="status">正在提交并核对回执…</p>}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {receipt && (
        <p className="muted" role="status">
          {receipt.result.state !== "unknown" && receipts[receipt.operation]
            ? `${receipts[receipt.operation]} · `
            : ""}
          {showState(receipt.result.state)} · 已核对版本{" "}
          {receipt.result.version}
          {receipt.result.state === "unknown"
            ? "；结果尚未确认，请查询原任务。"
            : ""}
        </p>
      )}
    </>
  );
}
export function ResourceFeedback({
  loading,
  error,
  empty,
}: {
  loading: boolean;
  error: string;
  empty: boolean;
}) {
  return (
    <>
      {loading && <p role="status">正在读取…</p>}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {!loading && !error && empty && (
        <p className="muted">当前可读范围内暂无记录。</p>
      )}
    </>
  );
}
