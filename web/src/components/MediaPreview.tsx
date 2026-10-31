import { useEffect, useState } from "react";
import { API_ROOT } from "../api/client";
import { useRoleWorkspace } from "../app/RoleWorkspace";
import type { ArtifactRef } from "../api/contracts";

export type MediaRef = ArtifactRef["media"];

export function MediaPreview({ media, alt = "媒体原件" }: { media: MediaRef; alt?: string }) {
  const [error, setError] = useState("");
  const { actor, conversation } = useRoleWorkspace();
  // Native media loading uses the same-origin HttpOnly session and preserves Range seeking.
  const query = new URLSearchParams();
  if (actor) query.set("actor_id", actor);
  if (conversation?.conversation_id) query.set("conversation_id", conversation.conversation_id);
  if (conversation?.person_id) query.set("person_id", conversation.person_id);
  const url = `${API_ROOT}capabilities/media/${encodeURIComponent(media.media_id)}?${query}`;
  useEffect(() => { setError(""); }, [url]);
  const failed = () => setError("原件暂时无法读取，请刷新后重试。");
  if (error) return <div className="error-text" role="alert">{error} <button className="text-link" onClick={() => setError("")}>重试读取</button></div>;
  return <div className="media-artifact">
    {media.mime_type.startsWith("image/") ? <img src={url} alt={alt} loading="lazy" onError={failed} />
      : media.mime_type.startsWith("audio/") ? <audio src={url} controls preload="metadata" aria-label={alt} onError={failed} />
      : media.mime_type.startsWith("video/") ? <video src={url} controls preload="metadata" aria-label={alt} onError={failed} /> : null}
    <a className="text-link" href={url} download>{media.mime_type.startsWith("audio/") ? "保存音频" : "保存原件"}</a>
  </div>;
}

export function ArtifactList({ artifacts }: { artifacts: ArtifactRef[] }) {
  return <div className="artifact-list">{artifacts.map((artifact) =>
    <MediaPreview key={artifact.artifact_id} media={artifact.media} />,
  )}</div>;
}
