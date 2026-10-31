import { requestId } from "../../app/requestId";
import { useEffect, useRef, useState } from "react";
import {
  integrationPost,
  readFailure,
} from "../../app/integrationApi";
import { apiFetch } from "../../api/client";
import { useLifeAction } from "./useLifeRuntime";
import { RuntimeFeedback } from "./RuntimeFeedback";
import { contentHash } from "./contentHash";
import type { LifeAccess } from "./runtimeApi";
import type { content_ref } from "./runtimeTypes";

type Upload = {
  upload_id: string;
  state: "pending" | "complete" | "imported";
  sha256: string;
  size: number;
};
export function ContentAcquire({
  access,
  onAcquired,
  imagesOnly = false,
}: {
  access: LifeAccess;
  onAcquired: (ref: content_ref) => void;
  imagesOnly?: boolean;
}) {
  const [url, setUrl] = useState(""),
    [file, setFile] = useState<File | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const action = useLifeAction(access, () => {});
  const current = useRef<AbortController | null>(null);
  const pending = useRef<{
    key: string;
    id: string;
    upload: Upload | null;
  } | null>(null);
  useEffect(() => () => current.current?.abort(), []);
  async function acquire(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    const control = new AbortController();
    current.current = control;
    setBusy(true);
    setError("");
    try {
      let source:
        { kind: "url"; url: string } | { kind: "upload"; upload_id: string };
      if (file) {
        if (!file.size || file.size > 33554432)
          throw new Error("请选择 1 字节至 32 MiB 的文件。");
        const bytes = await file.arrayBuffer();
        const hash = await contentHash(bytes);
        const key = JSON.stringify([file.name, file.size, file.type, hash]);
        if (pending.current?.key !== key)
          pending.current = { key, id: requestId(), upload: null };
        const attempt = pending.current;
        const post = <T,>(path: string, value: object) =>
          integrationPost<T>(
            `content/${path}`,
            { actor_id: access.actor, client_request_id: attempt.id, value },
            access.csrf,
            control.signal,
          );
        if (!attempt.upload)
          attempt.upload = await post<Upload>("uploads", {
            filename: file.name,
            media_type: file.type || "application/octet-stream",
            size: file.size,
            sha256: hash,
          });
        let status = await post<Upload>("upload-status", {
          upload_id: attempt.upload.upload_id,
        });
        if (status.state === "pending") {
          const response = await apiFetch(`content/upload/${encodeURIComponent(status.upload_id)}`, {
            binary: bytes, csrf: access.csrf, signal: control.signal,
            headers: { "X-Tianshu-Actor-Id": access.actor, "X-Tianshu-Request-Id": attempt.id },
          });
          const result = await response.json();
          status = result as Upload;
        }
        if (status.state !== "complete" && status.state !== "imported")
          throw new Error("上传尚未完成，请重试同一文件以核对原上传。");
        source = { kind: "upload", upload_id: status.upload_id };
      } else source = { kind: "url", url };
      if (control.signal.aborted) return;
      const answer = await action.run("content.acquire", {
        query: {},
        scope: {},
        source,
        purpose: "save",
      });
      if (answer?.result.content_ref && !control.signal.aborted) {
        if (imagesOnly && answer.result.content_ref.kind !== "image")
          throw new Error("原件已保存，但不是可用的图片参考，请选择图片。");
        onAcquired(answer.result.content_ref);
      }
    } catch (cause) {
      if (!control.signal.aborted) setError(readFailure(cause));
    } finally {
      if (!control.signal.aborted) setBusy(false);
    }
  }
  return (
    <form className="life-form life-acquire" onSubmit={acquire}>
      <label>
        {imagesOnly ? "上传参考图片" : "上传原件"}
        <input
          type="file"
          accept={
            imagesOnly
              ? "image/png,image/jpeg,image/webp"
              : ".txt,.md,.html,.pdf,.docx,.png,.jpg,.jpeg,.webp,.mp4,.mov,.mkv,.mp3,.wav,.m4a,.srt,.vtt"
          }
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            setUrl("");
          }}
        />
      </label>
      {!imagesOnly && (
        <label>
          或文章、媒体链接
          <input
            type="url"
            value={url}
            onChange={(e) => {
              setUrl(e.target.value);
              setFile(null);
            }}
            placeholder="https://…"
          />
        </label>
      )}
      <p className="muted">
        原件按当前登录身份保存，最多 32 MiB。续读、参考与版本都指向该原件。
      </p>
      <button
        className="button"
        disabled={busy || action.busy || (!file && !url)}
      >
        {busy ? "正在取得并核对原件…" : "取得原件"}
      </button>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <RuntimeFeedback {...action} />
    </form>
  );
}
