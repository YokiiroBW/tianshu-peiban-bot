import { useDomainResource, useDomainCommand } from "../../api/useDomainResource";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { CommandFeedback } from "../../components/CommandFeedback";
import { DetailCard } from "../../components/DetailCard";
import "../multimodal/multimodal.css";

type GrowthItem = {
  id: string; revision: number; kind: "reflection" | "interest" | "style";
  title: string; content: string; state: string; occurred_at: string | null;
  reason: string | null; evidence: { id: string; summary: string; available: boolean }[];
};
type GrowthView = { items: GrowthItem[]; next_cursor: string | null; missing: string[] };

export default function GrowthPage() {
  const { actor } = useRoleWorkspace();
  const view = useDomainResource<GrowthView>("companion/growth/view", { actor_id: actor }, !!actor);
  const action = useDomainCommand(actor);
  return <div className="domain-workspace"><header className="domain-toolbar"><div><p className="eyebrow">经历、手记与成长</p><h2>这次变化来自哪里</h2></div><button className="button" disabled={!actor || view.loading} onClick={() => void view.refresh()}>读取变化</button></header>
    {!actor && <p>先选择一位角色。</p>}{view.error && <p role="alert" className="error-text">{view.error}</p>}{view.loading && !view.data && <p role="status">正在读取角色的手记与变化…</p>}
    {view.data && <section className="panel"><p>手记表达角色当时的理解，事实与推断保留各自依据。整理后的兴趣和表达习惯可查看、撤回与恢复。</p>
      {view.data.missing.length > 0 && <p className="muted">当前读取缺少：{view.data.missing.join("、")}</p>}
      {!view.data.items.length && <p className="muted">当前可读范围还没有成长记录。</p>}
      <div className="domain-grid">{view.data.items.map((item) => <article className="domain-record" key={`${item.id}:${item.revision}`}><header><strong>{item.title}</strong><span className="badge">{{ reflection: "角色手记", interest: "兴趣", style: "表达习惯" }[item.kind]}</span></header>
        <p>{item.content}</p>{item.reason && <p>{item.reason}</p>}{item.occurred_at && <time dateTime={item.occurred_at}>{new Date(item.occurred_at).toLocaleString("zh-CN")}</time>}
        <DetailCard title="允许查看的经历依据">{item.evidence.length ? <ul>{item.evidence.map((evidence) => <li key={evidence.id}>{evidence.available ? evidence.summary : "该依据已无法读取"}</li>)}</ul> : <p>尚未取得可显示的依据。</p>}</DetailCard>
        {item.kind === "style" && <button className="button" disabled={action.busy} onClick={() => void action.run(item.state === "revoked" ? "companion/growth/restore" : "companion/growth/revoke", { actor_id: actor, id: item.id }, item.revision).then((result) => { if (result) void view.refresh(); })}>{item.state === "revoked" ? "恢复这项习惯" : "撤回这项习惯"}</button>}
      </article>)}</div><CommandFeedback {...action} />
    </section>}
  </div>;
}
