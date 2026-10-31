import { DetailCard } from "../../../components/DetailCard";
import { useState } from "react";
import type { BookSettings as Settings } from "./types";

export function BookSettings({
  settings,
  busy,
  onSave,
}: {
  settings: Settings;
  busy: boolean;
  onSave: (body: object) => void;
}) {
  const [directory, setDirectory] = useState(settings.directory);
  const [interval, setInterval] = useState(settings.interval_seconds);
  const [automatic, setAutomatic] = useState(settings.auto_metadata);
  const [online, setOnline] = useState(settings.open_library);
  const [opds, setOpds] = useState(settings.opds_url);
  const [username, setUsername] = useState(settings.opds_username ?? "");
  const [password, setPassword] = useState("");
  return (
    <DetailCard title={"书籍下载与整理设置"}>
      <form
        className="media-card book-settings media-stack"
        onSubmit={(event) => {
          event.preventDefault();
          onSave({
            directory,
            interval_seconds: interval,
            auto_metadata: automatic,
            open_library: online,
            opds_url: opds,
            opds_username: username,
            opds_password: password,
            expected_revision: settings.revision,
          });
        }}
      >
        <h3>下载与整理</h3>
        <label>
          书籍存档目录
          <input
            value={directory}
            onChange={(event) => setDirectory(event.target.value)}
            placeholder="例如 /media/Books"
            autoComplete="off"
          />
        </label>
        <p className="muted">
          填写天枢运行环境中已经挂载的书籍目录。成品按“类别 / 作者 / 书名 [标识]
          /
          book.epub”整理；类别优先采用书籍资料，可手动修改，无法识别时归入未分类。
          修改目录或类别后重新整理会生成新位置的成品，保留旧文件。
        </p>
        <label>
          默认追更周期
          <select
            value={interval}
            onChange={(event) => setInterval(Number(event.target.value))}
          >
            <option value={3600}>每小时</option>
            <option value={21600}>每 6 小时</option>
            <option value={86400}>每天</option>
          </select>
        </label>
        <label className="book-checkbox">
          <input
            type="checkbox"
            checked={automatic}
            onChange={(event) => setAutomatic(event.target.checked)}
          />
          明确匹配后自动补齐缺失资料
        </label>
        <p className="muted">
          人工编辑保持优先。无法确定的版本资料留待选择，不影响章节下载。
        </p>
        <h3>连接元数据书库</h3>
        <label>
          OPDS / Calibre 书库地址
          <input
            type="url"
            value={opds}
            onChange={(event) => setOpds(event.target.value)}
            placeholder="https://书库地址/opds"
          />
        </label>
        <div className="book-field-grid">
          <label>
            书库用户名
            <input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="off"
            />
          </label>
          <label>
            书库密码
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder={
                settings.opds_has_credentials
                  ? "已保存，留空保留"
                  : "书库未启用认证时可留空"
              }
              autoComplete="new-password"
            />
          </label>
        </div>
        <label className="book-checkbox">
          <input
            type="checkbox"
            checked={online}
            onChange={(event) => setOnline(event.target.checked)}
          />
          搜索 Open Library 在线书目
        </label>
        <p className="muted">
          网文资料优先使用书源详情；出版书目可从本地、已连接书库和在线候选补全。
        </p>
        <div>
          <button className="button primary" disabled={busy} type="submit">
            保存整理设置
          </button>
        </div>
      </form>
    </DetailCard>
  );
}
