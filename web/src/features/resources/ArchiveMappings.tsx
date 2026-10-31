import { useState } from "react";
import { GLOBAL_SCOPE, actorScope, useDomainResource } from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";

export type ArchiveMapping = { mapping_id: string; conversation_id: string; connection_id: string; channel: { namespace: "qq"; binding_id: string; channel_conversation_id: string; thread_id: null } };
type Connection = { connection_id: string; actor_id: string; namespace: string; self_external_id: string; enabled: boolean };
type Link = { connection_id: string; conversation_id: string; external_id: string };
type Directory = { connections: Connection[]; conversation_links: Link[] };
type Conversation = { conversation_id: string; label: string; adapter: string; kind: string };
const blank = (): ArchiveMapping => ({ mapping_id: "", conversation_id: "", connection_id: "", channel: { namespace: "qq", binding_id: "", channel_conversation_id: "", thread_id: null } });

function MappingRow({ value, directory, change, remove }: { value: ArchiveMapping; directory: Directory | null; change: (next: ArchiveMapping) => void; remove: () => void }) {
  const connection = directory?.connections.find((row) => row.connection_id === value.connection_id);
  const conversations = useDomainResource<Page<Conversation>>("identity/conversations", { ...actorScope(connection?.actor_id ?? null), limit: 100 }, !!connection);
  const links = directory?.conversation_links.filter((row) => row.connection_id === value.connection_id) ?? [];
  return <fieldset className="asset-settings-form"><legend>会话映射</legend>
    <label>映射名称<input required value={value.mapping_id} onChange={(event) => change({ ...value, mapping_id: event.target.value })} /></label>
    <label>已登记渠道{directory ? <select required value={value.connection_id} onChange={(event) => change({ ...value, connection_id: event.target.value, conversation_id: "" })}>
      <option value="">请选择渠道账号</option>
      {value.connection_id && !connection && <option value={value.connection_id}>原渠道不可读取：{value.connection_id}</option>}
      {directory.connections.filter((row) => row.namespace === "qq").map((row) => <option key={row.connection_id} value={row.connection_id}>{row.connection_id} · {row.self_external_id}{row.enabled ? "" : "（已停用）"}</option>)}
    </select> : <input required placeholder="已登记的渠道连接标识" value={value.connection_id} onChange={(event) => change({ ...value, connection_id: event.target.value })} />}</label>
    <label>天枢会话{directory ? <select required value={value.conversation_id} onChange={(event) => change({ ...value, conversation_id: event.target.value })}>
      <option value="">请选择该渠道已绑定的会话</option>
      {value.conversation_id && !links.some((row) => row.conversation_id === value.conversation_id) && <option value={value.conversation_id}>原会话不可读取：{value.conversation_id}</option>}
      {links.map((row) => <option key={row.conversation_id} value={row.conversation_id}>{conversations.data?.items.find((item) => item.conversation_id === row.conversation_id)?.label ?? row.conversation_id} · {row.external_id}</option>)}
    </select> : <input required placeholder="天枢会话标识" value={value.conversation_id} onChange={(event) => change({ ...value, conversation_id: event.target.value })} />}</label>
    {conversations.error && <p role="status">会话名称未读到：{conversations.error}</p>}
    <label>Chat Audit 账号绑定标识<input required value={value.channel.binding_id} onChange={(event) => change({ ...value, channel: { ...value.channel, binding_id: event.target.value } })} /></label>
    <label>Chat Audit 会话标识<input required placeholder="档案服务中登记的会话 ID" value={value.channel.channel_conversation_id} onChange={(event) => change({ ...value, channel: { ...value.channel, channel_conversation_id: event.target.value } })} /></label>
    <details><summary>协议字段</summary><p>档案命名空间：QQ；thread_id：null。选择渠道和会话只设置映射，不创建身份或扩大读取授权。</p></details>
    <button type="button" className="button" onClick={remove}>移除此映射</button>
  </fieldset>;
}

export function ArchiveMappings({ value, onChange }: { value: ArchiveMapping[]; onChange: (next: ArchiveMapping[]) => void }) {
  const directory = useDomainResource<Directory>("identity/directory", GLOBAL_SCOPE);
  const [json, setJson] = useState("");
  const [error, setError] = useState("");
  return <section>
    <h4>会话映射</h4>
    {directory.error && <p role="status">无法列出身份目录，可填写已有渠道与会话标识；实际读取仍会核验：{directory.error}</p>}
    {value.map((row, index) => <MappingRow key={index} value={row} directory={directory.data} change={(next) => onChange(value.map((item, at) => at === index ? next : item))} remove={() => onChange(value.filter((_, at) => at !== index))} />)}
    <button type="button" className="button" onClick={() => onChange([...value, blank()])}>添加会话映射</button>
    <details><summary>批量导入或导出 JSON（可选）</summary>
      <textarea rows={7} value={json} onChange={(event) => setJson(event.target.value)} />
      <button type="button" className="button" onClick={() => setJson(JSON.stringify(value, null, 2))}>导出当前映射</button>
      <button type="button" className="button" onClick={() => {
        try {
          const rows = JSON.parse(json);
          if (!Array.isArray(rows) || rows.some((row) => !row || typeof row.mapping_id !== "string" || typeof row.conversation_id !== "string" || typeof row.connection_id !== "string" || row.channel?.namespace !== "qq" || typeof row.channel.binding_id !== "string" || typeof row.channel.channel_conversation_id !== "string" || row.channel.thread_id !== null)) throw new Error("格式不符");
          onChange(rows); setError("");
        } catch { setError("请输入有效的会话映射数组。"); }
      }}>载入到表单</button>
      {error && <p role="alert">{error}</p>}
    </details>
  </section>;
}
