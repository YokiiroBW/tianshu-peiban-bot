import type { Job } from "../types";

export function ComicDownloadReceipt({ job }: { job: Job }) {
  const reader = job.library_results.find(
    (r) => r.state === "verified" && r.reader_url,
  );
  return (
    <section>
      <h4>漫画归档与阅读</h4>
      <p>
        {job.comic_pages_done ?? 0} / {job.comic_pages_total || "待查"} 页 ·
        原图 CBZ
      </p>
      {job.asset_receipt ? (
        <>
          <p>AssetLibrary 已纳管并返回资产回执。</p>
          <dl className="media-facts">
            <div>
              <dt>资产标识</dt>
              <dd>{String(job.asset_receipt.entry_id)}</dd>
            </div>
            <div>
              <dt>内容版本</dt>
              <dd>{String(job.asset_receipt.content_version)}</dd>
            </div>
            <div>
              <dt>成品路径</dt>
              <dd>{job.comic_relative_path}</dd>
            </div>
          </dl>
        </>
      ) : (
        <p className="muted">尚未取得正式资产回执。</p>
      )}
      {reader ? (
        <a
          className="button primary"
          href={reader.reader_url}
          target="_blank"
          rel="noreferrer"
        >
          在 Komga 阅读 · {reader.page_count} 页
        </a>
      ) : (
        <p className="muted">完成归档并被 Komga 识别后可打开阅读。</p>
      )}
      <p>
        <a className="button" href="#/media/comics">
          返回漫画订阅
        </a>
      </p>
    </section>
  );
}
