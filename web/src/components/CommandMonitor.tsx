import { useEffect, useState } from "react";
import type { CommandResult } from "../api/contracts";
import type { CommandTarget } from "../api/client";
import { recoverCommand, taskHref } from "../api/commandRecovery";
import { useAuth } from "../app/Auth";
import "./command-monitor.css";

type Pending = { client_request_id: string; action_id?: string | null; attempt_id?: string | null; operation?: string; capability_id?: string; target?: CommandTarget; state: string; note?: string };
export function CommandMonitor() {
  const { session } = useAuth();
  const [entries, setEntries] = useState<Pending[]>([]);
  const [checking, setChecking] = useState("");
  async function check(value: Pending) {
    if (!session?.csrf || checking) return;
    setChecking(value.client_request_id);
    try {
      const result = await recoverCommand(value, session.csrf, new AbortController().signal);
      const receipt = result.action?.receipt;
      setEntries((current) => current.map((item) => item.client_request_id === value.client_request_id ? {
        ...item, action_id: receipt?.action_id ?? result.command?.action_id ?? item.action_id,
        attempt_id: receipt?.attempt_id ?? result.command?.attempt_id ?? item.attempt_id,
        state: receipt?.execution ?? (result.found ? "recorded" : "unknown"),
        note: result.found ? receipt ? `原执行状态：${receipt.execution}；效果：${receipt.effect}；投递：${receipt.delivery}` : "已找到原命令身份，请刷新对应页面核对当前版本。" : "当前没有查到原登记，仍不能据此断言服务端已撤销。",
      } : item));
    } catch (cause) {
      setEntries((current) => current.map((item) => item.client_request_id === value.client_request_id ? { ...item, note: cause instanceof Error ? cause.message : "尚未取得原操作状态。" } : item));
    } finally { setChecking(""); }
  }
  useEffect(() => {
    const unknown = (event: Event) => {
      const value = (event as CustomEvent).detail as Pending;
      if (!value?.client_request_id) return;
      setEntries((current) => [...current.filter((item) => item.client_request_id !== value.client_request_id), { ...value, state: "unknown" }].slice(-12));
    };
    const observed = (event: Event) => {
      const value = (event as CustomEvent).detail as CommandResult<unknown> & Pick<Pending, "target" | "operation" | "capability_id">;
      if (!value?.client_request_id) return;
      const unresolved = value.receipt && (["pending", "running", "unknown"].includes(value.receipt.execution) || value.receipt.effect === "unknown" || value.receipt.delivery === "unknown");
      setEntries((current) => [...current.filter((item) => item.client_request_id !== value.client_request_id), ...(unresolved ? [{ client_request_id: value.client_request_id, action_id: value.action_id, attempt_id: value.attempt_id, target: value.target, operation: value.operation, capability_id: value.capability_id, state: value.receipt!.effect === "unknown" || value.receipt!.delivery === "unknown" ? "unknown" : value.receipt!.execution }] : [])].slice(-12));
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
    <code>{item.client_request_id}</code> · <a href={taskHref(item)}>查看原任务</a>
    <button className="text-link" disabled={!!checking} onClick={() => void check(item)}>{checking === item.client_request_id ? "正在核对" : "核对原操作"}</button>
    {item.note && <span>{item.note}</span>}
    <button className="text-link" onClick={() => setEntries((current) => current.filter((value) => value.client_request_id !== item.client_request_id))}>收起记录</button>
  </div>)}</aside>;
}
