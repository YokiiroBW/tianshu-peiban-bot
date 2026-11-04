import { useEffect, useState } from "react";
import { GLOBAL_SCOPE, useDomainCommand, useDomainResource } from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";
import type { ModelCatalog } from "../settings/providerTypes";
import type { OperationsSettings as Settings, OperationsValue } from "./types";

export function OperationsSettings() {
  const settings = useDomainResource<Settings>("operations/settings", GLOBAL_SCOPE);
  const models = useDomainResource<ModelCatalog>("models/catalog", GLOBAL_SCOPE);
  const action = useDomainCommand("operations-settings", "global");
  const [draft, setDraft] = useState<OperationsValue | null>(null);
  const [password, setPassword] = useState("");
  const [clearPassword, setClearPassword] = useState(false);
  useEffect(() => { if (settings.data) setDraft(structuredClone(settings.data.value)); }, [settings.data]);
  const routeSelect = (name: "primary_route_id" | "backup_route_id", label: string) => {
    const selected = draft?.diagnosis[name] ?? "";
    return <label>{label}<select value={selected} onChange={(event) => draft && setDraft({ ...draft, diagnosis: { ...draft.diagnosis, [name]: event.target.value || null } })}>
      <option value="">未配置</option>
      {selected && !models.data?.routes.some((item) => item.route_id === selected) && <option value={selected}>当前路由 · {selected}</option>}
      {models.data?.routes.map((item) => <option key={item.route_id} value={item.route_id}>{item.model} · {item.route_id}{item.enabled ? "" : "（已停用）"}</option>)}
    </select></label>;
  };
  return <section className="panel operations-settings">
    <div className="section-heading"><h3>诊断与通知设置</h3><button className="button" disabled={settings.loading} onClick={() => void settings.refresh()}>读取当前设置</button></div>
    {settings.error && <p role="alert">{settings.error}</p>}
    {models.error && <p>模型目录暂不可读，已保存的路由仍会保留：{models.error}</p>}
    {settings.loading && !settings.data && <p role="status">正在读取运维设置…</p>}
    {settings.data && draft && <form className="domain-form" onSubmit={(event) => {
      event.preventDefault();
      const value = structuredClone(draft);
      if (password && value.notifications.smtp) value.notifications.smtp.credential_ref = "sealed:operations:smtp";
      void action.run("operations/settings/save", { value, ...(password ? { smtp_password: password } : {}), ...(clearPassword ? { clear_smtp_password: true } : {}) }, settings.data!.revision)
        .then(async (result) => { if (result) { setPassword(""); setClearPassword(false); await settings.refresh(); } });
    }}>
      <p>诊断模型：{settings.data.diagnosis_configured ? "已配置" : "未配置"}；主模型结果未知时不会自动重发到备用模型。</p>
      <div className="operations-settings-grid">{routeSelect("primary_route_id", "诊断模型路由")}{routeSelect("backup_route_id", "备用路由")}</div>
      <fieldset><legend>管理员 QQ 通知</legend>
        <label><input type="checkbox" checked={draft.notifications.qq !== null} onChange={(event) => setDraft({ ...draft, notifications: { ...draft.notifications, qq: event.target.checked ? { connection_id: "", recipient_id: "" } : null } })} /> 配置 QQ 通知</label>
        <p>当前状态：{settings.data.notification_configured.qq ? "已配置" : "未配置"}</p>
        {draft.notifications.qq && <div className="operations-settings-grid">
          <label>机器人连接 ID<input required value={draft.notifications.qq.connection_id} onChange={(event) => setDraft({ ...draft, notifications: { ...draft.notifications, qq: { ...draft.notifications.qq!, connection_id: event.target.value } } })} /></label>
          <label>管理员接收 ID<input required value={draft.notifications.qq.recipient_id} onChange={(event) => setDraft({ ...draft, notifications: { ...draft.notifications, qq: { ...draft.notifications.qq!, recipient_id: event.target.value } } })} /></label>
        </div>}
      </fieldset>
      <fieldset><legend>邮件通知</legend>
        <label><input type="checkbox" checked={draft.notifications.smtp !== null} onChange={(event) => {
          if (!event.target.checked) { setPassword(""); setClearPassword(false); }
          setDraft({ ...draft, notifications: { ...draft.notifications, smtp: event.target.checked ? { host: "", port: 587, sender: "", recipient: "", username: null, credential_ref: null, tls: "starttls" } : null } });
        }} /> 配置 SMTP 通知</label>
        <p>当前状态：{settings.data.notification_configured.smtp ? "已配置" : "未配置"} · 登录凭据：{settings.data.smtp_credential_configured === null ? "不需要或未配置登录" : settings.data.smtp_credential_configured ? "已保存" : "尚未保存"}</p>
        {draft.notifications.smtp && <div className="operations-settings-grid">
          <label>SMTP 主机<input required value={draft.notifications.smtp.host} onChange={(event) => setDraft({ ...draft, notifications: { ...draft.notifications, smtp: { ...draft.notifications.smtp!, host: event.target.value } } })} /></label>
          <label>端口<input type="number" required min={1} max={65535} value={draft.notifications.smtp.port} onChange={(event) => setDraft({ ...draft, notifications: { ...draft.notifications, smtp: { ...draft.notifications.smtp!, port: Number(event.target.value) } } })} /></label>
          <label>发件人<input type="email" required value={draft.notifications.smtp.sender} onChange={(event) => setDraft({ ...draft, notifications: { ...draft.notifications, smtp: { ...draft.notifications.smtp!, sender: event.target.value } } })} /></label>
          <label>接收邮箱<input type="email" required value={draft.notifications.smtp.recipient} onChange={(event) => setDraft({ ...draft, notifications: { ...draft.notifications, smtp: { ...draft.notifications.smtp!, recipient: event.target.value } } })} /></label>
          <label>登录用户（可留空）<input value={draft.notifications.smtp.username ?? ""} onChange={(event) => setDraft({ ...draft, notifications: { ...draft.notifications, smtp: { ...draft.notifications.smtp!, username: event.target.value || null } } })} /></label>
          <label>连接加密<select value={draft.notifications.smtp.tls} onChange={(event) => setDraft({ ...draft, notifications: { ...draft.notifications, smtp: { ...draft.notifications.smtp!, tls: event.target.value as "tls" | "starttls" } } })}><option value="starttls">STARTTLS</option><option value="tls">TLS</option></select></label>
          <label>更换 SMTP 密码（留空保留）<input type="password" autoComplete="new-password" value={password} onChange={(event) => { setPassword(event.target.value); if (event.target.value) setClearPassword(false); }} /></label>
          <label><input type="checkbox" checked={clearPassword} onChange={(event) => { setClearPassword(event.target.checked); if (event.target.checked) setPassword(""); }} /> 清除已保存密码</label>
        </div>}
      </fieldset>
      <button className="button primary" disabled={action.busy}>保存运维设置</button><CommandFeedback {...action} />
    </form>}
  </section>;
}
