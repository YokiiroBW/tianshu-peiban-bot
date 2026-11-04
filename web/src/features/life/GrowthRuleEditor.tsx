import { useState } from "react";
import { useDomainCommand, type DomainTarget } from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";
import type { GrowthItem } from "./domainTypes";

export function GrowthRuleEditor({ item, scope, onClose, onSaved }: { item: GrowthItem; scope: DomainTarget; onClose: () => void; onSaved: () => Promise<void> }) {
  const [title, setTitle] = useState(item.title);
  const [content, setContent] = useState(item.content);
  const [conditions, setConditions] = useState(JSON.stringify(item.conditions, null, 2));
  const [counterexamples, setCounterexamples] = useState(item.counterexamples.join("\n"));
  const [error, setError] = useState("");
  const action = useDomainCommand(`growth-revise:${item.id}`, "actor");
  return <form className="domain-form" onSubmit={(event) => {
    event.preventDefault();
    try {
      const parsed: unknown = JSON.parse(conditions);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("适用条件需要是一个对象。");
      setError("");
      void action.run("companion/growth/revise", { ...scope, id: item.id, title, content, conditions: parsed,
        counterexamples: counterexamples.split("\n").map((value) => value.trim()).filter(Boolean) }, item.revision)
        .then(async (result) => { if (result) await onSaved(); });
    } catch (cause) { setError(cause instanceof Error ? cause.message : "适用条件无法读取。"); }
  }}>
    <h4>修订当前规则</h4>
    <label>标题<input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
    <label>正文<textarea value={content} rows={4} onChange={(event) => setContent(event.target.value)} /></label>
    <label>适用条件（JSON 对象）<textarea value={conditions} rows={4} onChange={(event) => setConditions(event.target.value)} /></label>
    <label>反例（每行一项）<textarea value={counterexamples} rows={3} onChange={(event) => setCounterexamples(event.target.value)} /></label>
    {error && <p role="alert">{error}</p>}
    <div className="domain-actions"><button className="button primary" disabled={action.busy}>保存修订</button><button type="button" className="button" onClick={onClose}>取消编辑</button></div>
    <CommandFeedback {...action} />
  </form>;
}
