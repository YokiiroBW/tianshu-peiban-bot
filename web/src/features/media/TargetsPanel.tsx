import { Drawer } from "../../components/Drawer";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { StatusRail } from "../../components/StatusRail";
import type { MediaController } from "./useMediaController";
import type { Target } from "./types";
import { tone, word } from "./wording";

const empty = () => ({
  target_id: "",
  expected_revision: 0,
  label: "",
  kind: "jellyfin",
  base_url: "",
  token: "",
  library_id: "",
  media_root: "",
  directory: "",
});
type Library = { library_id: string; label: string; paths: string[] };

export function TargetsPanel({ media }: { media: MediaController }) {
  const [form, setForm] = useState(empty);
  const [editing, setEditing] = useState(false);
  const [libraries, setLibraries] = useState<Library[]>([]);
  const [testing, setTesting] = useState(false);
  const generation = useRef(0);
  const current = useRef(media);
  current.current = media;
  useEffect(
    () => () => {
      generation.current++;
      current.current.cancel("target-test");
    },
    [],
  );
  const reset = () => {
    generation.current++;
    media.cancel("target-test");
    setTesting(false);
    setLibraries([]);
    setForm(empty());
    setEditing(false);
  };
  const update = (key: keyof ReturnType<typeof empty>, value: string) => {
    generation.current++;
    if (["kind", "base_url", "token"].includes(key)) {
      setLibraries([]);
      media.cancel("target-test");
      setTesting(false);
    }
    setForm((old) => ({ ...old, [key]: value }));
  };
  const edit = (target: Target) => {
    reset();
    const server = target.servers[0];
    setForm({
      target_id: target.target_id,
      expected_revision: target.revision ?? 0,
      label: target.label,
      directory: target.directory ?? "",
      kind: server.kind,
      base_url: server.base_url ?? "",
      token: "",
      library_id: server.library_id ?? "",
      media_root: server.media_root ?? "",
    });
    setEditing(true);
  };
  const test = async () => {
    const mine = ++generation.current;
    setTesting(true);
    const result = await media.request<{ libraries: Library[] }>(
      "target-test",
      "targets/test",
      {
        target_id: form.target_id || null,
        kind: form.kind,
        base_url: form.base_url.trim(),
        token: form.token,
      },
    );
    if (mine !== generation.current) return;
    setTesting(false);
    if (result) {
      setLibraries(result.libraries);
      media.setNotice(
        result.libraries.length
          ? "连接成功，请选择服务器上的媒体库。"
          : "连接成功，服务器还没有可用的视频媒体库。",
      );
    }
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    const result = await media.command("targets/save", {
      ...form,
      target_id: form.target_id || null,
    });
    if (result) {
      reset();
      media.setNotice("媒体服务器和整理目录已保存，可在订阅和下载中选择。");
    }
  };
  return (
    <section className="media-stack" aria-label="媒体服务器">
      <div className="media-section-head">
        <div>
          <h2>媒体服务器</h2>
          <p className="muted">
            连接 Emby、Jellyfin 或
            Plex。天枢将下载成品整理到指定目录，并通知服务器更新媒体库。
          </p>
        </div>
        <button
          className="button"
          onClick={() => {
            reset();
            setEditing(true);
          }}
        >
          添加媒体服务器
        </button>
      </div>
      {editing && (
        <Drawer
          title={form.target_id ? "编辑媒体服务器" : "添加媒体服务器"}
          variant="card"
          open={true}
          onClose={reset}
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
          <form
            className="media-card media-stack"
            onSubmit={(event) => void save(event)}
          >
            <h3>{form.target_id ? "编辑媒体服务器" : "添加媒体服务器"}</h3>
            <label>
              显示名称
              <input
                required
                maxLength={128}
                value={form.label}
                onChange={(e) => update("label", e.target.value)}
              />
            </label>
            <label>
              服务器类型
              <select
                value={form.kind}
                onChange={(e) => update("kind", e.target.value)}
              >
                <option value="emby">Emby</option>
                <option value="jellyfin">Jellyfin</option>
                <option value="plex">Plex</option>
              </select>
            </label>
            <label>
              服务器地址
              <input
                required
                type="url"
                placeholder="http://192.168.1.10:8096"
                value={form.base_url}
                onChange={(e) => update("base_url", e.target.value)}
              />
            </label>
            <label>
              API Key / Plex Token
              <input
                type="password"
                autoComplete="new-password"
                required={!form.target_id}
                placeholder={
                  form.target_id ? "留空保留已保存的密钥" : "输入服务器密钥"
                }
                value={form.token}
                onChange={(e) => update("token", e.target.value)}
              />
            </label>
            <button
              type="button"
              className="button"
              disabled={
                testing ||
                !!media.busy ||
                !form.base_url ||
                (!form.token && !form.target_id)
              }
              onClick={() => void test()}
            >
              {testing ? "正在连接…" : "测试连接并读取媒体库"}
            </button>
            {libraries.length > 0 ? (
              <label>
                服务器媒体库
                <select
                  required
                  value={form.library_id}
                  onChange={(e) => {
                    const item = libraries.find(
                      (item) => item.library_id === e.target.value,
                    );
                    update("library_id", e.target.value);
                    if (item?.paths.length === 1)
                      update("media_root", item.paths[0]);
                  }}
                >
                  <option value="">选择媒体库</option>
                  {libraries.map((item) => (
                    <option key={item.library_id} value={item.library_id}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <label>
                服务器媒体库 ID
                <input
                  required
                  value={form.library_id}
                  onChange={(e) => update("library_id", e.target.value)}
                />
                <small className="muted">
                  测试连接可读取并选择已有媒体库，也可直接填写 ID。
                </small>
              </label>
            )}
            <label>
              整理目录（天枢可见）
              <input
                required
                placeholder="/media/bilibili"
                value={form.directory}
                onChange={(e) => update("directory", e.target.value)}
              />
            </label>
            <label>
              对应目录（媒体服务器可见）
              <input
                required
                placeholder="/library/bilibili"
                value={form.media_root}
                onChange={(e) => update("media_root", e.target.value)}
              />
            </label>
            <p className="muted">
              两个路径必须映射到同一份实际文件。整理目录需挂载到天枢容器；服务器负责索引和播放。Plex
              核对文件是否入库，NFO 展示取决于其元数据代理。
            </p>
            <div className="media-actions">
              <button className="button primary" disabled={!!media.busy}>
                保存媒体服务器
              </button>
              <button type="button" className="button" onClick={reset}>
                取消编辑
              </button>
            </div>
          </form>
        </Drawer>
      )}
      {!media.view?.targets.length && (
        <p className="muted">
          尚未添加媒体服务器。添加后即可选择订阅和下载的目标媒体库。
        </p>
      )}
      <div className="media-targets">
        {media.view?.targets.map((target) => (
          <article className="media-card media-stack" key={target.target_id}>
            <div className="media-section-head">
              <h3>{target.label}</h3>
              <StatusRail
                tone={tone(target.state)}
                label={word(target.state)}
              />
            </div>
            {target.directory && (
              <p className="muted" style={{ overflowWrap: "anywhere" }}>
                整理目录：{target.directory}
              </p>
            )}
            {target.servers.map((server) => (
              <p key={server.server_id} style={{ overflowWrap: "anywhere" }}>
                {server.kind} · {server.base_url ?? server.label}
                {server.media_root && ` · ${server.media_root}`}
              </p>
            ))}
            {target.kind === "assetlibrary" && (
              <p className="muted">
                已有 AssetLibrary 目标，通过部署配置维护。
              </p>
            )}
            {target.revision != null && (
              <div className="media-actions">
                <button
                  className="button"
                  disabled={!!media.busy}
                  onClick={() => edit(target)}
                >
                  编辑连接
                </button>
                <button
                  className="button"
                  disabled={!!media.busy}
                  onClick={() =>
                    void media.command("targets/remove", {
                      target_id: target.target_id,
                      expected_revision: target.revision,
                    })
                  }
                >
                  移除连接
                </button>
              </div>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
