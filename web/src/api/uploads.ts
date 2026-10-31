import { apiJson, awaitCommand, commandInput, ApiError } from "./client";
import type { ArtifactRef, CommandResult, MediaRef, SourceRef } from "./contracts";

export interface UploadedMedia { artifact: ArtifactRef; media: MediaRef; source_ref: SourceRef }

export async function uploadMedia(file: File, selector: { actor_id?: string; conversation_id?: string; person_id?: string }, csrf: string, signal: AbortSignal) {
  const intent = commandInput(0);
  const form = new FormData();
  form.append("client_request_id", intent.client_request_id);
  form.append("file", file, file.name);
  const query = new URLSearchParams(Object.entries(selector).filter((entry): entry is [string, string] => !!entry[1]));
  const result = await apiJson<CommandResult<UploadedMedia>>(`capabilities/upload?${query}`, {
    binary: form, csrf, signal, kind: "command", intent,
  });
  if (result.client_request_id !== intent.client_request_id) throw new ApiError("outcome_unknown", 202, "上传结果尚未确认。", { client_request_id: intent.client_request_id }, null, "unknown");
  const completed = await awaitCommand(result, selector, csrf, signal);
  if (!completed.data?.media?.media_id.startsWith("source:") || !completed.data.source_ref || !completed.data.artifact) throw new ApiError("upload_source_unavailable", 502, "附件已上传，但原始来源引用不可用；请保留原操作编号核对。", { client_request_id: intent.client_request_id, action_id: completed.action_id }, null, "unknown");
  return completed.data;
}
