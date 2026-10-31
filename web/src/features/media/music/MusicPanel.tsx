import { Drawer } from "../../../components/Drawer";
import { DetailCard } from "../../../components/DetailCard";
import { useCallback, useEffect, useRef, useState } from "react";
import { Music2, RefreshCw, Trash2 } from "lucide-react";
import type { MediaController } from "../useMediaController";
import { timestamp, word } from "../wording";
import { MusicAccounts } from "./MusicAccounts";
import { MusicStorage } from "./MusicStorage";
import { audioQualityText, MusicDownloads, unlockText } from "./MusicDownloads";
import type {
  MusicPlaylist,
  MusicView,
  PlaylistDetail,
  SourcePlaylist,
} from "./types";
import "./music.css";
import { providerNames } from "./types";
import { MusicLibrary } from "./MusicLibrary";
import { NavidromeConnection } from "./NavidromeConnection";
import { NavidromePlaylist } from "./NavidromePlaylist";
import { MusicMaintenance } from "./MusicMaintenance";

export function MusicPanel({ media }: { media: MediaController }) {
  const [view, setView] = useState<MusicView | null>(null);
  const [accountId, setAccountId] = useState("");
  const [catalog, setCatalog] = useState<SourcePlaylist[] | null>(null);
  const [detail, setDetail] = useState<PlaylistDetail | null>(null);
  const [interval, setInterval] = useState("43200");
  const [reading, setReading] = useState("");
  const detailCursor = useRef<{ playlist_id: string; offset: number } | null>(
    null,
  );
  const current = useRef(media);
  current.current = media;
  const refresh = useCallback(async () => {
    const result = await current.current.request<MusicView>(
      "music-view",
      "music/view",
      {},
    );
    if (result?.configured) setView(result);
    const cursor = detailCursor.current;
    if (cursor) {
      const tracks = await current.current.request<PlaylistDetail>(
        "music-detail",
        "music/playlists/detail",
        cursor,
      );
      if (tracks && detailCursor.current === cursor) setDetail(tracks);
    }
  }, []);
  useEffect(() => {
    void refresh();
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    const poll = async () => {
      if (!document.hidden) await refresh();
      if (!stopped) timer = setTimeout(poll, 30000);
    };
    timer = setTimeout(poll, 30000);
    return () => {
      stopped = true;
      clearTimeout(timer);
      for (const slot of [
        "music-view",
        "music-catalog",
        "music-detail",
        "music-scan",
        "music-export",
        "music-library",
        "music-library-scan",
        "navidrome-test",
        "command",
      ])
        current.current.cancel(slot);
    };
  }, [refresh]);
  useEffect(() => {
    if (view?.maintenance)
      setInterval(String(view.maintenance.settings.playlist_interval_seconds));
  }, [view?.maintenance?.settings.revision]);
  useEffect(() => {
    if (
      view &&
      !view.accounts.some(
        (account) =>
          account.account_id === accountId && account.state === "ready",
      )
    ) {
      setAccountId(
        view.accounts.find((account) => account.state === "ready")
          ?.account_id ?? "",
      );
      setCatalog(null);
    }
  }, [view, accountId]);
  async function readCatalog() {
    setReading("catalog");
    const result = await media.request<{ playlists: SourcePlaylist[] }>(
      "music-catalog",
      "music/playlists/list",
      { account_id: accountId },
    );
    if (result) setCatalog(result.playlists);
    setReading("");
  }
  async function readDetail(playlist: MusicPlaylist, offset = 0) {
    setReading("detail");
    const cursor = { playlist_id: playlist.playlist_id, offset };
    detailCursor.current = cursor;
    const result = await media.request<PlaylistDetail>(
      "music-detail",
      "music/playlists/detail",
      cursor,
    );
    if (result && detailCursor.current === cursor) setDetail(result);
    setReading("");
  }
  if (!view)
    return (
      <section className="media-card">
        <p role="status">正在读取音乐订阅…</p>
        <button className="button" onClick={() => void refresh()}>
          重新读取
        </button>
      </section>
    );
  return (
    <section className="media-stack music-panel" aria-label="音乐订阅">
      <div className="media-section-head">
        <div>
          <h2>
            <Music2 aria-hidden="true" />
            音乐订阅
          </h2>
          <p className="muted">把喜欢的歌单留在天枢，按原有顺序持续同步。</p>
        </div>
        <button className="button" onClick={() => void refresh()}>
          <RefreshCw aria-hidden="true" />
          刷新音乐状态
        </button>
      </div>
      <p className="music-scope">
        网易云、QQ
        音乐、酷狗歌单统一保存在天枢。优先复用本地歌曲；缺失时自动搜索同一版本，使用有完整下载权限的账号获取，整理标签、封面和歌词。一首歌曲共用一个文件，各歌单保留自己的顺序，并导出
        M3U8 和 JSON。
      </p>
      <div className="music-settings-grid">
        <MusicAccounts
          media={media}
          accounts={view.accounts}
          refresh={refresh}
          providers={view.providers}
        />
        <MusicStorage
          media={media}
          settings={view.settings}
          refresh={refresh}
        />
      </div>
      <MusicLibrary media={media} status={view.imports} refresh={refresh} />
      {view.lyrics && (
        <section className="media-card" aria-label="歌词自动补齐">
          <h3>歌词自动补齐</h3>
          <p>
            {view.lyrics.synced} 首滚动歌词 · {view.lyrics.translated}{" "}
            首同步翻译 · {view.lyrics.counts.queued ?? 0} 首待检查 ·{" "}
            {view.lyrics.counts.retry_wait ?? 0} 首等待重试
          </p>
          <p className="muted">
            新下载和已有歌曲自动跨渠道补词，共用一份歌词，不重新下载音频。失败从
            6 小时起逐步延后，缺词按周补查；原文与翻译对齐后合并显示。
          </p>
        </section>
      )}
      {view.maintenance && (
        <MusicMaintenance
          key={view.maintenance.settings.revision}
          media={media}
          view={view.maintenance}
          refresh={refresh}
        />
      )}
      <NavidromeConnection
        key={view.navidrome?.connection?.revision ?? 0}
        media={media}
        connection={view.navidrome?.connection}
        refresh={refresh}
      />
      <DetailCard title={"添加音乐订阅"}>
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
        <section className="media-card media-stack" aria-label="选择音乐歌单">
          <div>
            <h3>选择歌单</h3>
            <p className="muted">
              读取本人创建、收藏的歌单和“我喜欢”。首次订阅会保存完整成员及歌曲资料。
            </p>
          </div>
          <div className="music-select-row">
            <label>
              音乐账号
              <select
                value={accountId}
                disabled={!!reading || !!media.busy}
                onChange={(event) => {
                  setAccountId(event.target.value);
                  setCatalog(null);
                }}
              >
                <option value="">请选择已登录账号</option>
                {view.accounts
                  .filter((account) => account.state === "ready")
                  .map((account) => (
                    <option key={account.account_id} value={account.account_id}>
                      {providerNames[account.provider]} · {account.label}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              同步周期
              <select
                value={interval}
                onChange={(event) => setInterval(event.target.value)}
              >
                <option value="900">每 15 分钟</option>
                <option value="3600">每小时</option>
                <option value="21600">每 6 小时</option>
                <option value="43200">每 12 小时</option>
                <option value="86400">每天</option>
              </select>
            </label>
            <button
              className="button"
              disabled={!accountId || !!reading || !!media.busy}
              onClick={() => void readCatalog()}
            >
              {reading === "catalog" ? "正在读取…" : "读取账号歌单"}
            </button>
          </div>
          {catalog?.length === 0 && (
            <p className="muted">此账号暂未返回可访问的歌单。</p>
          )}
          {catalog && (
            <div className="music-playlists">
              {catalog.map((item) => {
                const existing = view.playlists.find(
                  (row) =>
                    row.account_id === accountId &&
                    row.source_id === item.source_id,
                );
                return (
                  <article className="music-playlist" key={item.source_id}>
                    <div>
                      <strong>{item.name}</strong>
                      <p className="muted">
                        {
                          {
                            created: "我创建的",
                            collected: "我收藏的",
                            liked: "我喜欢",
                          }[item.kind]
                        }{" "}
                        · {item.track_count ?? "未知"} 首
                      </p>
                    </div>
                    <button
                      className="button"
                      disabled={!!existing || !!media.busy || !!reading}
                      onClick={async () => {
                        if (
                          await media.command("music/playlists/subscribe", {
                            account_id: accountId,
                            source_id: item.source_id,
                            interval_seconds: Number(interval),
                            expected_revision: 0,
                          })
                        ) {
                          media.setNotice("歌单及歌曲资料已保存到天枢。");
                          await refresh();
                        }
                      }}
                    >
                      {existing ? "已订阅" : "订阅歌单"}
                    </button>
                  </article>
                );
              })}
            </div>
          )}
        </section>
      </DetailCard>
      <section className="media-card media-stack" aria-label="本地歌单">
        <div>
          <h3>天枢中的歌单</h3>
          <p className="muted">
            成员关系保存在数据库中；远端暂时不可用时，仍可查看上次同步结果。
          </p>
        </div>
        {!view.playlists.length && (
          <p className="muted">还没有订阅歌单。连接账号后，从上方选择。</p>
        )}
        {view.playlists.map((item) => (
          <article className="media-stack" key={item.playlist_id}>
            <div className="music-playlist">
              <div>
                <strong>{item.name}</strong>
                <p className="muted">
                  {item.member_count} 首 · {word(item.state)} ·{" "}
                  {timestamp(item.synced_at)}
                  {" · "}每 {item.interval_seconds / 3600} 小时同步
                </p>
                {item.code !== "ready" && (
                  <p className="media-warning">
                    {word(item.code)}，保留上次完整歌单。
                  </p>
                )}
              </div>
              <div className="music-actions">
                <button
                  className="button"
                  disabled={!!reading}
                  onClick={() => void readDetail(item)}
                >
                  查看歌曲
                </button>
                <button
                  className="button"
                  disabled={
                    !!reading || !!media.busy || item.state !== "active"
                  }
                  onClick={async () => {
                    setReading("scan");
                    if (
                      await media.request(
                        "music-scan",
                        "music/playlists/scan",
                        {
                          playlist_id: item.playlist_id,
                        },
                      )
                    ) {
                      await refresh();
                    }
                    setReading("");
                  }}
                >
                  立即同步
                </button>
                <button
                  className="button"
                  disabled={!!media.busy || !!reading}
                  onClick={async () => {
                    if (
                      await media.command("music/playlists/control", {
                        playlist_id: item.playlist_id,
                        expected_revision: item.revision,
                        action: item.state === "active" ? "pause" : "resume",
                      })
                    )
                      await refresh();
                  }}
                >
                  {item.state === "active" ? "暂停同步" : "恢复同步"}
                </button>
                <button
                  className="button"
                  disabled={!!media.busy || !!reading}
                  onClick={async () => {
                    if (
                      await media.command("music/playlists/control", {
                        playlist_id: item.playlist_id,
                        expected_revision: item.revision,
                        action: "remove",
                      })
                    ) {
                      if (
                        detailCursor.current?.playlist_id === item.playlist_id
                      ) {
                        detailCursor.current = null;
                        media.cancel("music-detail");
                        setDetail(null);
                      }
                      media.setNotice(
                        "订阅已删除，后续同步和自动下载停止；已入队任务、音乐文件及已发布的 Navidrome 歌单保留。",
                      );
                      await refresh();
                    }
                  }}
                >
                  <Trash2 aria-hidden="true" />
                  删除订阅
                </button>
              </div>
            </div>
            <MusicDownloads
              media={media}
              playlist={item}
              configured={!!view.settings.directory}
              refresh={refresh}
            />
            {view.navidrome?.connection?.enabled && (
              <NavidromePlaylist
                key={
                  view.navidrome.playlists.find(
                    (link) => link.playlist_id === item.playlist_id,
                  )?.revision ?? 0
                }
                media={media}
                playlist={item}
                refresh={refresh}
                link={view.navidrome.playlists.find(
                  (link) => link.playlist_id === item.playlist_id,
                )}
              />
            )}
          </article>
        ))}
      </section>
      {detail && (
        <Drawer
          title={detail.playlist.name}
          variant="card"
          open={true}
          onClose={() => {
            detailCursor.current = null;
            media.cancel("music-detail");
            setDetail(null);
          }}
        >
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
          <section className="media-card media-stack" aria-label="歌单歌曲">
            <div className="media-section-head">
              <div>
                <h3>{detail.playlist.name}</h3>
                <p className="muted">
                  按远端歌单顺序显示，共 {detail.playlist.member_count} 首。
                </p>
              </div>
              <button
                className="button"
                onClick={() => {
                  detailCursor.current = null;
                  media.cancel("music-detail");
                  setDetail(null);
                }}
              >
                收起歌曲
              </button>
            </div>
            <div className="music-track-table">
              <table>
                <thead>
                  <tr>
                    <th>顺序</th>
                    <th>歌曲</th>
                    <th>艺术家</th>
                    <th>专辑</th>
                    <th>本地音频</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.tracks.map((track) => (
                    <tr key={track.position}>
                      <td>{track.position}</td>
                      <td>
                        {track.title || `暂不可用的歌曲 (${track.source_id})`}
                      </td>
                      <td>{track.artists.join("、") || "未知"}</td>
                      <td>{track.album || "未知"}</td>
                      <td>
                        <span>
                          {track.audio_state === "not_downloaded"
                            ? "未下载"
                            : track.audio_state === "completed"
                              ? "已归档"
                              : word(track.audio_state)}
                        </span>
                        {track.audio_quality && (
                          <p className="muted">
                            {audioQualityText(track.audio_quality)}
                          </p>
                        )}
                        {track.file && (
                          <p className="music-file">{track.file}</p>
                        )}
                        {track.audio_unlock && (
                          <p className="muted">
                            {unlockText(track.audio_unlock)}
                          </p>
                        )}
                        {track.download_provider && (
                          <p className="muted">
                            文件来源：{providerNames[track.download_provider]}
                          </p>
                        )}
                        {track.audio_code &&
                          !["queued", "music_archived"].includes(
                            track.audio_code,
                          ) && (
                            <p className="muted">{word(track.audio_code)}</p>
                          )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="music-actions">
              <button
                className="button"
                disabled={detail.offset === 0 || !!reading}
                onClick={() =>
                  void readDetail(
                    detail.playlist,
                    Math.max(0, detail.offset - 100),
                  )
                }
              >
                上一页
              </button>
              <button
                className="button"
                disabled={!detail.more || !!reading}
                onClick={() =>
                  void readDetail(detail.playlist, detail.offset + 100)
                }
              >
                下一页
              </button>
            </div>
          </section>
        </Drawer>
      )}
    </section>
  );
}
