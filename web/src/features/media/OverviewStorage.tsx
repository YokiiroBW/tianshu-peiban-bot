import { ArrowRight, HardDrive } from "lucide-react";
import type { MediaView } from "./types";
import { bytes, storageVolumes } from "./overviewPresentation";
import { StoragePanel } from "./StoragePanel";

export function OverviewStorage({ view }: { view: MediaView }) {
  const volumes = storageVolumes(view);
  return (
    <section className="media-card overview-capacity" aria-label="存储空间">
      <div className="media-section-head">
        <h2>
          <HardDrive aria-hidden="true" />
          存储空间
        </h2>
        <a className="overview-text-link" href="#/media/storage">
          详情
          <ArrowRight aria-hidden="true" />
        </a>
      </div>
      {volumes.length ? (
        volumes.slice(0, 2).map((volume) => {
          const valid =
            volume.state === "available" &&
            [volume.total_bytes, volume.used_bytes, volume.free_bytes].every(
              (v) => v != null && Number.isFinite(v) && v >= 0,
            );
          const percent =
            valid && volume.total_bytes! > 0
              ? Math.min(100, (volume.free_bytes! / volume.total_bytes!) * 100)
              : 0;
          return (
            <div className="overview-capacity-volume" key={volume.storage_id}>
              <div className="overview-capacity-label">
                <span>{volume.label}</span>
                <strong>
                  {valid ? `${percent.toFixed(1)}% 可用` : "未取得"}
                </strong>
              </div>
              <div className="overview-capacity-value">
                {valid ? bytes(volume.free_bytes) : "—"}
                <span>可用 / {valid ? bytes(volume.total_bytes) : "—"}</span>
              </div>
              {valid && (
                <div
                  className="overview-capacity-track"
                  role="img"
                  aria-label={`${volume.label}剩余 ${percent.toFixed(1)}%`}
                >
                  <span style={{ width: `${percent}%` }} />
                </div>
              )}
            </div>
          );
        })
      ) : (
        <p className="muted">
          {view.overview?.storage.state === "not_configured"
            ? "尚未配置媒体存储目录"
            : "暂未取得磁盘容量"}
        </p>
      )}
      {volumes.length > 2 && (
        <a className="overview-text-link" href="#/media/storage">
          查看全部 {volumes.length} 个磁盘
        </a>
      )}
    </section>
  );
}

export function StorageDetails({ view }: { view: MediaView }) {
  const volumes = storageVolumes(view);
  return (
    <section
      className="media-stack media-storage-details"
      aria-label="存储空间详情"
    >
      <div className="media-section-head">
        <h2>存储空间</h2>
        <a className="overview-text-link" href="#/media">
          返回概览
        </a>
      </div>
      <StoragePanel
        storage={{
          state: view.overview?.storage.state ?? "unavailable",
          volumes,
        }}
      />
      <div className="media-card">
        <h2>目录与归档设置</h2>
        <p className="muted">
          磁盘按实际文件系统合并。分类占用沿用视频与 Emby
          的统计，音乐文件计入其他占用；音乐独立磁盘的分类暂不统计。
        </p>
        <div className="media-actions">
          <a className="button" href="#/media/accounts">
            视频媒体库设置
          </a>
          <a className="button" href="#/media/music">
            音乐归档与 Navidrome
          </a>
        </div>
      </div>
    </section>
  );
}
