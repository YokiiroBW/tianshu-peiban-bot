import { HardDrive } from "lucide-react";
import type { MediaStorage, StorageBreakdown, StorageVolume } from "./types";
import { storageReasonWords, timestamp } from "./wording";
import { bytes } from "./overviewPresentation";

const categories = [
  { key: "subscription_bytes", category: "subscription", label: "订阅占用" },
  { key: "emby_bytes", category: "emby", label: "Emby占用" },
  { key: "other_bytes", category: "other", label: "其他占用" },
  { key: "unused_bytes", category: "unused", label: "未使用" },
] as const;

function amount(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : null;
}

function classificationNote(
  breakdown: StorageBreakdown | undefined,
  ready: boolean,
) {
  if (breakdown?.state === "ready" && !ready)
    return storageReasonWords.storage_measurement_inconsistent;
  if (breakdown?.code && breakdown.code !== "storage_logical_estimate")
    return (
      storageReasonWords[breakdown.code] ?? "分类统计暂不完整，后台会继续更新。"
    );
  if (breakdown?.state === "scanning")
    return storageReasonWords.storage_scanning;
  if (breakdown?.state === "partial")
    return "部分容量尚未完整统计，未知项显示为 —。";
  if (!breakdown || breakdown.state === "unavailable")
    return "分类容量暂不可用，未知项显示为 —。";
  return "";
}

function Volume({ volume }: { volume: StorageVolume }) {
  const available =
    volume.state === "available" &&
    [volume.total_bytes, volume.used_bytes, volume.free_bytes].every(
      (value) =>
        typeof value === "number" && Number.isFinite(value) && value >= 0,
    );
  const breakdown = volume.breakdown;
  const values = categories.map(({ key }) =>
    amount(
      breakdown
        ? breakdown[key]
        : key === "unused_bytes"
          ? volume.free_bytes
          : null,
    ),
  );
  const ready =
    available &&
    breakdown?.state === "ready" &&
    values.every((value) => value !== null) &&
    values.reduce<number>((total, value) => total + value, 0) ===
      volume.total_bytes;
  const segments = ready
    ? categories.map((item, index) => ({
        category: item.category,
        value: values[index]!,
      }))
    : values[3] !== null && available && values[3] <= volume.total_bytes!
      ? [
          {
            category: "pending",
            value: volume.total_bytes! - values[3],
          },
          { category: "unused", value: values[3] },
        ]
      : [];
  const tiny =
    ready &&
    volume.total_bytes! > 0 &&
    values.some((value) => value! > 0 && value! / volume.total_bytes! < 0.005);
  const note = classificationNote(breakdown, ready);
  return (
    <section className="overview-storage-volume" aria-label={volume.label}>
      <div className="media-section-head">
        <h3>{volume.label}</h3>
        <span className="muted">{available ? "已读取" : "暂不可用"}</span>
      </div>
      <ul className="overview-storage-directories" aria-label="媒体目录">
        {volume.directories.map((directory) => (
          <li key={directory}>{directory}</li>
        ))}
      </ul>
      {available ? (
        <div className="overview-storage-capacity">
          <strong>{bytes(volume.total_bytes!)}</strong>
          <span className="muted">总容量</span>
        </div>
      ) : (
        <p className="muted">暂未取得磁盘容量</p>
      )}
      <div
        className="overview-storage-bar"
        role="img"
        aria-label={`${volume.label} 容量分布：${
          ready
            ? categories
                .map((item, index) => `${item.label} ${bytes(values[index]!)}`)
                .join("，")
            : "已用区域待分类统计，" +
              (values[3] !== null
                ? `未使用 ${bytes(values[3])}`
                : "未使用容量未知")
        }`}
      >
        {segments
          .filter((segment) => segment.value > 0 && volume.total_bytes! > 0)
          .map((segment) => (
            <span
              key={segment.category}
              className={`overview-storage-segment storage-category-${segment.category}${ready && segment.value / volume.total_bytes! < 0.005 ? " is-tiny" : ""}`}
              aria-hidden="true"
              data-category={segment.category}
              style={{
                flexBasis: `${(segment.value / volume.total_bytes!) * 100}%`,
              }}
            />
          ))}
      </div>
      <dl className="overview-storage-facts" aria-label="四类容量">
        {categories.map((item, index) => (
          <div key={item.key} data-category={item.category}>
            <dt>
              <span
                className={`overview-storage-swatch storage-category-${item.category}`}
                aria-hidden="true"
              />
              {item.label}
            </dt>
            <dd>{values[index] === null ? "—" : bytes(values[index])}</dd>
          </div>
        ))}
      </dl>
      {note && (
        <p className="overview-storage-status muted" role="status">
          {note}
        </p>
      )}
      {breakdown?.measured_at != null && (
        <p className="overview-storage-status muted">
          统计时间：{timestamp(breakdown.measured_at)}
        </p>
      )}
      {breakdown?.refreshing && breakdown.measured_at != null && (
        <p className="overview-storage-status muted" role="status">
          正在后台更新，当前显示上次统计的容量。
        </p>
      )}
      {breakdown?.basis === "logical" && (
        <p className="overview-storage-status muted">
          {storageReasonWords.storage_logical_estimate}
        </p>
      )}
      {tiny && (
        <p className="overview-storage-status muted">
          极小占用以细线提示，不表示精确比例。
        </p>
      )}
    </section>
  );
}

export function StoragePanel({ storage }: { storage?: MediaStorage }) {
  const volumes = Array.isArray(storage?.volumes) ? storage.volumes : [];
  return (
    <section className="media-card overview-storage" aria-label="存储磁盘">
      <h2>
        <HardDrive aria-hidden="true" /> 存储磁盘
      </h2>
      {volumes.length ? (
        volumes.map((volume) => (
          <Volume key={volume.storage_id} volume={volume} />
        ))
      ) : (
        <div className="overview-empty">
          <p>
            {storage?.state === "not_configured"
              ? "尚未配置媒体存储目录"
              : "暂未取得磁盘容量"}
          </p>
          <p className="muted">
            {storage?.state === "not_configured"
              ? "添加媒体服务器并设置共享目录后显示对应磁盘。"
              : "恢复读取后显示媒体目录所在磁盘的实际容量。"}
          </p>
        </div>
      )}
      <p className="overview-note muted">
        媒体目录所在的存储磁盘，容量包含同盘其他文件。同盘目录合并显示。
        其他占用含文件系统开销与预留空间；订阅与 Emby 文件去重计量。
      </p>
    </section>
  );
}
