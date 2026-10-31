import { useEffect, useRef, useState } from "react";
import { useAuth } from "../../app/Auth";
import { integrationPost, readFailure } from "../../app/integrationApi";
import {
  readRolePreference,
  saveRolePreference,
} from "../../app/rolePreference";
import { StatePanel } from "../../components/StatePanel";
import { WeatherSettings } from "../life/WeatherSettings";
import "./external.css";

type Actor = { actor_id: string; label?: string };
export function WeatherPanel() {
  const { session } = useAuth();
  const csrf = session?.authenticated ? session.csrf : "";
  const preference = `tianshu-life-role:${encodeURIComponent(session?.username || csrf)}`;
  const [actors, setActors] = useState<Actor[]>([]);
  const [actor, setActor] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  async function list(after: string | null = null) {
    if (!csrf) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError("");
    try {
      const result = await integrationPost<{
        items: Actor[];
        next_after_actor_id: string | null;
      }>(
        "life/actors",
        { limit: 50, after_actor_id: after },
        csrf,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setActors((old) => (after ? [...old, ...result.items] : result.items));
      setCursor(result.next_after_actor_id);
      if (!after)
        setActor(
          result.items.find(
            (item) => item.actor_id === readRolePreference(preference),
          )?.actor_id ??
            result.items[0]?.actor_id ??
            "",
        );
    } catch (cause) {
      if (!controller.signal.aborted) setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  useEffect(() => {
    setActors([]);
    setActor("");
    setCursor(null);
    void list();
    return () => request.current?.abort();
  }, [csrf]);
  return (
    <section
      className="panel external-panel"
      aria-labelledby="weather-settings-title"
    >
      <h2 id="weather-settings-title">位置与天气</h2>
      <div className="external-form">
        <label>
          选择角色
          <select
            value={actor}
            disabled={busy || !actors.length}
            onChange={(event) => {
              setActor(event.target.value);
              saveRolePreference(preference, event.target.value);
            }}
          >
            {!actors.length && <option value="" />}
            {actors.map((item) => (
              <option key={item.actor_id} value={item.actor_id}>
                {item.label || item.actor_id}
              </option>
            ))}
          </select>
        </label>
      </div>
      {cursor && (
        <button
          className="button"
          disabled={busy}
          onClick={() => void list(cursor)}
        >
          读取更多角色
        </button>
      )}
      {error && (
        <StatePanel kind="error" title="角色目录读取失败">
          <p>{error}</p>
        </StatePanel>
      )}
      {!busy && !error && !actors.length && (
        <StatePanel kind="empty" title="没有可读角色">
          <p>添加并授权角色后，可为角色设置天气位置。</p>
        </StatePanel>
      )}
      {actor && <WeatherSettings key={actor} actor={actor} csrf={csrf} />}
    </section>
  );
}
