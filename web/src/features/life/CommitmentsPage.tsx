import { useState } from "react";
import { useDomainResource, useDomainCommand } from "../../api/useDomainResource";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { CommandFeedback } from "../../components/CommandFeedback";
import { DetailCard } from "../../components/DetailCard";
import type { Page } from "../../api/contracts";
import "../multimodal/multimodal.css";

type Commitment = { id: string; revision: number; title: string; due_at: string | null; timezone: string; state: string; reason: string | null; matter_id: string | null; can_cancel: boolean; can_reschedule: boolean };
const states: Record<string, string> = { scheduled: "已约定", waiting: "等待合适时机", ready: "等待执行", running: "正在处理", completed: "已完成", cancelled: "已取消", unknown: "结果未知" };

export default function CommitmentsPage() {
  const { actor } = useRoleWorkspace();
  const [cursor, setCursor] = useState<string | null>(null);
  const view = useDomainResource<Page<Commitment>>("life/commitments", { actor_id: actor, cursor, limit: 40 }, !!actor);
  const action = useDomainCommand(actor);
  return <div className="domain-workspace"><header className="domain-toolbar"><div><p className="eyebrow">约定与持续事项</p><h2>接下来，我们有什么约定</h2></div><button className="button" disabled={!actor || view.loading} onClick={() => void view.refresh()}>读取约定</button></header>
    {!actor && <p>先选择一位角色。</p>}{view.error && <p role="alert" className="error-text">{view.error}</p>}
    <section className="panel"><h3>新增约定</h3><form className="domain-form" onSubmit={(event) => {
      event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
      void action.run("life/commitments.save", { actor_id: actor, title: data.get("title"), due_at: data.get("due_at") ? new Date(String(data.get("due_at"))).toISOString() : null, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone }).then((result) => { if (result) { form.reset(); void view.refresh(); } });
    }}><label>记下什么约定<input name="title" required maxLength={1000} placeholder="例如：周六早上提醒我去图书馆" /></label><label>计划时间<input type="datetime-local" name="due_at" /></label><button className="button primary" disabled={!actor || action.busy}>保存约定</button></form><CommandFeedback {...action} /></section>
    {view.loading && !view.data && <p role="status">正在读取当前约定…</p>}
    {view.data && <section className="panel"><h3>当前约定</h3>{!view.data.coverage.complete && <p className="muted">本次读取有缺口：{view.data.coverage.missing.join("、")}</p>}
      {!view.data.items.length && <p className="muted">当前可读范围没有约定。</p>}<div className="domain-grid">{view.data.items.map((item) => <article className="domain-record" key={`${item.id}:${item.revision}`}><header><strong>{item.title}</strong><span className="badge">{states[item.state] ?? item.state}</span></header>
        <p>{item.due_at ? new Date(item.due_at).toLocaleString("zh-CN", { timeZone: item.timezone }) : "时间尚未确定"} · {item.timezone}</p>{item.reason && <p>{item.reason}</p>}
        {item.can_reschedule && <DetailCard title="调整时间"><form className="domain-form" onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); void action.run("life/commitments.reschedule", { actor_id: actor, id: item.id, due_at: new Date(String(data.get("due_at"))).toISOString(), timezone: item.timezone }, item.revision).then(view.refresh); }}><label>新的时间<input type="datetime-local" name="due_at" required /></label><button className="button" disabled={action.busy}>保存新时间</button></form></DetailCard>}
        {item.can_cancel && <button className="button" disabled={action.busy} onClick={() => void action.run("life/commitments.cancel", { actor_id: actor, id: item.id }, item.revision).then(view.refresh)}>取消约定</button>}
      </article>)}</div><div className="domain-actions">{cursor && <button className="button" onClick={() => setCursor(null)}>回到最新约定</button>}{view.data.next_cursor && <button className="button" onClick={() => setCursor(view.data!.next_cursor)}>继续读取</button>}</div>
    </section>}
  </div>;
}
