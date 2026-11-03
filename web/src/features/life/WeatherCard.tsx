import { useDomainResource } from "../../api/useDomainResource";
import { WeatherLandscapeSvg } from "../../components/curio/OrientalWidgets";
import { WeatherIcon } from "./WeatherIcon";
import "./weather.css";

export type WeatherLocation = {
  id: string;
  name: string;
  adm1: string;
  adm2: string;
  country: string;
  tz: string;
  utc_offset: string | null;
};
export type CurrentWeather = {
  location: WeatherLocation | null;
  weather: {
    temp: string;
    feels_like: string;
    text: string;
    icon: string;
    wind_scale: string;
    attributions: string[];
    observed_at: string | null;
  } | null;
  description: string;
  observed_at: string | null;
  fetched_at: string | null;
  expires_at: string | null;
  stale: boolean;
  code: string;
  recovery?: "observe_original_attempt" | null;
  retry_after?: string | null;
};

export function WeatherCard({
  locationId,
  date,
  timezone,
}: {
  locationId: string | null;
  date: Date;
  timezone: string;
}) {
  const view = useDomainResource<CurrentWeather>(
    "weather/current",
    { location_id: locationId },
    !!locationId,
    300000,
  );
  const current = view.data;
  const placeText = current?.location
    ? [current.location.adm1, current.location.adm2, current.location.name]
        .filter(Boolean)
        .join(" · ")
    : "角色地点与实际天气位置分别保存";

  return (
    <div
      className="ambient-cell life-weather"
      aria-label="实际天气"
      aria-busy={view.loading}
    >
      <WeatherLandscapeSvg />
      <div className="cell-head">
        <span className="cell-kicker">节气气象 · 实时观测</span>
        {current?.weather ? (
          <span className="celadon-tag">
            体感 {current.weather.feels_like}°C
          </span>
        ) : (
          <span className="meta-tag">气象台</span>
        )}
      </div>

      <div className="cell-main life-weather-copy">
        <div className="curio-weather-row">
          <WeatherIcon
            code={current?.weather?.icon}
            text={current?.weather?.text}
            date={date}
            timezone={timezone}
          />
          <div className="life-weather-reading big-metric">
            {current?.weather ? (
              <>
                <strong>
                  {current.weather.temp}
                  <small>°C</small>
                </strong>
                <span>{current.weather.text}</span>
              </>
            ) : (
              <strong className="life-weather-empty">
                {!locationId
                  ? "尚未选择天气位置"
                  : view.loading
                    ? "读取天气…"
                    : view.error
                      ? "天气暂不可用"
                      : current?.recovery === "observe_original_attempt"
                        ? "天气原结果尚未确认"
                        : "尚无新的实际读数"}
              </strong>
            )}
          </div>
        </div>

        <div className="curio-weather-tags">
          {current?.weather && (
            <span className="meta-tag">
              体感 {current.weather.feels_like}°C · 风力{" "}
              {current.weather.wind_scale}级
            </span>
          )}
          <span className="meta-tag">📍 {placeText}</span>
        </div>

        {current?.recovery === "observe_original_attempt" && (
          <p role="status" className="muted">
            原天气读取仍在等待或结果未知，继续观察原请求。
            {current.retry_after
              ? ` 资源占位截止 ${new Date(current.retry_after).toLocaleString("zh-CN")}；截止不等于已取得新读数。`
              : " 尚未取得资源占位截止时间。"}
          </p>
        )}
        {current?.stale && (
          <p className="muted">当前展示上次保留的过期读数。</p>
        )}
        {view.error && <p role="alert">{view.error}</p>}
      </div>

      <div className="cell-foot">
        <span>
          {current?.fetched_at
            ? `${current.stale ? "上次读数 · " : "获取于 "}${new Date(current.fetched_at).toLocaleString("zh-CN")}`
            : "尚未取得观测时间"}
          {current?.fetched_at &&
            (current.observed_at
              ? ` · 供应商观测 ${new Date(current.observed_at).toLocaleString("zh-CN")}`
              : " · 供应商未提供观测时间")}
        </span>
        <div className="domain-actions">
          <button
            type="button"
            className="text-link ink-link"
            disabled={!locationId || view.loading}
            onClick={() => void view.refresh()}
          >
            {current?.recovery === "observe_original_attempt"
              ? "查询原读取状态"
              : "刷新天气"}
          </button>
          <a className="text-link ink-link" href="#/settings/weather">
            天气连接设置
          </a>
        </div>
      </div>
      {current?.weather?.attributions.map((value) => (
        <small className="muted" key={value}>
          {value}
        </small>
      ))}
    </div>
  );
}
