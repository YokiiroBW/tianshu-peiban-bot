import type { MediaView, StorageVolume } from "./types";

export function bytes(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value) || value < 0) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  const index =
    value > 0
      ? Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
      : 0;
  return `${(value / 1024 ** Math.max(0, index)).toLocaleString("zh-CN", { maximumFractionDigits: 1 })} ${units[Math.max(0, index)]}`;
}
export function storageVolumes(view: MediaView) {
  const volumes = new Map<string, StorageVolume>();
  for (const volume of [
    ...(view.overview?.storage.volumes ?? []),
    ...(view.overview?.music_storage?.volumes ?? []),
  ]) {
    const prior = volumes.get(volume.storage_id);
    volumes.set(
      volume.storage_id,
      prior
        ? {
            ...prior,
            label:
              prior.label === volume.label ? prior.label : "视频与音乐共享磁盘",
            directories: [
              ...new Set([...prior.directories, ...volume.directories]),
            ],
            target_ids: [
              ...new Set([...prior.target_ids, ...volume.target_ids]),
            ],
          }
        : volume,
    );
  }
  return [...volumes.values()];
}
export const count = (value: number | null | undefined) =>
  value == null ? "—" : value.toLocaleString("zh-CN");
export function uptime(seconds: number | undefined) {
  if (seconds == null) return "尚未取得";
  if (seconds < 60) return "不足 1 分钟";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`;
  return `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分钟`;
}
