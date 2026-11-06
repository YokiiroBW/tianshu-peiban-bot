import { Notice } from "../../components/Notifications";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { GLOBAL_SCOPE, useDomainCommand, useDomainResource } from "../../api/useDomainResource";
import "./access.css";

type Access = { mode: "http" | "https" | "proxy"; origin: string; certificate: string | null };
type View = { available: boolean; can_manage: boolean; active: Access; saved: Access;
  revision: number; restart_required: boolean; certificates: string[]; listener_port: number; https_listener_port: number };

export function AccessPanel() {
  const resource = useDomainResource<View>("access/view", GLOBAL_SCOPE);
  const command = useDomainCommand<View>("access", "global");
  const view = resource.data;
  const [draft, setDraft] = useState<Access | null>(null);
  const [notice, setNotice] = useState("");
  useEffect(() => { if (view?.available) setDraft(view.saved); }, [view]);

  function changeMode(mode: Access["mode"]) {
    if (!draft || !view) return;
    let origin = draft.origin;
    if (mode !== "proxy") {
      try {
        const url = new URL(origin);
        const wasDefault = Number(url.port || (url.protocol === "https:" ? 443 : 80)) === (draft.mode === "https" ? view.https_listener_port : view.listener_port);
        url.protocol = mode + ":";
        if (wasDefault) url.port = String(mode === "https" ? view.https_listener_port : view.listener_port);
        origin = url.origin;
      } catch { /* The normal URL field retains the user's unfinished input. */ }
    }
    setDraft({ mode, origin, certificate: mode === "https" ? draft.certificate || view.certificates[0] || null : null });
    setNotice("");
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!view || !draft) return;
    setNotice("");
    const result = await command.run("access/save", { value: draft }, view.revision);
    if (result) {
      setNotice(result.data?.restart_required ? "访问设置已保存。重启天枢后生效。" : "访问设置已保存，与当前运行配置一致。");
      await resource.refresh();
    }
  }

  return <section className="panel access-settings" aria-labelledby="access-title">
    <div className="section-heading"><div><h2 id="access-title">访问地址与 HTTPS</h2><p>管理用户实际打开的地址、连接方式和已安装证书。</p></div>
      <button type="button" className="button secondary" onClick={() => void resource.refresh()} disabled={resource.loading || command.busy}>刷新</button></div>
    {(resource.error || command.error) && <p role="alert">{command.error || resource.error}</p>}
    {notice && <Notice>{notice}</Notice>}
    {resource.loading && !view && <p role="status">正在读取访问设置…</p>}
    {view && !view.available && <p>此部署未配置访问地址管理。</p>}
    {view?.available && draft && <>
      <dl className="access-summary"><div><dt>当前生效</dt><dd><a href={view.active.origin}>{view.active.origin}</a></dd></div>
        <div><dt>连接方式</dt><dd>{{ http: "直接 HTTP", https: "直接 HTTPS", proxy: "反向代理" }[view.active.mode]}</dd></div></dl>
      {view.restart_required && <p className="access-pending" role="status">待重启：{view.saved.origin}。请在部署管理中重启天枢。</p>}
      <form onSubmit={(event) => void save(event)}><fieldset disabled={command.busy || !view.can_manage}>
        <legend>访问设置</legend>
        <label htmlFor="access-mode">连接方式</label><select id="access-mode" value={draft.mode} onChange={(event) => changeMode(event.target.value as Access["mode"])}>
          <option value="http">直接 HTTP</option><option value="https">直接 HTTPS</option><option value="proxy">反向代理</option></select>
        <label htmlFor="access-origin">完整访问地址</label><input id="access-origin" type="url" required maxLength={300} value={draft.origin} placeholder="https://tianshu.example.com:18443" onChange={(event) => { setDraft({ ...draft, origin: event.target.value }); setNotice(""); }} />
        <p className="field-hint">只填写协议、IP 或域名及端口，不含路径。域名解析和反向代理由部署管理配置。</p>
        {draft.mode === "https" && <><label htmlFor="access-certificate">已安装证书</label><select id="access-certificate" required value={draft.certificate || ""} onChange={(event) => setDraft({ ...draft, certificate: event.target.value || null })}>
          <option value="" disabled>请选择证书</option>{view.certificates.map((id) => <option key={id} value={id}>{id}</option>)}</select>
          <p className="field-hint">保存时会核对证书有效期、私钥和访问域名。证书文件由部署管理员安装。</p></>}
        {draft.mode === "proxy" && <p className="field-hint">填写用户实际访问的外部地址；反向代理上游使用天枢的 HTTP 入口。HTTPS 证书由代理管理。</p>}
        <button type="submit" className="button" disabled={command.busy || !view.can_manage || draft.mode === "https" && !draft.certificate}>{command.busy ? "正在保存…" : "保存，重启后生效"}</button>
      </fieldset></form>
      {!view.can_manage && <p>当前账号可以查看访问设置，修改需要配置管理权限。</p>}
    </>}
  </section>;
}
