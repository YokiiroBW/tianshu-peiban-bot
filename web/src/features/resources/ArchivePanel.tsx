import { ArchiveMappings, type ArchiveMapping } from "./ArchiveMappings";
import { useState } from "react";
import { useDomainCommand, useDomainResource, useDomainTarget } from "../../api/useDomainResource";
import type { SourceRef } from "../../api/contracts";

type ArchiveConnection = { id: string; name?: string; endpoint: string; quota_group?: string; ca_file?: string; enabled: boolean; configured: boolean; revision: number; mappings: ArchiveMapping[] };
type ArchiveView = { available: boolean; reason: string | null; can_manage: boolean; connections: { id: string; name?: string; enabled: boolean; configured: boolean }[]; configurations: ArchiveConnection[] };
type ArchiveRecord = { locator: string; stored_content_sha256: string; text: string; sender_id: string; timestamp: number; source_ref: SourceRef };
type ArchiveResult = { records: ArchiveRecord[]; next_cursor?: string | null; observed_at: string; archive_observed_at: number | null; omitted_records: number; omitted: { locator: string; reason: string }[]; context_complete?: boolean };
const empty: ArchiveConnection = { id: "", endpoint: "", enabled: true, configured: false, revision: 0, mappings: [] };

/** Human history requests remain scoped to the actual selected conversation. */
export function ArchivePanel() {
  const target = useDomainTarget();
  const view = useDomainResource<ArchiveView>("resources/archive/view");
  const command = useDomainCommand<ArchiveResult>("archive-read", "conversation");
  const save = useDomainCommand<ArchiveConnection>("archive-connection", "global");
  const [config, setConfig] = useState<ArchiveConnection>(empty);
  const [token, setToken] = useState("");
  const [clearToken, setClearToken] = useState(false);
  const [connectionId, setConnectionId] = useState("");
  const [keyword, setKeyword] = useState("");
  const [sender, setSender] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [query, setQuery] = useState<object>({});
  const result = command.result?.receipt?.execution === "succeeded" ? command.result.data : null;
  return <details className="panel asset-connection-settings"><summary>聊天档案 · Chat Audit</summary>
    <p>读取当前所选角色会话的历史存档；存档原文保留原作者和时间，仅代表当时记录。档案接口不提供媒体原件。</p>
    {view.error && <p role="alert">{view.error}</p>}
    {!target.conversation_id && <p>请先在角色工作区选择需要查询的会话。</p>}
    {target.conversation_id && view.data && !view.data.available && <p>该会话尚无已配置的档案映射。</p>}
    <form className="asset-settings-form" onSubmit={(event) => {
      event.preventDefault();
      const input = { ...(connectionId ? { connection_id: connectionId } : {}), keyword, ...(sender ? { sender_id: sender } : {}), ...(start ? { start_timestamp: new Date(start).getTime()/1000 } : {}), ...(end ? { end_timestamp: new Date(end).getTime()/1000 } : {}), limit: 20 };
      setQuery(input); void command.run("resources/archive/search", input);
    }}>
      <label>档案连接<select value={connectionId} onChange={(event) => setConnectionId(event.target.value)}>
        <option value="">{view.data?.connections.length === 1 ? view.data.connections[0].name || view.data.connections[0].id : "请选择连接"}</option>
        {view.data?.connections.map((row) => <option key={row.id} value={row.id}>{row.name || row.id}{row.enabled && row.configured ? "" : "（未配置或停用）"}</option>)}
      </select></label>
      <label>正文关键词<input value={keyword} onChange={(event) => setKeyword(event.target.value)} maxLength={500} /></label>
      <label>原作者外部账号（可选）<input value={sender} onChange={(event) => setSender(event.target.value)} /></label>
      <label>开始时间<input type="datetime-local" value={start} onChange={(event) => setStart(event.target.value)} /></label>
      <label>结束时间<input type="datetime-local" value={end} onChange={(event) => setEnd(event.target.value)} /></label>
      <button className="button" disabled={command.busy || !target.conversation_id || !view.data?.available}>查询历史</button>
    </form>
    {command.error && <p role="alert">{command.error}</p>}
    {result && <section aria-label="历史查询结果">
      <p>本次核对：{new Date(result.observed_at).toLocaleString()} · 省略 {result.omitted_records} 条</p>
      {result.records.length === 0 && <p>没有可显示的已授权记录。</p>}
      {result.records.map((row) => <article key={row.locator} className="asset-archive-record">
        <small>{row.sender_id} · {new Date(row.timestamp*1000).toLocaleString()}</small>
        <p style={{ whiteSpace: "pre-wrap" }}>{row.text}</p>
        <button className="button" disabled={command.busy} onClick={() => void command.run("resources/archive/read", { ...(connectionId ? { connection_id: connectionId } : {}), locator: row.locator, expected_stored_content_sha256: row.stored_content_sha256, before: 2, after: 2 })}>重新核对原文与前后文</button>
      </article>)}
      {result.next_cursor && <button className="button" disabled={command.busy} onClick={() => void command.run("resources/archive/search", { ...query, cursor: result.next_cursor })}>读取下一页</button>}
      {result.omitted.length > 0 && <details><summary>省略原因</summary><ul>{result.omitted.map((row) => <li key={row.locator}>{row.reason}</li>)}</ul></details>}
    </section>}
    {view.data?.can_manage && <details><summary>档案连接与会话映射</summary>
      <label>已有连接<select value={config.revision ? config.id : ""} onChange={(event) => {
        const row = view.data?.configurations.find((item) => item.id === event.target.value) ?? empty;
        setConfig(row); setToken(""); setClearToken(false);
      }}><option value="">新增连接</option>{view.data.configurations.map((row) => <option key={row.id} value={row.id}>{row.name || row.id}</option>)}</select></label>
      <form className="asset-settings-form" onSubmit={async (event) => {
        event.preventDefault();
        const answer = await save.run("resources/archive/connections.save", { ...config, ...(token ? { token } : {}), clear_credential: clearToken }, config.revision);
        if (answer) { setConfig(answer.data); setToken(""); setClearToken(false); await view.refresh(); }
      }}>
        <label>连接标识<input required readOnly={config.revision > 0} value={config.id} onChange={(event) => setConfig({ ...config, id: event.target.value })} /></label>
        <label>名称<input value={config.name ?? ""} onChange={(event) => setConfig({ ...config, name: event.target.value })} /></label>
        <label>HTTPS 服务根地址<input type="url" required value={config.endpoint} onChange={(event) => setConfig({ ...config, endpoint: event.target.value })} /></label>
        <label>容量组<input required value={config.quota_group ?? ""} onChange={(event) => setConfig({ ...config, quota_group: event.target.value })} /></label>
        <label>服务令牌<input type="password" autoComplete="new-password" placeholder={config.configured ? "已配置，留空保留" : "尚未配置"} value={token} onChange={(event) => setToken(event.target.value)} /></label>
        <label><input type="checkbox" checked={clearToken} onChange={(event) => setClearToken(event.target.checked)} />清除已保存令牌</label>
        <label>自定义 CA 文件（服务器绝对路径，可选）<input value={config.ca_file ?? ""} onChange={(event) => setConfig({ ...config, ca_file: event.target.value || undefined })} /></label>
        <ArchiveMappings value={config.mappings} onChange={(mappings) => setConfig({ ...config, mappings })} />
        <label><input type="checkbox" checked={config.enabled} onChange={(event) => setConfig({ ...config, enabled: event.target.checked })} />启用</label>
        <button className="button" disabled={save.busy}>保存连接</button>
      </form>
      {save.error && <p role="alert">{save.error}</p>}
      {save.result?.receipt?.execution === "succeeded" && <p role="status">档案配置已保存。</p>}
    </details>}
  </details>;
}
