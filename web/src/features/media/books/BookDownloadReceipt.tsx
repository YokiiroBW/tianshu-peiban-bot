import type { Job } from "../types";
import { bookBytes } from "./presentation";
import { nativeResourceUrl } from "../../../api/client";

export function BookDownloadReceipt({ job }: { job: Job }) {
  const artifact = job.book_artifact;
  return (
    <section className="media-stack">
      <h4>书籍整理</h4>
      <p>
        已取得 {job.book_chapters_done ?? 0} /{" "}
        {job.book_chapters_total || "待查"} 章
      </p>
      {artifact ? (
        <>
          <p>
            {artifact.complete ? "完整 EPUB" : "部分章节 EPUB"} ·{" "}
            {bookBytes(artifact.bytes)} · 第 {artifact.version} 版
          </p>
          <p className="media-description">{artifact.relative_path}</p>
          {job.book_reused && (
            <p className="muted">章节与资料未变化，已复用原有 EPUB。</p>
          )}
          <a
            className="button primary"
            href={nativeResourceUrl(`/api/v1/media/books/files/${encodeURIComponent(job.book_id ?? "")}/epub`)}
            download
          >
            下载 EPUB
          </a>
        </>
      ) : (
        <p className="muted">取得正文并完成格式检查后，将写入书籍存档目录。</p>
      )}
      <a className="button" href="#/media/books">
        打开书籍订阅与资料
      </a>
    </section>
  );
}
