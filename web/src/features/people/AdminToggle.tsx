import { useEffect, useRef, useState } from "react";
import { IntegrationError, integrationPost } from "../../app/integrationApi";

type Grant = {
  qq_id: string;
  capabilities: string[];
  actor_ids: string[];
  conversations: string[];
};
type View = { version: number; grants: Grant[] };

export function AdminToggle({ qqId, csrf }: { qqId: string; csrf: string }) {
  const [view, setView] = useState<View | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);
  const grant = view?.grants.find((item) => item.qq_id === qqId);
  const enabled = grant?.capabilities.includes("identity.explain") ?? false;
  const scoped =
    enabled && !!(grant?.actor_ids.length || grant?.conversations.length);

  async function load() {
    active.current?.abort();
    const request = new AbortController();
    active.current = request;
    setBusy(true);
    setError("");
    try {
      const result = await integrationPost<View>(
        "qq-admin/view",
        {},
        csrf,
        request.signal,
      );
      if (!request.signal.aborted) setView(result);
    } catch {
      if (!request.signal.aborted) {
        setView(null);
        setError("无法读取管理员状态，请重试。");
      }
    } finally {
      if (!request.signal.aborted) setBusy(false);
    }
  }

  useEffect(() => {
    void load();
    return () => active.current?.abort();
  }, [qqId, csrf]);

  async function toggle() {
    if (!view || busy) return;
    active.current?.abort();
    const request = new AbortController();
    active.current = request;
    setBusy(true);
    setError("");
    try {
      const result = await integrationPost<View>(
        `qq-admin/${enabled ? "revoke" : "grant"}`,
        {
          qq_id: qqId,
          expected_revision: view.version,
          ...(!enabled && {
            note: `QQ ${qqId}`,
            actor_ids: [],
            conversations: [],
            capabilities: ["identity.explain"],
          }),
        },
        csrf,
        request.signal,
      );
      if (!request.signal.aborted) setView(result);
    } catch (cause) {
      if (!request.signal.aborted) {
        setView(null);
        setError(
          cause instanceof IntegrationError && cause.code === "version_conflict"
            ? "管理员设置已变化，请刷新状态后重试。"
            : cause instanceof IntegrationError && cause.status === 403
              ? "当前账号不能修改管理员设置。"
              : "未能确认保存结果，请刷新状态后核对。",
        );
      }
    } finally {
      if (!request.signal.aborted) setBusy(false);
    }
  }

  return (
    <div className="people-admin">
      <label className="people-admin-label">
        <input
          type="checkbox"
          role="switch"
          checked={enabled}
          disabled={busy || !view}
          onChange={() => void toggle()}
        />
        设为管理员
      </label>
      <span className="people-admin-state" role="status">
        {busy
          ? "正在同步…"
          : !view
            ? "状态未确认"
            : scoped
              ? "管理员 · 限定范围"
              : enabled
                ? "管理员"
                : "普通用户"}
      </span>
      {error && (
        <div className="people-admin-error" role="alert">
          {error}
          <button type="button" disabled={busy} onClick={() => void load()}>
            刷新状态
          </button>
        </div>
      )}
    </div>
  );
}
