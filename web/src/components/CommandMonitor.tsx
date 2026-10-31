import { useEffect, useState } from "react";
import type { CommandResult } from "../api/contracts";
import "./command-monitor.css";

type Pending = { client_request_id: string; action_id?: string | null; attempt_id?: string | null; operation?: string; state: string };
export function CommandMonitor() {
  const [entries, setEntries] = useState<Pending[]>([]);
  useEffect(() => {
    const unknown = (event: Event) => {
      const value = (event as CustomEvent).detail as Pending;
      if (!value?.client_request_id) return;
      setEntries((current) => [...current.filter((item) => item.client_request_id !== value.client_request_id), { ...value, state: "unknown" }].slice(-12));
    };
    const observed = (event: Event) => {
      const value = (event as CustomEvent).detail as CommandResult<unknown>;
      if (!value?.client_request_id) return;
      const unresolved = value.receipt && (["pending", "running", "unknown"].includes(value.receipt.execution) || value.receipt.effect === "unknown" || value.receipt.delivery === "unknown");
      setEntries((current) => [...current.filter((item) => item.client_request_id !== value.client_request_id), ...(unresolved ? [{ client_request_id: value.client_request_id, action_id: value.action_id, attempt_id: value.attempt_id, state: value.receipt!.effect === "unknown" || value.receipt!.delivery === "unknown" ? "unknown" : value.receipt!.execution }] : [])].slice(-12));
    };
    const clear = () => setEntries([]);
    window.addEventListener("tianshu:command-unknown", unknown);
    window.addEventListener("tianshu:command-result", observed);
    window.addEventListener("tianshu:session-ended", clear);
    return () => { window.removeEventListener("tianshu:command-unknown", unknown); window.removeEventListener("tianshu:command-result", observed); window.removeEventListener("tianshu:session-ended", clear); };
  }, []);
  if (!entries.length) return null;
  return <aside className="command-monitor panel" aria-live="polite" aria-label="原操作状态">{entries.map((item) => <div key={item.client_request_id}>
    <span>{item.state === "unknown" ? "有一项提交尚未确认" : item.state === "running" ? "原任务正在执行" : "原任务已受理"} · </span>
    <code>{item.client_request_id}</code>{item.action_id && <> · <a href={`#/system/tasks?action=${encodeURIComponent(item.action_id)}`}>查看原任务</a></>}
    <button className="text-link" onClick={() => setEntries((current) => current.filter((value) => value.client_request_id !== item.client_request_id))}>收起记录</button>
  </div>)}</aside>;
}
