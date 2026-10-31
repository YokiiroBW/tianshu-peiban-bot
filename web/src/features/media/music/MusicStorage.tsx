import { DetailCard } from "../../../components/DetailCard";
import { useEffect, useState } from "react";
import type { MediaController } from "../useMediaController";
import type { MusicSettings } from "./types";

type Inspection = {
  state: string;
  samples: { artist: string; album: string; files: string[] }[];
};
export function MusicStorage({
  media,
  settings,
  refresh,
}: {
  media: MediaController;
  settings: MusicSettings;
  refresh: () => Promise<void>;
}) {
  const [directory, setDirectory] = useState(settings.directory);
  const [layout, setLayout] = useState(settings.layout);
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [checking, setChecking] = useState(false);
  useEffect(() => {
    setDirectory(settings.directory);
    setLayout(settings.layout);
  }, [settings.directory, settings.layout, settings.revision]);
  useEffect(() => () => media.cancel("music-directory"), [media.cancel]);
  return (
    <section className="media-card media-stack" aria-label="音乐存储">
      <div>
        <h3>音乐存储</h3>
        <p className="muted">
          选择天枢所在主机可访问的音乐目录。歌单关系保存于天枢，文件按专辑整理。
        </p>
      </div>
      <DetailCard title={"音乐存档设置"}>
        {media.error && (
          <p className="media-message media-warning" role="alert">
            {media.error}
          </p>
        )}
        {media.notice && (
          <p className="media-message" role="status">
            {media.notice}
          </p>
        )}
        <form
          className="media-stack"
          onSubmit={async (event) => {
            event.preventDefault();
            if (
              await media.command("music/settings/save", {
                directory: directory.trim(),
                layout,
                expected_revision: settings.revision,
              })
            ) {
              media.setNotice("音乐目录和整理方式已保存。");
              await refresh();
            }
          }}
        >
          <label>
            音乐目录
            <input
              value={directory}
              onChange={(event) => {
                setDirectory(event.target.value);
                setInspection(null);
              }}
              placeholder="例如 /media/Music 或共享目录路径"
              required
              maxLength={4096}
            />
          </label>
          <label>
            整理方式
            <select
              value={layout}
              onChange={(event) =>
                setLayout(event.target.value as MusicSettings["layout"])
              }
            >
              <option value="artist_album">
                艺术家 / 专辑 / 艺术家 - 歌曲
              </option>
              <option value="album_artist_album">
                专辑艺术家 / 专辑 / 艺术家 - 歌曲
              </option>
            </select>
          </label>
          <div className="music-actions">
            <button
              className="button"
              type="button"
              disabled={!directory.trim() || checking}
              onClick={async () => {
                setChecking(true);
                const value = await media.request<Inspection>(
                  "music-directory",
                  "music/settings/inspect",
                  { directory: directory.trim() },
                );
                if (value) setInspection(value);
                setChecking(false);
              }}
            >
              {checking ? "正在读取…" : "查看目录结构"}
            </button>
            <button
              className="button primary"
              disabled={!directory.trim() || !!media.busy}
            >
              保存存储设置
            </button>
          </div>
        </form>
      </DetailCard>
      {inspection && (
        <div role="status" className="music-directory-result">
          <p>
            {inspection.state === "readable"
              ? "目录可读取，以下为抽样结构。"
              : "目录暂不可读取，请检查路径和目录访问权限。"}
          </p>
          {inspection.samples.slice(0, 4).map((item, index) => (
            <div key={index}>
              <strong>
                {item.artist} / {item.album}
              </strong>
              <p className="muted">{item.files.join("、") || "暂无音频样本"}</p>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
