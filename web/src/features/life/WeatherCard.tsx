import { useEffect } from "react";
import { RefreshCw } from "lucide-react";
import { useWeather } from "./useWeather";
import { WeatherIcon } from "./WeatherIcon";
import { displayTime, displayTimezone } from "./dayPeriod";
import { locationLabel, weatherProblems, type WeatherClock } from "./weather";
import "./weather.css";

/** Read-only conditions. Connection and location controls live in Settings. */
export function WeatherCard({
  actor,
  csrf,
  onClockChange,
  date,
  timezone,
}: {
  actor: string;
  csrf: string;
  onClockChange: (clock: WeatherClock | null) => void;
  date: Date;
  timezone: string;
}) {
  const { view, error, loading, refresh } = useWeather(
    actor,
    csrf,
    onClockChange,
  );
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, 5 * 60_000);
    const resume = () => {
      if (!document.hidden) void refresh();
    };
    document.addEventListener("visibilitychange", resume);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", resume);
    };
  }, [actor, csrf]);

  const weather = view?.weather;
  const stale = Boolean(weather && (view?.stale || error));
  const weatherTime = weather?.observed_at || view?.fetched_at;
  const observed =
    weatherTime && Number.isFinite(Date.parse(weatherTime))
      ? displayTime(
          new Date(weatherTime),
          view?.location
            ? (displayTimezone(view.location.tz, view.location.utc_offset) ??
                timezone)
            : timezone,
          {
            month: "2-digit",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
            hourCycle: "h23",
          },
        )
      : "";
  return (
    <div className="life-weather" aria-label="实际天气" aria-busy={loading}>
      <WeatherIcon
        code={weather?.icon}
        text={weather?.text}
        date={date}
        timezone={timezone}
      />
      <div className="life-weather-copy">
        <div className="life-weather-reading">
          {weather ? (
            <>
              <strong>
                {weather.temp}
                <small>°C</small>
              </strong>
              <span>{weather.text}</span>
            </>
          ) : (
            <strong className="life-weather-empty">
              {loading && !view
                ? "读取天气…"
                : error
                  ? "天气读取失败"
                  : !view?.configured
                    ? "天气未连接"
                    : !view.location
                      ? "尚无天气位置"
                      : "天气暂不可用"}
            </strong>
          )}
        </div>
        {weather ? (
          <p>
            体感 {weather.feels_like}°C · 风力 {weather.wind_scale} 级
          </p>
        ) : (
          <p>
            {view?.location ? locationLabel(view.location) : "暂无实际天气数据"}
          </p>
        )}
        {view?.location && weather && (
          <p className="life-weather-place">
            {locationLabel(view.location)} · {stale ? "上次读数" : "更新于"}{" "}
            {observed || "时间未知"}
          </p>
        )}
        {(stale || error) && (
          <p className="life-weather-warning" role="status">
            {error || "天气更新失败，当前显示上次读数。"}
          </p>
        )}
        {error && (
          <div className="life-weather-actions">
            <button
              type="button"
              disabled={loading}
              onClick={() => void refresh()}
            >
              <RefreshCw size={13} aria-hidden="true" />
              重试天气
            </button>
          </div>
        )}
        {!weather &&
          view?.code &&
          ![
            "weather_not_configured",
            "weather_location_missing",
            "weather_location_required",
          ].includes(view.code) &&
          weatherProblems[view.code] && <p>{weatherProblems[view.code]}</p>}
      </div>
    </div>
  );
}
