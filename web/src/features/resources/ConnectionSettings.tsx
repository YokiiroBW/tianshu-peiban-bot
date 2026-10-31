import { useState } from "react";
import { useDomainCommand } from "../../api/useDomainResource";
import type { AssetConfiguration } from "./api";

const empty: AssetConfiguration = { id: "", name: "", endpoint: "", revision: 0, enabled: true, configured: false, quota_group: "" };
export function ConnectionSettings({ rows, onSaved }: { rows: AssetConfiguration[]; onSaved: () => void }) {
  const [value, setValue] = useState<AssetConfiguration>(empty);
  const [token, setToken] = useState("");
  const [clearCredential, setClearCredential] = useState(false);
  const command = useDomainCommand<AssetConfiguration>("asset-connections", "global");
  function select(id: string) {
    setValue(rows.find((row) => row.id === id) ?? empty);
    setToken(""); setClearCredential(false);
  }
  return <details className="panel asset-connection-settings"><summary>管理资产库连接</summary>
    <p>登记 AssetLibrary 的只读 AssetLink 接口；每次读取仍由上游核验权限。</p>
    <label>已有连接<select value={value.revision ? value.id : ""} onChange={(event) => select(event.target.value)}>
      <option value="">新增连接</option>{rows.map((row) => <option key={row.id} value={row.id}>{row.name || row.id}</option>)}
    </select></label>
    <form className="asset-settings-form" onSubmit={async (event) => {
      event.preventDefault();
      const saved = await command.run("resources/assets/connections.save", {
        ...value, ...(token ? { token } : {}), clear_credential: clearCredential,
      }, value.revision);
      if (saved) { setValue(saved.data); setToken(""); setClearCredential(false); onSaved(); }
    }}>
      <label>连接标识<input required readOnly={value.revision > 0} value={value.id} onChange={(event) => setValue({ ...value, id: event.target.value })} /></label>
      <label>名称<input value={value.name ?? ""} onChange={(event) => setValue({ ...value, name: event.target.value })} /></label>
      <label>HTTPS AssetLink 地址<input required type="url" placeholder="https://assets.example/assetlink/v1/control" value={value.endpoint} onChange={(event) => setValue({ ...value, endpoint: event.target.value })} /></label>
      <label>容量组<input required value={value.quota_group ?? ""} onChange={(event) => setValue({ ...value, quota_group: event.target.value })} /></label>
      <label>访问令牌<input type="password" autoComplete="new-password" placeholder={value.configured ? "已保存，留空保留" : "尚未配置"} value={token} onChange={(event) => setToken(event.target.value)} /></label>
      <label><input type="checkbox" checked={clearCredential} onChange={(event) => setClearCredential(event.target.checked)} />清除已保存令牌</label>
      <label>自定义 CA 文件（服务器绝对路径，可选）<input value={value.ca_file ?? ""} onChange={(event) => setValue({ ...value, ca_file: event.target.value || undefined })} /></label>
      <label><input type="checkbox" checked={value.enabled} onChange={(event) => setValue({ ...value, enabled: event.target.checked })} />启用连接</label>
      <button className="button" disabled={command.busy}>保存连接</button>
    </form>
    {command.error && <p role="alert">{command.error}</p>}
    {command.result?.receipt?.execution === "succeeded" && <p role="status">连接配置已保存；可在上方选择后读取。</p>}
  </details>;
}
