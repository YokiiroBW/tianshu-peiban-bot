import { useEffect, useRef, useState } from "react";
import { apiRead } from "../../api/client";
import { useDomainCommand, useDomainResource } from "../../api/useDomainResource";
import { useAuth } from "../../app/Auth";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { CommandFeedback } from "../../components/CommandFeedback";
import { ArtifactList } from "../../components/MediaPreview";
import { DetailCard } from "../../components/DetailCard";
import type { MediaRef } from "../../components/MediaPreview";
import { encodedFile } from "./file";
import type { ImageEngine, ImageView, ImagePreview, ImageProfile, SceneSnapshot } from "./types";
import "./multimodal.css";

const engineNames = { comfyui: "ComfyUI", nai: "NovelAI", gpt_image: "GPT Image" };
const executionNames: Record<string, string> = { pending: "等待生成", running: "正在生成", succeeded: "生成完成", failed: "生成失败", cancelled: "已取消", unknown: "结果未知", staged: "已暂存", awaiting_share: "待分享", delivered: "已投递" };
const hasImageWork = (view: ImageView) => view.jobs.some((job) => ["pending", "running"].includes(job.execution));

export default function ImagesPage({ settings = false }: { settings?: boolean }) {
  const { session } = useAuth();
  const { actor } = useRoleWorkspace();
  const view = useDomainResource<ImageView>("images/view", { actor_id: actor || null }, true, 5000, hasImageWork);
  const action = useDomainCommand(actor);
  const [engineId, setEngineId] = useState("");
  const [prompt, setPrompt] = useState("");
  const [purpose, setPurpose] = useState<"selfie" | "creation">("selfie");
  const [share, setShare] = useState(false);
  const [outfit, setOutfit] = useState("");
  const [preview, setPreview] = useState<ImagePreview | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [previewBusy, setPreviewBusy] = useState(false);
  const [fileError, setFileError] = useState("");
  const previewRequest = useRef<AbortController | null>(null);
  const profile = view.data?.profiles.find((item) => item.actor_id === actor);
  useEffect(() => {
    setPreview(null); setPreviewError(""); setPrompt(""); setOutfit("");
    previewRequest.current?.abort();
    return () => previewRequest.current?.abort();
  }, [actor, session?.csrf]);
  useEffect(() => {
    const available = view.data?.engines ?? [];
    setEngineId((current) => available.some((engine) => engine.id === current) ? current : profile?.engine_id ?? available[0]?.id ?? "");
  }, [view.data?.engines, profile?.engine_id]);
  function intent() { return { actor_id: actor, engine_id: engineId, profile_id: profile?.id ?? null, prompt, purpose, share, outfit_id: outfit || null }; }
  async function prepare() {
    if (!session?.authenticated || !actor) return;
    previewRequest.current?.abort();
    const controller = new AbortController(); previewRequest.current = controller;
    setPreviewBusy(true); setPreviewError("");
    try { const result = await apiRead<ImagePreview>("images/preview", intent(), session.csrf, controller.signal); if (!controller.signal.aborted) setPreview(result); }
    catch (cause) { if (!controller.signal.aborted) setPreviewError(cause instanceof Error ? cause.message : "预览失败"); }
    finally { if (!controller.signal.aborted) setPreviewBusy(false); }
  }
  return <div className="domain-workspace">
    <header className="domain-toolbar"><div><p className="eyebrow">{settings ? "图像引擎" : "生图与相册"}</p><h2>{settings ? "连接创作工具" : "此刻的她，与真实完成的作品"}</h2></div>
      <button className="button" disabled={view.loading} onClick={() => void view.refresh()}>读取状态</button></header>
    {view.error && <p role="alert" className="error-text">{view.error}</p>}
    {view.loading && !view.data && <p role="status">正在读取创作状态…</p>}
    {view.data && <>
      {settings ? <section className="panel"><h3>图像引擎</h3><div className="domain-grid">{view.data.engines.map((engine) => <EngineCard key={`${engine.id}:${engine.revision}`} engine={engine} refresh={view.refresh} />)}</div>
        <DetailCard title="添加图像引擎"><EngineEditor refresh={view.refresh} /></DetailCard>
      </section> : <>
        <section className="panel"><h3>拍一张，或继续创作</h3>
          {!actor ? <p>先选择一位角色。</p> : <form className="domain-form" onSubmit={(event) => { event.preventDefault(); void action.run("images/generate", intent()).then((result) => { if (result) void view.refresh(); }); }}>
            <div className="domain-grid"><label>创作方式<select value={purpose} onChange={(event) => { setPurpose(event.target.value as "selfie" | "creation"); setPreview(null); }}><option value="selfie">结合当前生活的自拍</option><option value="creation">明确主题创作</option></select></label>
              <label>图像引擎<select required value={engineId} onChange={(event) => { setEngineId(event.target.value); setPreview(null); }}>{view.data.engines.map((engine) => <option key={engine.id} value={engine.id} disabled={!engine.enabled || !engine.configured}>{engineNames[engine.engine]} · {engine.model} · {engine.configured ? engine.state : "未配置"}</option>)}</select></label>
              <label>穿搭<select value={outfit} onChange={(event) => { setOutfit(event.target.value); setPreview(null); }}><option value="">使用当前穿搭</option>{view.data.outfits.map((item) => <option key={item.id} value={item.id}>{item.description}</option>)}</select></label></div>
            <label>想看到怎样的画面<textarea value={prompt} onChange={(event) => { setPrompt(event.target.value); setPreview(null); }} placeholder="例如：今天的小屋自拍，保持当前穿搭，轻松自然。也可以留空，由当前生活场景规划。" required={purpose === "creation"} /></label>
            <label className="inline-check"><input type="checkbox" checked={share} onChange={(event) => setShare(event.target.checked)} />生成后沿当前已确定会话分享</label>
            <p className="muted">纯自主活动使用允许该用途的路线。NovelAI 的用户请求与自主创作条件由服务端按实际来源处理。</p>
            <div className="domain-actions"><button className="button" type="button" disabled={previewBusy || !engineId} onClick={() => void prepare()}>预览场景与提示</button>
              <button className="button primary" disabled={action.busy || !engineId}>生成新作品</button></div>
          </form>}
          {previewError && <p role="alert">{previewError}</p>}{preview && <Preview result={preview} />}
          <CommandFeedback {...action} />
        </section>
        <section className="panel"><h3>创作进度</h3>{!view.data.jobs.length && <p className="muted">当前没有创作任务。</p>}
          <div className="domain-grid">{view.data.jobs.map((job) => <article className="domain-record" key={job.action_id}><header><strong>{executionNames[job.execution] ?? job.execution}</strong><a className="text-link" href={`#/system/tasks?action=${encodeURIComponent(job.action_id)}`}>任务详情</a></header>
            {job.reason && <p>{job.reason}</p>}<ArtifactList artifacts={job.artifacts} />
            <div className="domain-actions">{["pending", "running"].includes(job.execution) && <button className="button" disabled={action.busy} onClick={() => void action.run("images/cancel", { action_id: job.action_id }).then(view.refresh)}>取消生成</button>}
              {job.execution === "unknown" && <button className="button" disabled={action.busy} onClick={() => void action.run("images/reconcile", { action_id: job.action_id }).then(view.refresh)}>核对原任务</button>}</div>
          </article>)}</div>
        </section>
        <section className="panel"><h3>相册</h3><p>这里只展示实际生成或收取的作品。再次查看已有照片不会重新生成。</p><ArtifactList artifacts={view.data.album} />{!view.data.album.length && <p className="muted">相册中还没有可读作品。</p>}</section>
      </>}
      {actor && <section className="panel"><h3>角色形象与引擎绑定</h3><ProfileEditor key={`${actor}:${profile?.revision ?? 0}`} actor={actor} profile={profile} view={view.data} refresh={view.refresh} /></section>}
      <DetailCard title="ComfyUI 工作流"><form className="domain-form" onSubmit={(event) => {
        event.preventDefault(); const form = event.currentTarget; const data = new FormData(form); const file = data.get("workflow"); setFileError("");
        if (file instanceof File) void file.text().then((text) => JSON.parse(text) as object).then((workflow) => action.run("images/workflows.import", { name: data.get("name"), workflow })).then((result) => { if (result) { form.reset(); void view.refresh(); } }).catch((cause) => setFileError(cause instanceof Error ? cause.message : "工作流文件无法解析"));
      }}><label>工作流名称<input name="name" required /></label><label>ComfyUI API JSON<input type="file" name="workflow" accept=".json,application/json" required /></label><button className="button" disabled={action.busy}>导入工作流</button>{fileError && <p role="alert">{fileError}</p>}</form>
        {view.data.workflows.map((workflow) => <article className="domain-record" key={workflow.id}><h4>{workflow.name}</h4><p>版本 {workflow.revision} · 输出 {workflow.outputs.length} 项</p><details><summary>参数绑定</summary><pre>{JSON.stringify(workflow.bindings, null, 2)}</pre></details></article>)}
      </DetailCard>
    </>}
  </div>;
}

function Scene({ scene }: { scene: SceneSnapshot }) {
  const labels: Record<string, string> = { occurred_at: "场景时间", captured_at: "快照时间", timezone: "时区", activity: "当前活动", location: "场景", weather: "天气", outfit: "穿搭", mood: "表情方向", source: "创作动机" };
  return <dl className="scene-summary">{Object.entries(scene).filter(([key]) => key in labels).map(([key, value]) => <div key={key}><dt>{labels[key]}</dt><dd>{value === null ? "未知" : typeof value === "object" ? Object.entries(value).map(([name, part]) => `${name}：${typeof part === "object" ? "见依据详情" : String(part)}`).join(" · ") : String(value)}</dd></div>)}</dl>;
}

function Preview({ result }: { result: ImagePreview }) {
  return <div className="domain-record"><h4>本次场景快照</h4><Scene scene={result.scene} />{result.warnings.length > 0 && <ul>{result.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}
    <DetailCard title="实际引擎提示与参数"><h5>画面提示</h5><p>{result.compiled.prompt}</p>{result.compiled.negative_prompt && <><h5>排除内容</h5><p>{result.compiled.negative_prompt}</p></>}<pre>{JSON.stringify(result.compiled.parameters, null, 2)}</pre></DetailCard></div>;
}

function EngineCard({ engine, refresh }: { engine: ImageEngine; refresh: () => Promise<void> }) {
  return <article className="domain-record"><header><strong>{engineNames[engine.engine]}</strong><span className="badge">{engine.configured ? engine.state : "尚未配置"}</span></header><p>{engine.model || "未选择模型"}</p>{engine.reason && <p>{engine.reason}</p>}
    <p className="muted">共享资源：{engine.quota_group || "尚未确认"}</p><DetailCard title="引擎设置"><EngineEditor engine={engine} refresh={refresh} /></DetailCard></article>;
}

function EngineEditor({ engine, refresh }: { engine?: ImageEngine; refresh: () => Promise<void> }) {
  const [kind, setKind] = useState(engine?.engine ?? "comfyui");
  const action = useDomainCommand();
  return <form className="domain-form" onSubmit={(event) => {
    event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
    void action.run("images/engines.save", { id: data.get("id"), engine: kind, base_url: data.get("base_url") || null, model: data.get("model"), quota_group: data.get("quota_group"), enabled: data.get("enabled") === "on", ...(data.get("api_key") ? { api_key: data.get("api_key") } : {}) }, engine?.revision ?? 0).then((result) => { const secret = form.querySelector<HTMLInputElement>('[name="api_key"]'); if (secret) secret.value = ""; if (result) void refresh(); });
  }}><label>连接名称<input name="id" required defaultValue={engine?.id ?? ""} readOnly={!!engine} /></label><label>引擎<select value={kind} onChange={(event) => setKind(event.target.value as ImageEngine["engine"])}>{Object.entries(engineNames).map(([value, name]) => <option value={value} key={value}>{name}</option>)}</select></label>
    <label>{kind === "comfyui" ? "ComfyUI 地址" : "原生 API 地址（可选）"}<input name="base_url" type="url" placeholder={kind === "comfyui" ? "http://你的主机:8188" : "使用提供者官方地址"} /></label>
    <label>模型<input name="model" defaultValue={engine?.model ?? ""} required={kind !== "comfyui"} /></label><label>API Key<input name="api_key" type="password" autoComplete="off" placeholder={engine?.configured ? "已保存；留空保留" : "需要时填写"} /></label>
    <label>共享资源组<input name="quota_group" defaultValue={engine?.quota_group ?? ""} placeholder="同一 GPU 或账户使用同一资源组" /></label><label className="inline-check"><input type="checkbox" name="enabled" defaultChecked={engine?.enabled ?? true} />启用引擎</label>
    <button className="button primary" disabled={action.busy}>保存引擎</button><CommandFeedback {...action} /></form>;
}

function ProfileEditor({ actor, profile, view, refresh }: { actor: string; profile?: ImageProfile; view: ImageView; refresh: () => Promise<void> }) {
  const action = useDomainCommand();
  const upload = useDomainCommand<{ media: MediaRef }>();
  const [references, setReferences] = useState<unknown[]>(profile?.references ?? []);
  const [fileError, setFileError] = useState("");
  return <div className="domain-form"><form className="domain-form" onSubmit={(event) => {
    event.preventDefault(); const data = new FormData(event.currentTarget);
    void action.run("images/profiles.save", { id: profile?.id ?? `image:${actor}`, actor_id: actor, engine_id: data.get("engine_id"), workflow_id: data.get("workflow_id") || null, character: data.get("character"), references, outfit_defaults: profile?.outfit_defaults ?? {} }, profile?.revision ?? 0).then((result) => { if (result) void refresh(); });
  }}><label>角色外观<textarea name="character" defaultValue={profile?.character ?? ""} placeholder="固定外观特征，保持与角色设定一致" /></label><div className="domain-grid"><label>默认引擎<select name="engine_id" defaultValue={profile?.engine_id ?? ""} required><option value="">选择图像引擎</option>{view.engines.map((engine) => <option value={engine.id} key={engine.id}>{engineNames[engine.engine]} · {engine.id}</option>)}</select></label>
      <label>ComfyUI 工作流<select name="workflow_id" defaultValue={profile?.workflow_id ?? ""}><option value="">按引擎使用默认设置</option>{view.workflows.map((workflow) => <option key={workflow.id} value={workflow.id}>{workflow.name}</option>)}</select></label></div>
    <p>当前已绑定 {references.length} 张参考图。</p><div className="domain-actions"><button className="button" type="button" disabled={!references.length} onClick={() => setReferences([])}>移除当前参考图</button><button className="button primary" disabled={action.busy}>保存形象绑定</button></div><CommandFeedback {...action} /></form>
    <form className="domain-form" onSubmit={(event) => {
      event.preventDefault(); const data = new FormData(event.currentTarget); const file = data.get("reference"); setFileError("");
      if (file instanceof File && file.size) void encodedFile(file).then((encoded) => upload.run("capabilities/upload", { ...encoded, actor_id: actor })).then((result) => { if (result?.data.media) setReferences((current) => [...current, result.data.media]); }).catch((cause) => setFileError(cause instanceof Error ? cause.message : "参考图片读取失败"));
    }}><label>添加角色参考图<input name="reference" type="file" accept="image/*" required /></label><button className="button" disabled={upload.busy}>上传参考图</button>{fileError && <p role="alert">{fileError}</p>}<CommandFeedback {...upload} /></form></div>;
}
