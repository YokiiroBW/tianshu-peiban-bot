import { DetailCard } from "../../../components/DetailCard";
import { useState } from "react";
import type { MediaController } from "../useMediaController";
import { timestamp, word } from "../wording";
import type { MusicPlaylist, NavidromePlaylist as Link } from "./types";

const states: Record<string, string> = {
  queued: "等待同步",
  syncing: "正在同步",
  synced: "已同步",
  partial: "部分歌曲待补齐",
  failed: "同步未完成",
  paused: "自动同步已暂停",
  deleting: "正在删除",
};
const missing: Record<string, string> = {
  verification_pending: "暂时无法复核，保留上次确认的歌曲",
  not_downloaded: "尚未下载",
  local_file_missing: "本地文件缺失或已改变",
  waiting_index: "等待 Navidrome 入库",
  ambiguous: "存在多个匹配，未自动选择",
};

export function NavidromePlaylist({
  media,
  playlist,
  link,
  refresh,
}: {
  media: MediaController;
  playlist: MusicPlaylist;
  link?: Link;
  refresh: () => Promise<void>;
}) {
  const [name, setName] = useState(link?.name ?? "");
  const [shared, setShared] = useState(link?.public ?? false);
  const [automatic, setAutomatic] = useState(link?.auto_sync ?? true);
  async function control(action: string) {
    if (
      link &&
      (await media.command("music/navidrome/playlists/control", {
        link_id: link.link_id,
        action,
        expected_revision: link.revision,
      }))
    )
      await refresh();
  }
  return (
    <section
      className="media-stack navidrome-playlist"
      aria-label={`${playlist.name}的 Navidrome 歌单`}
    >
      {link && (
        <>
          <p>
            <strong>Navidrome · {states[link.status] ?? link.status}</strong> ·{" "}
            {link.synced_count}/{link.total} 首<br />
            <span className="muted">
              {link.auto_sync ? "自动同步" : "手动同步"} ·{" "}
              {timestamp(link.checked_at)}
            </span>
          </p>
          {link.code !== link.status && link.code !== "syncing" && (
            <p role="status">{word(link.code)}</p>
          )}
          <div className="music-actions">
            <button
              className="button"
              disabled={!!media.busy || link.status === "deleting"}
              onClick={() => void control("sync")}
            >
              同步至 Navidrome
            </button>
            <button
              className="button"
              disabled={!!media.busy || link.status === "deleting"}
              onClick={() => void control(link.auto_sync ? "pause" : "resume")}
            >
              {link.auto_sync
                ? "暂停 Navidrome 自动同步"
                : "开启 Navidrome 自动同步"}
            </button>
            {link.code === "navidrome_not_found" && (
              <button
                className="button"
                disabled={!!media.busy}
                onClick={() => void control("recreate")}
              >
                重新创建 Navidrome 歌单
              </button>
            )}
          </div>
          {link.problem_count > 0 && (
            <DetailCard title={<>查看待补齐歌曲（{link.problem_count} 首）</>}>
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

              <ul>
                {link.problems.map((row) => (
                  <li key={row.position}>
                    {row.position}. {row.title} ·{" "}
                    {missing[row.code] ?? word(row.code)}
                  </li>
                ))}
              </ul>
              {link.problem_count > link.problems.length && (
                <p className="muted">此处显示前 {link.problems.length} 首。</p>
              )}
            </DetailCard>
          )}
        </>
      )}
      <DetailCard
        title={<>{link ? "Navidrome 歌单设置" : "创建 Navidrome 歌单"}</>}
      >
        <form
          className="media-stack navidrome-form"
          onSubmit={async (e) => {
            e.preventDefault();
            if (
              await media.command("music/navidrome/playlists/save", {
                playlist_id: playlist.playlist_id,
                name,
                public: shared,
                auto_sync: automatic,
                expected_revision: link?.revision ?? 0,
              })
            )
              await refresh();
          }}
        >
          <label>
            Navidrome 歌单名称
            <input
              maxLength={256}
              value={name}
              placeholder={`跟随原歌单：${playlist.name}`}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label className="media-check">
            <input
              type="checkbox"
              checked={automatic}
              onChange={(e) => setAutomatic(e.target.checked)}
            />
            歌单或本地歌曲更新后自动同步
          </label>
          <label className="media-check">
            <input
              type="checkbox"
              checked={shared}
              onChange={(e) => setShared(e.target.checked)}
            />
            在 Navidrome 中共享给其他用户
          </label>
          <p className="muted">
            按天枢歌单顺序同步已归档歌曲；未下载或尚未入库的歌曲之后补齐。手动编辑
            Navidrome 中的这份歌单，会在下次同步时按天枢内容更新。
          </p>
          <div className="music-actions">
            <button
              className="button"
              disabled={!!media.busy || link?.status === "deleting"}
            >
              {link ? "保存同步设置" : "创建并同步歌单"}
            </button>
            {link && (
              <button
                type="button"
                className="button"
                disabled={!!media.busy || link.status === "deleting"}
                onClick={() => void control("delete")}
              >
                删除 Navidrome 歌单
              </button>
            )}
          </div>
          {link && (
            <p className="muted">
              删除只移除 Navidrome 中这份歌单，音乐存档和天枢歌单仍保留。
            </p>
          )}
        </form>
      </DetailCard>
    </section>
  );
}
