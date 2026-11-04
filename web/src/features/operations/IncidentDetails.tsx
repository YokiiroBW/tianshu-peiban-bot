import { useState } from "react";
import { GLOBAL_SCOPE, useDomainCommand, useDomainResource } from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";
import type { IncidentDetail } from "./types";

function show(value: unknown): string {
  if (value == null) return "未记录";
  return typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "未记录";
}
function Records({ title, items }: { title: string; items: unknown[] }) {
  return <details><summary>{title}（{items.length}）</summary>{items.length ? items.map((item, index) => <pre key={index}>{show(item)}</pre>) : <p>没有返回相关记录。</p>}</details>;
}

export function IncidentDetails({ id, onChanged }: { id: string; onChanged: () => Promise<void> }) {
  const view = useDomainResource<IncidentDetail>("operations/incidents/get", { ...GLOBAL_SCOPE, incident_id: id });
  const action = useDomainCommand<{ status: string }>(`operations-recover:${id}`, "global");
  const [material, setMaterial] = useState("");
  const [version, setVersion] = useState("");
  const [error, setError] = useState("");
  const incident = view.data;
  return <section className="panel operations-detail" aria-label="运维事件详情">
    <div className="section-heading"><h3>事件详情</h3><button className="button" disabled={view.loading} onClick={() => void view.refresh()}>刷新事件</button></div>
    {view.error && <p role="alert">{view.error}</p>}
    {view.loading && !incident && <p role="status">正在读取事件…</p>}
    {incident && <>
      <p><strong>{incident.summary || incident.error_family}</strong> · {incident.state} · {incident.severity}</p>
      <p>依赖：{incident.dependency} · 事件版本 {incident.revision} · 出现 {incident.occurrences} 次 · 最近 {incident.last_seen}</p>
      <p>诊断状态：{incident.diagnostic_state ?? "未记录"} · 恢复动作：{incident.recovery_action ?? "未登记"}</p>
      <details><summary>受影响范围与最近观察</summary><h4>受影响对象</h4><pre>{show(incident.affected)}</pre><h4>最近观察</h4><pre>{show(incident.last_observation)}</pre><h4>恢复链</h4><pre>{show(incident.recovery_lineage)}</pre></details>
      <Records title="诊断记录" items={incident.diagnostics ?? []} />
      <Records title="恢复与验证记录" items={incident.recoveries ?? []} />
      <Records title="通知与投递回执" items={incident.notifications ?? []} />
      {incident.recovery_action && incident.state !== "resolved" && <form className="domain-form" onSubmit={(event) => {
        event.preventDefault();
        const parsed = version.trim() ? Number(version) : null;
        if (parsed !== null && (!Number.isSafeInteger(parsed) || parsed < 0)) { setError("版本应为非负整数。"); return; }
        setError("");
        const target = { ...(material.trim() ? { material_id: material.trim() } : {}), ...(parsed !== null ? { version: parsed } : {}) };
        void action.run("operations/recover", { incident_id: incident.incident_id, action: incident.recovery_action, target }, incident.revision)
          .then(async (result) => { if (result) { await Promise.all([view.refresh(), onChanged()]); } });
      }}>
        <h4>请求已登记的恢复动作</h4>
        <p className="muted">请求受理后仍需观察实际恢复记录与业务结果。</p>
        <div className="operations-settings-grid"><label>原材料 ID（可选）<input value={material} onChange={(event) => setMaterial(event.target.value)} /></label><label>材料版本（可选）<input inputMode="numeric" value={version} onChange={(event) => setVersion(event.target.value)} /></label></div>
        <button className="button" disabled={action.busy}>请求 {incident.recovery_action}</button>
      </form>}
      {error && <p role="alert">{error}</p>}
      {action.result?.data?.status === "requested" && <p role="status">恢复请求已受理，尚未证明业务恢复。</p>}
      <CommandFeedback {...action} />
    </>}
  </section>;
}
