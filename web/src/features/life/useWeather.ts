import { useEffect, useRef, useState } from "react";
import { integrationPost } from "../../app/integrationApi";
import { weatherFailure, type WeatherClock, type WeatherView } from "./weather";
import { displayTimezone } from "./dayPeriod";

/** Shared current projection; configuration remains in the existing encrypted catalog. */
export function useWeather(
  actor: string,
  csrf: string,
  onClockChange?: (clock: WeatherClock | null) => void,
) {
  const [view, setView] = useState<WeatherView | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const read = useRef<AbortController | null>(null);

  function accept(next: WeatherView) {
    if (next.location) {
      if (!displayTimezone(next.location.tz, next.location.utc_offset)) {
        setError("天气服务返回的时区无效，请重新选择位置。");
        return false;
      }
    }
    setView(next);
    return true;
  }
  async function refresh() {
    if (!actor || !csrf) return;
    read.current?.abort();
    const controller = new AbortController();
    read.current = controller;
    setLoading(true);
    setError("");
    try {
      const next = await integrationPost<WeatherView>(
        "weather/current",
        { actor_id: actor },
        csrf,
        controller.signal,
      );
      if (!controller.signal.aborted) accept(next);
    } catch (cause) {
      if (!controller.signal.aborted) setError(weatherFailure(cause));
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }
  useEffect(() => {
    setView(null);
    setError("");
    void refresh();
    return () => read.current?.abort();
  }, [actor, csrf]);
  useEffect(() => {
    onClockChange?.(
      view?.location && Number.isFinite(view.server_time)
        ? {
            timezone: displayTimezone(
              view.location.tz,
              view.location.utc_offset,
            )!,
            serverTime: view.server_time,
            receivedAt: performance.now(),
          }
        : null,
    );
  }, [view, onClockChange]);
  return { view, error, loading, accept, refresh };
}
