import { useEffect, useState } from "react";
import { API_ROOT } from "../api/client";
import { useDomainTarget, type DomainTarget } from "../api/useDomainResource";
import type { ArtifactRef } from "../api/contracts";

export type MediaRef = ArtifactRef["media"];

export function MediaPreview({
  media,
  alt = "媒体原件",
  scope,
}: {
  media: MediaRef;
  alt?: string;
  scope?: DomainTarget;
}) {
  const [error, setError] = useState("");
  const current = useDomainTarget();
  const target = scope ?? current;
  // Native media loading uses the same-origin HttpOnly session and preserves Range seeking.
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(target))
    if (typeof value === "string" && value) query.set(key, value);
  const url = `${API_ROOT}capabilities/media/${encodeURIComponent(media.media_id)}?${query}`;
  useEffect(() => {
    setError("");
  }, [url]);
  const failed = () => setError("原件暂时无法读取，请刷新后重试。");
  if (error)
    return (
      <div className="error-text" role="alert">
        {error}{" "}
        <button className="text-link" onClick={() => setError("")}>
          重试读取
        </button>
      </div>
    );
  return (
    <div className="media-artifact">
      {media.mime_type.startsWith("image/") ? (
        <img src={url} alt={alt} loading="lazy" onError={failed} />
      ) : media.mime_type.startsWith("audio/") ? (
        <audio
          src={url}
          controls
          preload="metadata"
          aria-label={alt}
          onError={failed}
        />
      ) : media.mime_type.startsWith("video/") ? (
        <video
          src={url}
          controls
          preload="metadata"
          aria-label={alt}
          onError={failed}
        />
      ) : null}
      <a className="text-link" href={url} download>
        {media.mime_type.startsWith("audio/") ? "保存音频" : "保存原件"}
      </a>
    </div>
  );
}

export function ArtifactList({
  artifacts,
  scope,
}: {
  artifacts: ArtifactRef[];
  scope?: DomainTarget;
}) {
  return (
    <div className="artifact-list">
      {artifacts.map((artifact) => (
        <MediaPreview
          key={artifact.artifact_id}
          media={artifact.media}
          scope={scope}
        />
      ))}
    </div>
  );
}
