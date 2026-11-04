import { useState } from "react";
import { GLOBAL_SCOPE, useDomainResource } from "../../api/useDomainResource";
import { useAuth } from "../../app/Auth";
import { IncidentDetails } from "./IncidentDetails";
import { OperationsSettings } from "./OperationsSettings";
import type { IncidentPage } from "./types";
import "./operations.css";

export default function OperationsPage() {
  const { session } = useAuth();
  const allowed = session?.principal?.permissions.some((permission) => permission === "*" || permission === "config.publish") === true;
  const [state, setState] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState(() => new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("incident_id") ?? "");
  const view = useDomainResource<IncidentPage>("operations/incidents", { ...GLOBAL_SCOPE, state: state || null, cursor, limit: 30 }, allowed);
  if (!allowed) return <section className="panel"><h2>运维事件</h2><p>当前账号没有配置管理权限。</p></section>;
  return <div className="operations-page">
    <section className="panel"><div className="section-heading"><div><h2>运维事件</h2><p>事件、恢复动作与实际业务观察分别记录。</p></div><button className="button" disabled={view.loading} onClick={() => void view.refresh()}>刷新事件</button></div>
      <label>事件状态 <select value={state} onChange={(event) => { setState(event.target.value); setCursor(null); }}><option value="">全部</option><option value="open">待处理</option><option value="observing">观察中</option><option value="escalated">已升级</option><option value="resolved">已恢复</option></select></label>
      {view.error && <p role="alert">{view.error}</p>}
      {view.loading && !view.data && <p role="status">正在读取运维事件…</p>}
      {view.data && <>
        {!view.data.items.length && <p>当前范围没有运维事件。</p>}
        <div className="operations-incidents">{view.data.items.map((item) => <button type="button" key={item.incident_id} className="operations-incident" aria-pressed={selected === item.incident_id} onClick={() => setSelected(item.incident_id)}>
          <strong>{item.summary || item.error_family}</strong><span>{item.state} · {item.severity} · {item.dependency}</span><small>最近 {item.last_seen} · {item.occurrences} 次</small>
        </button>)}</div>
        {!view.data.coverage.complete && <p>事件列表有未返回的部分。</p>}
        <div className="domain-actions">{cursor && <button className="button" onClick={() => setCursor(null)}>回到首批事件</button>}{view.data.next_cursor && <button className="button" onClick={() => setCursor(view.data!.next_cursor)}>下一批事件</button>}</div>
      </>}
    </section>
    {selected && <IncidentDetails key={selected} id={selected} onChanged={view.refresh} />}
    <OperationsSettings />
  </div>;
}
