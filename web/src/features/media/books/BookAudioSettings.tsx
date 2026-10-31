import { DetailCard } from "../../../components/DetailCard";
import { useEffect, useRef, useState } from "react";
import type { MediaController } from "../useMediaController";
import type { AudioSettings, SpeechConfig } from "./audioTypes";

export function BookAudioSettings({ media }: { media: MediaController }) {
  const current = useRef(media);
  current.current = media;
  const [data, setData] = useState<AudioSettings | null>(null);
  const [provider, setProvider] = useState("edge");
  const [config, setConfig] = useState<SpeechConfig | null>(null);
  const [key, setKey] = useState("");
  async function load() {
    const result = await current.current.request<AudioSettings>(
      "book-speech-settings",
      "books/audio/settings",
      {},
    );
    if (result) setData(result);
  }
  useEffect(() => {
    void load();
    return () => current.current.cancel("book-speech-settings");
  }, []);
  useEffect(() => {
    setConfig(data?.providers.find((p) => p.provider === provider) ?? null);
    setKey("");
  }, [data, provider]);
  if (!config || !data) return <p role="status">正在读取听书设置…</p>;
  return (
    <DetailCard title={"听书服务设置"}>
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
        className="media-card book-settings media-stack"
        onSubmit={async (event) => {
          event.preventDefault();
          const saved = await media.command("books/audio/settings/save", {
            ...config,
            api_key: key || undefined,
            expected_revision: config.revision,
          });
          if (saved) {
            setKey("");
            await load();
            media.setNotice("听书配置已保存；生成时使用所选服务。");
          }
        }}
      >
        <h3>听书服务</h3>
        <p className="muted">
          音频保存在书籍分类目录的 audio
          文件夹。配置服务后，在书籍内选择要提前生成的章节。
        </p>
        <label>
          语音服务
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
          >
            <option value="edge">Edge 在线朗读</option>
            <option value="fish">Fish Audio</option>
            <option value="local">本地兼容接口</option>
          </select>
        </label>
        {provider === "edge" ? (
          <>
            <label>
              Edge 音色
              <select
                value={config.voice}
                onChange={(e) =>
                  setConfig({ ...config, voice: e.target.value })
                }
              >
                {data.voices.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
            </label>
            <p className="muted">
              使用 Edge 在线朗读服务，无需 API
              Key；服务可用性取决于微软在线端。音频生成后可离线保存在 NAS。
            </p>
          </>
        ) : (
          <>
            {provider === "local" && (
              <label>
                本地服务地址
                <input
                  value={config.endpoint}
                  onChange={(e) =>
                    setConfig({ ...config, endpoint: e.target.value })
                  }
                  placeholder="http://192.168.31.100:8000"
                />
              </label>
            )}
            <label>
              {provider === "fish" ? "Fish 模型" : "模型名称"}
              {provider === "fish" ? (
                <select
                  value={config.model}
                  onChange={(e) =>
                    setConfig({ ...config, model: e.target.value })
                  }
                >
                  {data.fish_models.map((m) => (
                    <option key={m}>{m}</option>
                  ))}
                </select>
              ) : (
                <input
                  value={config.model}
                  onChange={(e) =>
                    setConfig({ ...config, model: e.target.value })
                  }
                />
              )}
            </label>
            <label>
              {provider === "fish" ? "Fish 音色 ID" : "音色名称"}
              <input
                value={config.voice}
                onChange={(e) =>
                  setConfig({ ...config, voice: e.target.value })
                }
              />
            </label>
            <label>
              API Key{" "}
              {config.has_key
                ? "（已保存，留空保留）"
                : provider === "local"
                  ? "（可选）"
                  : ""}
              <input
                type="password"
                autoComplete="new-password"
                value={key}
                onChange={(e) => setKey(e.target.value)}
              />
            </label>
            <p className="muted">
              {provider === "fish"
                ? "仅手动选择 Fish 生成时使用额度。免费模型是否可用由你的账号决定，失败不会切换到付费模型。"
                : "需先运行自己的语音服务，提供 /v1/audio/speech 接口。本页不会安装模型。"}
            </p>
          </>
        )}
        <div className="book-field-grid">
          <label>
            生成语速
            <input
              type="number"
              min="0.5"
              max="2"
              step="0.05"
              value={config.speed}
              onChange={(e) =>
                setConfig({ ...config, speed: Number(e.target.value) })
              }
            />
          </label>
          {provider === "edge" && (
            <label>
              音高（Hz）
              <input
                type="number"
                min="-50"
                max="50"
                value={config.pitch}
                onChange={(e) =>
                  setConfig({ ...config, pitch: Number(e.target.value) })
                }
              />
            </label>
          )}
          <label>
            每日生成字符上限
            <input
              type="number"
              min="0"
              max="10000000"
              value={config.daily_characters}
              onChange={(e) =>
                setConfig({
                  ...config,
                  daily_characters: Number(e.target.value),
                })
              }
            />
          </label>
        </div>
        <p className="book-fineprint muted">
          UTC 当日已申请 {data.usage[provider].toLocaleString()} 字；上限填 0
          表示不限。更改音色会建立新的缓存版本，原音频仍可播放。
        </p>
        <button className="button primary" disabled={!!media.busy}>
          保存听书设置
        </button>
      </form>
    </DetailCard>
  );
}
