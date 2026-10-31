import { DetailCard } from "../../../components/DetailCard";
import { useState } from "react";
import type { MediaController } from "../useMediaController";
import { timestamp } from "../wording";
import type { NavidromeConnection as Connection } from "./types";

export function NavidromeConnection({
  media,
  connection,
  refresh,
}: {
  media: MediaController;
  connection?: Connection | null;
  refresh: () => Promise<void>;
}) {
  const [baseUrl, setBaseUrl] = useState(connection?.base_url ?? "");
  const [username, setUsername] = useState(connection?.username ?? "");
  const [password, setPassword] = useState("");
  const [directory, setDirectory] = useState(connection?.music_directory ?? "");
  const [folder, setFolder] = useState(connection?.music_folder_id ?? "");
  const [folders, setFolders] = useState(connection?.folders ?? []);
  const [testing, setTesting] = useState(false);
  const [checked, setChecked] = useState("");
  const busy = testing || !!media.busy;
  const credentials = { base_url: baseUrl, username, password };
  return (
    <section className="media-card media-stack" aria-label="Navidrome 连接">
      <div className="media-section-head">
        <div>
          <h3>Navidrome 播放与歌单</h3>
          <p className="muted">
            连接你的音乐服务器，把天枢歌单同步为可播放的 Navidrome
            歌单，共用音乐存档中的音频。
          </p>
        </div>
        {connection && (
          <a
            className="button"
            href={connection.base_url}
            target="_blank"
            rel="noreferrer"
          >
            打开 Navidrome
          </a>
        )}
      </div>
      {connection && (
        <p>
          {connection.enabled ? "已连接" : "连接已停用"} · {connection.username}{" "}
          · {connection.version}
          <br />
          <span className="muted">
            上次验证：{timestamp(connection.checked_at)}
          </span>
        </p>
      )}
      <DetailCard title={<>{connection ? "编辑连接" : "连接 Navidrome"}</>}>
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
          className="media-stack navidrome-form"
          onSubmit={async (event) => {
            event.preventDefault();
            if (
              await media.command("music/navidrome/save", {
                ...credentials,
                music_folder_id: folder,
                music_directory: directory,
                expected_revision: connection?.revision ?? 0,
              })
            ) {
              setPassword("");
              await refresh();
            }
          }}
        >
          <div className="music-settings-grid">
            <label>
              Navidrome 地址
              <input
                required
                type="url"
                value={baseUrl}
                placeholder="http://你的NAS:4533"
                onChange={(e) => {
                  setBaseUrl(e.target.value);
                  setChecked("");
                }}
              />
            </label>
            <label>
              Navidrome 用户名
              <input
                required
                autoComplete="username"
                value={username}
                onChange={(e) => {
                  setUsername(e.target.value);
                  setChecked("");
                }}
              />
            </label>
            <label>
              Navidrome 密码
              <input
                type="password"
                autoComplete="new-password"
                value={password}
                required={!connection}
                placeholder={connection ? "留空保留已保存密码" : "登录密码"}
                onChange={(e) => {
                  setPassword(e.target.value);
                  setChecked("");
                }}
              />
            </label>
            <label>
              Navidrome 中的音乐存档路径
              <input
                required
                value={directory}
                placeholder="/music/音乐存档"
                onChange={(e) => setDirectory(e.target.value)}
              />
            </label>
          </div>
          <div className="music-actions">
            <button
              type="button"
              className="button"
              disabled={busy || !baseUrl || !username}
              onClick={async () => {
                setTesting(true);
                setChecked("");
                const result = await media.request<{
                  version: string;
                  folders: { id: string; name: string }[];
                }>("navidrome-test", "music/navidrome/test", credentials);
                if (result) {
                  setFolders(result.folders);
                  if (!result.folders.some((f) => f.id === folder))
                    setFolder(result.folders[0]?.id ?? "");
                  setChecked(`连接成功 · ${result.version}`);
                }
                setTesting(false);
              }}
            >
              {testing ? "正在连接…" : "测试连接并读取音乐库"}
            </button>
          </div>
          {checked && <p role="status">{checked}</p>}
          <label>
            Navidrome 音乐库
            <select
              required
              value={folder}
              onChange={(e) => setFolder(e.target.value)}
            >
              <option value="">请先测试连接</option>
              {folders.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
            </select>
          </label>
          <p className="muted">
            两个服务需要能读取同一份音乐存档。密码加密保存；没有扫描权限的账号会等待
            Navidrome 定期入库。
          </p>
          <div className="music-actions">
            <button
              className="button button-primary"
              disabled={busy || !folder}
            >
              保存 Navidrome 连接
            </button>
            {connection?.enabled && (
              <button
                type="button"
                className="button"
                disabled={busy}
                onClick={async () => {
                  if (
                    await media.command("music/navidrome/disable", {
                      expected_revision: connection.revision,
                    })
                  )
                    await refresh();
                }}
              >
                停用连接
              </button>
            )}
          </div>
        </form>
      </DetailCard>
    </section>
  );
}
