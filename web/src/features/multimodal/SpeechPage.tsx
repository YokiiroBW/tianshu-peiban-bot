import { useState, type FormEvent } from "react";
import { useDomainResource, useDomainCommand } from "../../api/useDomainResource";
import { apiRead } from "../../api/client";
import { useAuth } from "../../app/Auth";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { CommandFeedback } from "../../components/CommandFeedback";
import { ArtifactList, MediaPreview, type MediaRef } from "../../components/MediaPreview";
import { DetailCard } from "../../components/DetailCard";
import { encodedFile } from "./file";
import type { FishConnection, SpeechView, Voice, VoiceProfile, SpeechArtifact, PronunciationDictionary } from "./types";
import "./multimodal.css";

const capabilityNames: Record<string, string> = {
  tts: "语音合成", asr: "文件转写", stream: "增量语音", timestamps: "时间对齐",
  voices: "音色查询", clone: "声音创建", dictionaries: "发音词典",
};
const billingNames = { plus: "Plus 网页额度", api: "开发者 API 余额", free: "免费模型路线" };

export default function SpeechPage() {
  const view = useDomainResource<SpeechView>("speech/view");
  const action = useDomainCommand();
  const upload = useDomainCommand<{ media: MediaRef }>();
  const [transcriptionError, setTranscriptionError] = useState("");
  const workspace = useRoleWorkspace();
  return <div className="domain-workspace">
    <header className="domain-toolbar"><div><p className="eyebrow">声音与语音</p><h2>保持她的声音，自然表达</h2></div>
      <button className="button" disabled={view.loading} onClick={() => void view.refresh()}>读取状态</button></header>
    {view.error && <p role="alert" className="error-text">{view.error}</p>}
    {view.loading && !view.data && <p role="status">正在读取语音连接…</p>}
    {view.data && <>
      <section className="panel"><h3>Fish Audio 连接</h3>
        <p>Plus MCP 使用网页账户额度；原生 API 单独计费。路线不可用时保留原因，由你选择下一条路线。</p>
        <div className="domain-grid">{view.data.connections.map((connection) => <ConnectionCard key={connection.id} connection={connection} refresh={view.refresh} />)}</div>
        {!view.data.connections.length && <p className="muted">还没有配置语音连接。</p>}
        <DetailCard title="添加语音连接"><ConnectionEditor refresh={view.refresh} /></DetailCard>
      </section>
      <section className="panel"><h3>角色声线与输出方式</h3>
        <p>固定音色与本次语气分开保存。文字、语音、双模式或自然选择都沿同一表达链发送。</p>
        {!workspace.actor ? <p>先选择一位角色。</p> : <VoiceProfileEditor key={`${workspace.actor}:${view.data.profiles.find((profile) => profile.actor_id === workspace.actor)?.revision ?? 0}`} actor={workspace.actor} profile={view.data.profiles.find((profile) => profile.actor_id === workspace.actor)} view={view.data} refresh={view.refresh} />}
      </section>
      <VoiceBrowser connections={view.data.connections} />
      <section className="panel"><h3>中文读音词典</h3><p>为人名、多音字和项目名保存明确读音，并通过试听核对。</p>
        {view.data.dictionaries.map((dictionary) => <DetailCard key={`${dictionary.id}:${dictionary.revision}`} title={dictionary.name}><DictionaryEditor dictionary={dictionary} refresh={view.refresh} /></DetailCard>)}
        <DetailCard title="新建发音词典"><DictionaryEditor refresh={view.refresh} /></DetailCard>
      </section>
      <section className="panel"><h3>文件转写</h3><form className="domain-form" onSubmit={(event) => {
        event.preventDefault(); const form = event.currentTarget; const data = new FormData(form); const file = data.get("audio");
        if (!(file instanceof File) || !file.size) return;
        setTranscriptionError("");
        void encodedFile(file).then((audio) => upload.run("capabilities/upload", audio)).then((result) => result?.data.media ? action.run("speech/transcribe", { connection_id: data.get("connection"), media: result.data.media, source_event_id: null }) : null).catch((cause) => setTranscriptionError(cause instanceof Error ? cause.message : "音频读取失败"));
      }}><label>语音连接<select name="connection" required>{view.data.connections.filter((connection) => connection.capabilities.asr).map((connection) => <option key={connection.id} value={connection.id}>{connection.id} · {billingNames[connection.billing_source]}</option>)}</select></label>
        <label>音频原件<input name="audio" type="file" accept="audio/*" required /></label><button className="button" disabled={action.busy || upload.busy}>转写文件</button></form>
        {transcriptionError && <p role="alert">{transcriptionError}</p>}<CommandFeedback {...upload} />
        <CommandFeedback {...action} />{action.result && <Transcription result={action.result.data} />}
      </section>
    </>}
  </div>;
}

function Transcription({ result }: { result: unknown }) {
  const text = typeof result === "object" && result !== null && "text" in result ? result.text : null;
  return typeof text === "string" ? <blockquote>{text}</blockquote> : null;
}

function ConnectionCard({ connection, refresh }: { connection: FishConnection; refresh: () => Promise<void> }) {
  const action = useDomainCommand<{ authorization_url?: string }>();
  return <article className="domain-record"><header><strong>{connection.id}</strong><span className="badge">{connection.state}</span></header>
    <dl><div><dt>接入方式</dt><dd>{connection.transport === "mcp" ? "官方 MCP" : "原生 API"}</dd></div>
      <div><dt>计费来源</dt><dd>{billingNames[connection.billing_source]}</dd></div>
      <div><dt>可用额度</dt><dd>{connection.balance ? `${connection.balance.value} ${connection.balance.unit}` : "未知"}</dd></div>
      <div><dt>账户/团队</dt><dd>{[connection.account_id, connection.team_id, connection.workspace_id].filter(Boolean).join(" / ") || "尚未取得"}</dd></div>
      <div><dt>可用功能</dt><dd>{Object.entries(connection.capabilities).filter(([, supported]) => supported).map(([key]) => capabilityNames[key] ?? key).join("、") || "尚未确认"}</dd></div></dl>
    {connection.reason && <p>{connection.reason}</p>}
    <div className="domain-actions">{connection.transport === "mcp" && <button className="button" disabled={action.busy} onClick={() => void action.run("speech/connect", { connection_id: connection.id }, connection.revision)}>连接 Plus 账户</button>}
      <button className="button" disabled={action.busy} onClick={() => void action.run("speech/refresh", { connection_id: connection.id }, connection.revision).then(refresh)}>刷新能力与余额</button>
      <button className="button" disabled={action.busy} onClick={() => void action.run("speech/disconnect", { connection_id: connection.id }, connection.revision).then(refresh)}>断开连接</button></div>
    <CommandFeedback {...action} />{action.result?.data?.authorization_url && <a className="button primary" href={action.result.data.authorization_url} target="_blank" rel="noreferrer">打开官方账户授权</a>}
    <DetailCard title="连接设置"><ConnectionEditor connection={connection} refresh={refresh} /></DetailCard>
  </article>;
}

function ConnectionEditor({ connection, refresh }: { connection?: FishConnection; refresh: () => Promise<void> }) {
  const action = useDomainCommand();
  const [transport, setTransport] = useState(connection?.transport ?? "mcp");
  return <form className="domain-form" onSubmit={(event) => {
    event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
    void action.run("speech/connections.save", {
      id: data.get("id"), transport, enabled: data.get("enabled") === "on",
      ...(data.get("api_key") ? { api_key: data.get("api_key") } : {}),
      team_id: data.get("team_id") || null, workspace_id: data.get("workspace_id") || null,
    }, connection?.revision ?? null).then((answer) => { const secret = form.querySelector<HTMLInputElement>('[name="api_key"]'); if (secret) secret.value = ""; if (answer) void refresh(); });
  }}><label>连接名称<input name="id" required defaultValue={connection?.id ?? ""} readOnly={!!connection} /></label>
    <label>接入方式<select value={transport} onChange={(event) => setTransport(event.target.value as "mcp" | "native")}><option value="mcp">Fish Plus 官方 MCP</option><option value="native">Fish 原生 API</option></select></label>
    {transport === "native" ? <label>API Key<input type="password" name="api_key" autoComplete="off" placeholder={connection?.configured ? "已保存；留空保留" : "输入 API Key"} /></label> : <p className="muted">保存后连接官方账户。MCP 生成的供应商链接可被持链者访问；天枢会将产物收取到受控媒体库，但无法撤销供应商链接。</p>}
    <label>团队<input name="team_id" defaultValue={connection?.team_id ?? ""} /></label><label>工作区<input name="workspace_id" defaultValue={connection?.workspace_id ?? ""} /></label>
    <label className="inline-check"><input type="checkbox" name="enabled" defaultChecked={connection?.enabled ?? true} />启用此连接</label>
    <button className="button primary" disabled={action.busy}>保存连接</button><CommandFeedback {...action} />
  </form>;
}

function VoiceProfileEditor({ actor, profile, view, refresh }: { actor: string; profile?: VoiceProfile; view: SpeechView; refresh: () => Promise<void> }) {
  const action = useDomainCommand<SpeechArtifact>(actor);
  const [connection, setConnection] = useState(profile?.connection_id ?? view.connections[0]?.id ?? "");
  const [voice, setVoice] = useState(profile?.voice_id ?? "");
  const [model, setModel] = useState(profile?.model ?? "s2.1-pro");
  const [sample, setSample] = useState("你好，今天过得怎么样？");
  const [mode, setMode] = useState(profile?.output_mode ?? "natural");
  return <div className="domain-form"><form className="domain-form" onSubmit={(event) => {
    event.preventDefault(); const data = new FormData(event.currentTarget);
    void action.run("speech/profiles.save", {
      id: profile?.id ?? `voice:${actor}`, actor_id: actor, connection_id: connection, voice_id: voice, model,
      language: data.get("language"), output_mode: mode, dictionary_ids: data.getAll("dictionary"),
      vocal_defaults: { pace: Number(data.get("pace")), direction: data.get("direction") },
    }, profile?.revision ?? null).then((result) => { if (result) void refresh(); });
  }}><div className="domain-grid"><label>合成连接<select required value={connection} onChange={(event) => setConnection(event.target.value)}>{view.connections.map((item) => <option key={item.id} value={item.id}>{item.id} · {billingNames[item.billing_source]}</option>)}</select></label>
      <label>稳定音色 ID<input value={voice} onChange={(event) => setVoice(event.target.value)} required /></label>
      <label>模型<input list="fish-models" value={model} onChange={(event) => setModel(event.target.value)} required /><datalist id="fish-models"><option value="s2.1-pro" /><option value="s2.1-pro-free" /><option value="drama-3" /></datalist></label>
      <label>输出方式<select value={mode} onChange={(event) => setMode(event.target.value as VoiceProfile["output_mode"])}><option value="natural">自然选择</option><option value="text">文字</option><option value="voice">语音</option><option value="both">文字与语音</option></select></label>
      <label>语言<input name="language" defaultValue={profile?.language ?? "zh-CN"} /></label>
      <label>默认语速<input name="pace" type="number" min="0.5" max="2" step="0.05" defaultValue={Number(profile?.vocal_defaults.pace ?? 1)} /></label></div>
    <label>发声风格<input name="direction" defaultValue={String(profile?.vocal_defaults.direction ?? "")} placeholder="例如：清晰、轻松，避免过度夸张" /></label>
    <fieldset><legend>使用的读音词典</legend>{view.dictionaries.map((item) => <label className="inline-check" key={item.id}><input type="checkbox" name="dictionary" value={item.id} defaultChecked={profile?.dictionary_ids.includes(item.id)} />{item.name}</label>)}</fieldset>
    <button className="button primary" disabled={action.busy}>保存角色声线</button></form>
    <label>试听台词<textarea value={sample} onChange={(event) => setSample(event.target.value)} /></label>
    <button className="button" disabled={action.busy || !voice || !connection || !sample.trim()} onClick={() => void action.run("speech/preview", { profile_id: profile?.id ?? null, actor_id: actor, connection_id: connection, voice_id: voice, model, text: sample })}>按当前路线试听</button>
    <CommandFeedback {...action} />{action.result?.data?.artifacts && <><ArtifactList artifacts={action.result.data.artifacts} /><p className="muted">实际模型 {action.result.data.model} · {action.result.data.billing_source} · {action.result.data.usage === null ? "费用未知" : "已返回实际用量"}</p></>}
  </div>;
}

function VoiceBrowser({ connections }: { connections: FishConnection[] }) {
  const { session } = useAuth();
  const [voices, setVoices] = useState<Voice[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const clone = useDomainCommand();
  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!session?.authenticated) return;
    const data = new FormData(event.currentTarget); setBusy(true); setError("");
    try { const result = await apiRead<{ items: Voice[] }>("speech/voices.search", { query: data.get("query"), connection_id: data.get("connection_id") }, session.csrf, new AbortController().signal); setVoices(result.items); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "音色查询失败"); }
    finally { setBusy(false); }
  }
  return <section className="panel"><h3>查找与试听音色</h3><form className="domain-form" onSubmit={(event) => void search(event)}>
    <label>音色连接<select name="connection_id">{connections.filter((item) => item.capabilities.voices).map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}</select></label>
    <label>搜索音色<input name="query" placeholder="音色名称或描述" /></label><button className="button" disabled={busy}>搜索</button></form>
    {error && <p role="alert">{error}</p>}<div className="domain-grid">{voices.map((voice) => <article key={voice.id} className="domain-record"><h4>{voice.name}</h4><p>{voice.id} · {voice.visibility}</p>{voice.preview && <MediaPreview media={voice.preview} alt={`${voice.name}试听`} />}</article>)}</div>
    <DetailCard title="创建自己的音色"><form className="domain-form" onSubmit={(event) => {
      event.preventDefault(); const data = new FormData(event.currentTarget); const file = data.get("audio");
      if (file instanceof File && file.size) void encodedFile(file).then((audio) => clone.run("speech/voices.clone", { connection_id: data.get("connection_id"), name: data.get("name"), visibility: "private", audio }));
    }}><label>连接<select name="connection_id">{connections.filter((item) => item.capabilities.clone).map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}</select></label>
      <label>音色名称<input name="name" required /></label><label>你的参考录音<input name="audio" type="file" accept="audio/*" required /></label><p className="muted">创建为私有音色，使用你有权提供的录音。</p>
      <button className="button" disabled={clone.busy}>创建私有音色</button><CommandFeedback {...clone} /></form></DetailCard>
  </section>;
}

function DictionaryEditor({ dictionary, refresh }: { dictionary?: PronunciationDictionary; refresh: () => Promise<void> }) {
  const action = useDomainCommand();
  return <form className="domain-form" onSubmit={(event) => {
    event.preventDefault(); const data = new FormData(event.currentTarget);
    const entries = String(data.get("entries")).split("\n").filter((line) => line.trim()).map((line) => { const separator = line.indexOf("="); return { text: line.slice(0, separator).trim(), pronunciation: line.slice(separator + 1).trim() }; });
    if (entries.some((entry) => !entry.text || !entry.pronunciation)) return;
    void action.run("speech/dictionaries.save", { id: dictionary?.id ?? null, name: data.get("name"), entries }, dictionary?.revision ?? null).then((result) => { if (result) void refresh(); });
  }}><label>词典名称<input name="name" defaultValue={dictionary?.name ?? ""} required /></label>
    <label>读音条目<textarea name="entries" required defaultValue={dictionary?.entries.map((entry) => `${entry.text}=${entry.pronunciation}`).join("\n") ?? ""} placeholder={"每行一个词，例如：\n重庆=chóng qìng"} /></label>
    <button className="button" disabled={action.busy}>保存词典</button><CommandFeedback {...action} /></form>;
}
