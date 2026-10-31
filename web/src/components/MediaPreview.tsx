import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import type { ArtifactRef } from "../api/contracts";

export type MediaRef = ArtifactRef["media"];

export function MediaPreview({ media, alt = "媒体原件" }: { media: MediaRef; alt?: string }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = "";
    setUrl(""); setError("");
    void apiFetch(`capabilities/media/${encodeURIComponent(media.media_id)}`, { signal: controller.signal })
      .then((response) => response.blob())
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob); setUrl(objectUrl);
      })
      .catch((cause) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "原件读取失败"); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [media.media_id, media.sha256]);
  if (error) return <p className="error-text" role="alert">{error}</p>;
  if (!url) return <p role="status">正在读取原件…</p>;
  return <div className="media-artifact">
    {media.mime_type.startsWith("image/") ? <img src={url} alt={alt} loading="lazy" />
      : media.mime_type.startsWith("audio/") ? <audio src={url} controls preload="metadata" aria-label={alt} />
      : media.mime_type.startsWith("video/") ? <video src={url} controls preload="metadata" aria-label={alt} /> : null}
    <a className="text-link" href={url} download>{media.mime_type.startsWith("audio/") ? "保存音频" : "保存原件"}</a>
  </div>;
}

export function ArtifactList({ artifacts }: { artifacts: ArtifactRef[] }) {
  return <div className="artifact-list">{artifacts.map((artifact) =>
    <MediaPreview key={artifact.artifact_id} media={artifact.media} />,
  )}</div>;
}
