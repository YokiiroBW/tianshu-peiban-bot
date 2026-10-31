import { PanelsTopLeft } from "lucide-react";
import type { Comic } from "./types";
import { languageEvidence } from "./language";

export function ComicCard({
  comic,
  onOpen,
}: {
  comic: Comic;
  onOpen: () => void;
}) {
  return (
    <article className="media-card comic-card">
      <button
        className="comic-cover"
        aria-label={`打开 ${comic.metadata.title}`}
        onClick={onOpen}
      >
        {comic.has_cover ? (
          <img
            src={`/api/v1/media/comics/files/${comic.comic_id}/cover`}
            alt=""
            loading="lazy"
            onError={(e) => {
              e.currentTarget.style.visibility = "hidden";
            }}
          />
        ) : (
          <PanelsTopLeft aria-hidden="true" />
        )}
      </button>
      <div>
        <h3>
          <button className="comic-text-button" onClick={onOpen}>
            {comic.metadata.title}
          </button>
        </h3>
        <p>{comic.metadata.author || "作者未提供"}</p>
        <p className="muted">
          {comic.source_name} · {comic.metadata.language || "语言未知"}
        </p>
        <p className="muted">
          {languageEvidence(comic.language_evidence, comic.chinese_available)}
        </p>
        {comic.chapter_count > 0 && (
          <p className="muted">
            {comic.downloaded} / {comic.chapter_count} 章已归档
          </p>
        )}
        <button className="button" onClick={onOpen}>
          查看章节与预览
        </button>
      </div>
    </article>
  );
}
