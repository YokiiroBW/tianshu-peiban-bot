import { useEffect, useRef, useState } from "react";
import { apiRead } from "../../api/client";
import { useDomainCommand, useDomainResource } from "../../api/useDomainResource";
import { useAuth } from "../../app/Auth";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { CommandFeedback } from "../../components/CommandFeedback";
import { ArtifactList, MediaPreview } from "../../components/MediaPreview";
import { DetailCard } from "../../components/DetailCard";
import type { ArtifactRef, Page } from "../../api/contracts";
import { encodedFile } from "./file";
import type { ReactionsView, ReactionAsset } from "./types";
import "./multimodal.css";

export default function ReactionsPage() {
  const { session } = useAuth();
  const workspace = useRoleWorkspace();
  const [pack, setPack] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const view = useDomainResource<ReactionsView>("reactions/view", { pack_id: pack || null, cursor, limit: 48 });
  const [assets, setAssets] = useState<ReactionAsset[]>([]);
  const [search, setSearch] = useState<Page<ReactionAsset> | null>(null);
  const [query, setQuery] = useState("");
  const [searchError, setSearchError] = useState("");
  const [searching, setSearching] = useState(false);
  const [fileError, setFileError] = useState("");
  const activeSearch = useRef<AbortController | null>(null);
  const action = useDomainCommand<{ artifacts?: ArtifactRef[] }>();
  useEffect(() => { setAssets([]); setSearch(null); setCursor(null); activeSearch.current?.abort(); }, [pack, session?.csrf]);
  useEffect(() => {
    if (!view.data) return;
    setAssets((current) => cursor ? [...current, ...view.data!.assets.filter((asset) => !current.some((item) => item.id === asset.id))] : view.data!.assets);
  }, [view.data]);
  useEffect(() => () => activeSearch.current?.abort(), []);
  async function searchAssets() {
    if (!session?.authenticated || !query.trim()) return;
    activeSearch.current?.abort(); const controller = new AbortController(); activeSearch.current = controller;
    setSearching(true); setSearchError("");
    try {
      const result = await apiRead<Page<ReactionAsset>>("reactions/search", { query, pack_ids: pack ? [pack] : [], actor_id: workspace.actor || null, limit: 24 }, session.csrf, controller.signal);
      if (!controller.signal.aborted) setSearch(result);
    } catch (cause) { if (!controller.signal.aborted) setSearchError(cause instanceof Error ? cause.message : "检索失败"); }
    finally { if (!controller.signal.aborted) setSearching(false); }
  }
  function refreshed() { setCursor(null); return view.refresh(); }
  return <div className="domain-workspace">
    <header className="domain-toolbar"><div><p className="eyebrow">表情与图包</p><h2>按交流用途找到合适的表情</h2></div><button className="button" disabled={view.loading} onClick={() => void refreshed()}>读取图库</button></header>
    <p>角色根据回应意图选图。普通照片和聊天截图不会因此自动成为共享表情。</p>
    {view.error && <p role="alert" className="error-text">{view.error}</p>}
    {view.loading && !view.data && <p role="status">正在读取图包…</p>}
    {view.data && <>
      <section className="panel"><div className="domain-grid"><label className="domain-form">素材包<select value={pack} onChange={(event) => setPack(event.target.value)}><option value="">全部可用图包</option>{view.data.packs.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.asset_count ?? "未知"} 张</option>)}</select></label>
        <div><p>向量配置：{view.data.index.profile ?? "尚未配置"}</p><p>等待索引 {view.data.index.pending ?? "未知"} · 失败 {view.data.index.failed ?? "未知"}</p></div></div>
        <div className="domain-actions"><button className="button" disabled={action.busy} onClick={() => void action.run("reactions/annotate", { pack_id: pack || null }).then(refreshed)}>标注未处理素材</button>
          <button className="button" disabled={action.busy} onClick={() => void action.run("reactions/index", { pack_id: pack || null }).then(refreshed)}>增量建立索引</button>
          <button className="button" disabled={action.busy || !pack} onClick={() => void action.run("reactions/packs.export", { pack_id: pack })}>导出图包</button></div>
        <CommandFeedback {...action} />{action.result?.data?.artifacts && <ArtifactList artifacts={action.result.data.artifacts} />}
      </section>
      <DetailCard title="导入图片、动图或图包"><form className="domain-form" onSubmit={(event) => {
        event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
        const files = data.getAll("files").filter((file): file is File => file instanceof File && file.size > 0);
        setFileError("");
        void Promise.all(files.map(encodedFile)).then((encoded) => action.run("reactions/packs.import", {
          name: data.get("name"), actor_ids: data.get("role_only") === "on" && workspace.actor ? [workspace.actor] : [],
          scope_ids: String(data.get("scopes") ?? "").split(/[,，\n]/).map((value) => value.trim()).filter(Boolean),
          files: encoded.filter((file) => !file.filename.toLowerCase().endsWith(".zip")),
          ...(encoded.find((file) => file.filename.toLowerCase().endsWith(".zip")) ? { archive_base64: encoded.find((file) => file.filename.toLowerCase().endsWith(".zip"))!.data_base64 } : {}),
        })).then((result) => { if (result) { form.reset(); void refreshed(); } }).catch((cause) => setFileError(cause instanceof Error ? cause.message : "文件读取失败"));
      }}><label>图包名称<input name="name" required /></label><label>图片、GIF 或 ZIP 图包<input name="files" type="file" accept="image/*,.zip" multiple required /></label>
        <label className="inline-check"><input name="role_only" type="checkbox" disabled={!workspace.actor} />仅供当前角色使用</label>
        <label>会话范围（可选）<textarea name="scopes" placeholder="每行一个已授权会话 ID；留空使用图包的默认范围" /></label>
        <button className="button primary" disabled={action.busy}>导入并去重</button>{fileError && <p role="alert">{fileError}</p>}</form></DetailCard>
      <section className="panel"><h3>语义检索试用</h3><form className="domain-form" onSubmit={(event) => { event.preventDefault(); void searchAssets(); }}><label>这次回应想表达什么<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="例如：轻轻安慰、表示期待、俏皮地道晚安" required /></label>
        <div className="domain-actions"><button className="button" disabled={searching}>检索素材</button>{search && <button className="button" type="button" onClick={() => setSearch(null)}>返回图库</button>}</div></form>
        {searchError && <p role="alert">{searchError}</p>}{search?.coverage && !search.coverage.complete && <p className="muted">本次检索范围有缺口：{search.coverage.missing.join("、") || "尚未完成全部索引"}</p>}
      </section>
      <section className="panel"><h3>{search ? "检索结果" : "素材预览与纠错"}</h3>
        <div className="domain-grid">{(search?.items ?? assets).map((asset) => <ReactionCard key={`${asset.id}:${asset.revision}`} asset={asset} refresh={refreshed} />)}</div>
        {!(search?.items ?? assets).length && !view.loading && <p className="muted">当前可用范围内没有素材。</p>}
        {!search && view.data.next_cursor && <button className="button" disabled={view.loading} onClick={() => setCursor(view.data!.next_cursor)}>继续加载素材</button>}
      </section>
    </>}
  </div>;
}

function ReactionCard({ asset, refresh }: { asset: ReactionAsset; refresh: () => Promise<void> }) {
  const action = useDomainCommand();
  return <article className="domain-record reaction-card"><MediaPreview media={asset.media} alt={asset.description || "表情素材"} />
    <p>{asset.description || "尚未标注"}</p><small>{asset.animated ? "动图" : "静态图"} · {asset.index_state}</small>
    <DetailCard title="描述、用途与启用设置"><form className="domain-form" onSubmit={(event) => {
      event.preventDefault(); const data = new FormData(event.currentTarget);
      void action.run("reactions/assets.update", {
        id: asset.id, description: data.get("description"), ocr: data.get("ocr"),
        actions: String(data.get("actions")).split(/[,，]/).map((word) => word.trim()).filter(Boolean),
        purposes: String(data.get("purposes")).split(/[,，]/).map((word) => word.trim()).filter(Boolean),
        enabled: data.get("enabled") === "on",
      }, asset.revision).then((result) => { if (result) void refresh(); });
    }}><label>画面描述<textarea name="description" defaultValue={asset.description} /></label><label>图中文字<input name="ocr" defaultValue={asset.ocr} /></label>
      <label>动作标签<input name="actions" defaultValue={asset.actions.join("，")} /></label><label>交流用途<input name="purposes" defaultValue={asset.purposes.join("，")} /></label>
      <label className="inline-check"><input type="checkbox" name="enabled" defaultChecked />允许选用</label><button className="button" disabled={action.busy}>保存纠正</button></form>
      <button className="button" disabled={action.busy} onClick={() => void action.run("reactions/assets.delete", { id: asset.id }, asset.revision).then(refresh)}>删除素材</button><CommandFeedback {...action} />
    </DetailCard></article>;
}
