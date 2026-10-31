import { DetailCard } from "../../../components/DetailCard";
import { useState } from "react";
import type {
  Connection,
  Kind,
  Media,
  Mutate,
  Settings,
  Target,
} from "./types";
import { kinds } from "./types";
import { word } from "../wording";

type Probe = Record<string, { state: string; code?: string; value?: unknown }>;
const names = [
  ["suwayomi", "Suwayomi 下载源"],
  ["assetlibrary", "AssetLibrary 归档"],
  ["komga", "Komga 阅读"],
] as const;
const emptyConnection: Connection = {
  url: "",
  username: "",
  has_credentials: false,
};
const emptyTarget: Target = {
  directory: "",
  asset_target_id: "",
  asset_library_id: "",
  komga_library_id: "",
  komga_root: "",
};

export function ComicSettings({
  settings,
  media,
  mutate,
}: {
  settings: Settings;
  media: Media;
  mutate: Mutate;
}) {
  const [connections, setConnections] = useState(settings.connections);
  const [targets, setTargets] = useState(settings.targets);
  const [directory, setDirectory] = useState(settings.import_directory);
  const [interval, setInterval] = useState(settings.interval_seconds);
  const [probe, setProbe] = useState<Probe>();
  const targetChange = (kind: Kind, key: keyof Target, value: string) =>
    setTargets((old) => ({
      ...old,
      [kind]: { ...emptyTarget, ...old[kind], [key]: value },
    }));
  return (
    <DetailCard title={"漫画下载与阅读设置"}>
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
        className="comic-stack"
        onSubmit={(e) => {
          e.preventDefault();
          void mutate(
            "settings/save",
            {
              connections,
              targets,
              import_directory: directory,
              interval_seconds: interval,
              expected_revision: settings.revision,
            },
            "漫画连接与整理设置已保存。",
          );
        }}
      >
        <div className="media-card comic-stack">
          <h3>连接 NAS 服务</h3>
          <p className="muted">
            使用现有服务地址与凭据。密码和令牌加密保存，已保存的凭据留空即可保留。
          </p>
          {names.map(([key, label]) => {
            const c = connections[key] ?? emptyConnection;
            const change = (field: keyof Connection, value: string) =>
              setConnections((old) => ({
                ...old,
                [key]: { ...c, [field]: value },
              }));
            return (
              <fieldset key={key} className="comic-stack">
                <legend>{label}</legend>
                <label>
                  服务地址
                  <input
                    type="url"
                    value={c.url}
                    onChange={(e) => change("url", e.target.value)}
                    placeholder="http://NAS地址:端口"
                  />
                </label>
                <div className="comic-field-grid">
                  {key === "suwayomi" && (
                    <>
                      <label>
                        用户名（已启用认证时）
                        <input
                          value={c.username}
                          onChange={(e) => change("username", e.target.value)}
                          autoComplete="off"
                        />
                      </label>
                      <label>
                        密码
                        <input
                          type="password"
                          value={c.password ?? ""}
                          onChange={(e) => change("password", e.target.value)}
                          placeholder={
                            c.has_credentials
                              ? "已保存，留空保留"
                              : "未启用认证可留空"
                          }
                          autoComplete="new-password"
                        />
                      </label>
                    </>
                  )}
                  {key !== "suwayomi" && (
                    <label>
                      {key === "komga" ? "API 密钥" : "服务凭据"}
                      <input
                        type="password"
                        value={c.token ?? ""}
                        onChange={(e) => change("token", e.target.value)}
                        placeholder={
                          c.has_credentials
                            ? "已保存，留空保留"
                            : "填写此连接的专用凭据"
                        }
                        autoComplete="new-password"
                      />
                    </label>
                  )}
                </div>
                {key === "assetlibrary" && (
                  <label>
                    HTTPS 信任证书（使用自签发证书时）
                    <textarea
                      rows={3}
                      value={c.ca_pem ?? ""}
                      onChange={(e) => change("ca_pem", e.target.value)}
                      placeholder="粘贴此 NAS 服务的 CA 证书 PEM；使用系统信任证书时留空"
                    />
                  </label>
                )}
              </fieldset>
            );
          })}
          <div className="comic-actions">
            <button className="button primary" disabled={!!media.busy}>
              保存设置
            </button>
            <button
              type="button"
              className="button"
              onClick={() => {
                void media
                  .request<Probe>("comic-probe", "comics/connections/probe", {})
                  .then((r) => {
                    if (r) setProbe(r);
                  });
              }}
            >
              测试已保存的连接
            </button>
          </div>
          {probe && (
            <ul>
              {[
                ...names,
                ["publisher", "NAS 成品写入"],
                ["inputs", "NAS 原图读取"],
              ]
                .filter(([key]) => !!probe[key])
                .map(([key, label]) => (
                  <li key={key}>
                    {label}：
                    {probe[key]?.state === "ready"
                      ? "连接成功"
                      : word(probe[key]?.code ?? "unavailable")}
                  </li>
                ))}
            </ul>
          )}
        </div>
        <div className="media-card comic-stack">
          <h3>目录与追更</h3>
          <label>
            NAS 图片输入目录
            <input
              value={directory}
              onChange={(e) => setDirectory(e.target.value)}
              placeholder="例如 /media/Cartoon"
            />
          </label>
          <p className="muted">
            {settings.import_socket
              ? "已连接 NAS 只读输入服务，导入时原文件保持原样。"
              : "填写天枢容器内已挂载的漫画目录；导入时只读取原文件。"}
          </p>
          <label>
            默认追更周期
            <select
              value={interval}
              onChange={(e) => setInterval(Number(e.target.value))}
            >
              <option value={3600}>每小时</option>
              <option value={21600}>每 6 小时</option>
              <option value={86400}>每天</option>
            </select>
          </label>
        </div>
        <div className="media-card comic-stack">
          <h3>归档与阅读映射</h3>
          <p className="muted">
            归档目标和 Komga 必须指向同一批成品。填写 Komga
            容器看到的目录，不是电脑上的网络共享路径。
          </p>
          {kinds.map(([kind, label]) => {
            const t = targets[kind] ?? emptyTarget;
            return (
              <fieldset className="comic-field-grid" key={kind}>
                <legend>{label}</legend>
                {(
                  [
                    ["directory", "天枢成品目录"],
                    ["asset_target_id", "AssetLibrary 受管目标"],
                    ["asset_library_id", "AssetLibrary 库标识"],
                    ["komga_library_id", "Komga 库标识"],
                    ["komga_root", "Komga 成品目录"],
                  ] as [keyof Target, string][]
                ).map(([key, title]) => (
                  <label key={key}>
                    {title}
                    <input
                      value={t[key]}
                      onChange={(e) => targetChange(kind, key, e.target.value)}
                      placeholder={
                        key === "asset_target_id"
                          ? kind === "general"
                            ? "comic-main"
                            : "comic-adult"
                          : key === "komga_root"
                            ? kind === "general"
                              ? "/Cartoon/漫画/天枢"
                              : "/Cartoon/本子/天枢"
                            : ""
                      }
                    />
                  </label>
                ))}
              </fieldset>
            );
          })}
          <button className="button primary" disabled={!!media.busy}>
            保存设置
          </button>
        </div>
      </form>
    </DetailCard>
  );
}
