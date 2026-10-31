import { useEffect, useRef, useState, type FormEvent } from "react";
import { LockKeyhole } from "lucide-react";
import { integrationPost, readFailure } from "../../app/integrationApi";
export function ObservationUnlock({
  csrf,
  onUnlocked,
  title = "解锁会话来源与回复管理",
  description = "身份目录仍可查看。会话观察沿用机器人管理的密码验证。",
  buttonLabel = "解锁用户会话",
}: {
  csrf: string;
  onUnlocked: () => void;
  title?: string;
  description?: string;
  buttonLabel?: string;
}) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    try {
      await integrationPost(
        "bots/unlock",
        { password },
        csrf,
        controller.signal,
      );
      if (!controller.signal.aborted) onUnlocked();
    } catch (cause) {
      if (!controller.signal.aborted) setError(readFailure(cause));
    } finally {
      setPassword("");
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  return (
    <form
      className="people-unlock people-directory-unlock"
      onSubmit={(event) => void submit(event)}
    >
      <LockKeyhole size={20} aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <p>{description}</p>
        <div className="people-unlock-controls">
          <label className="people-field">
            管理员密码
            <input
              aria-label="管理员密码"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
          <button className="button" disabled={busy || !password}>
            {buttonLabel}
          </button>
        </div>
        {error && <p role="alert">{error}</p>}
      </div>
    </form>
  );
}
