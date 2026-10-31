import { DetailCard } from "../../components/DetailCard";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Search, RefreshCw } from "lucide-react";
import { integrationPost, IntegrationError } from "../../app/integrationApi";
import { requestId } from "../../app/requestId";
import { useWeather } from "./useWeather";
import {
  locationLabel,
  weatherFailure,
  type WeatherLocation,
  type WeatherView,
} from "./weather";
import "./weather.css";

/** The original weather connection/location form, now mounted only in Settings. */
export function WeatherSettings({
  actor,
  csrf,
}: {
  actor: string;
  csrf: string;
}) {
  const { view, error, loading, accept, refresh } = useWeather(actor, csrf);
  const [host, setHost] = useState("");
  const [key, setKey] = useState("");
  const [query, setQuery] = useState("");
  const [locations, setLocations] = useState<WeatherLocation[]>([]);
  const [searched, setSearched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [notice, setNotice] = useState("");
  const mutation = useRef<AbortController | null>(null);
  useEffect(() => {
    setHost(view?.host ?? "");
  }, [view?.host]);
  useEffect(() => () => mutation.current?.abort(), [actor, csrf]);

  async function operation<T>(
    path: string,
    body: object,
  ): Promise<T | undefined> {
    mutation.current?.abort();
    const controller = new AbortController();
    mutation.current = controller;
    setSaving(true);
    setFormError("");
    setNotice("");
    try {
      const result = await integrationPost<T>(
        `weather/${path}`,
        { actor_id: actor, ...body },
        csrf,
        controller.signal,
      );
      if (!controller.signal.aborted) return result;
    } catch (cause) {
      if (!controller.signal.aborted) {
        setFormError(weatherFailure(cause));
        if (cause instanceof IntegrationError && cause.status === 409)
          void refresh();
      }
    } finally {
      if (!controller.signal.aborted) setSaving(false);
    }
  }
  async function configure(event: FormEvent) {
    event.preventDefault();
    if (!view) return;
    const next = await operation<WeatherView>("configure", {
      host: host.trim(),
      credential: key ? { action: "replace", value: key } : { action: "keep" },
      expected_revision: view.revision,
      client_request_id: requestId(),
    });
    if (next) {
      setKey("");
      if (!accept(next)) return;
      setNotice("连接配置已保存。选择位置后会读取实际天气。");
      setLocations([]);
      setSearched(false);
      void refresh();
    }
  }
  async function search(event: FormEvent) {
    event.preventDefault();
    setLocations([]);
    setSearched(false);
    const result = await operation<{ items: WeatherLocation[] }>("locations", {
      query: query.trim(),
    });
    if (result) {
      setLocations(result.items);
      setSearched(true);
    }
  }
  async function choose(location: WeatherLocation) {
    if (!view) return;
    const next = await operation<WeatherView>("location", {
      location_id: location.id,
      expected_revision: view.revision,
      client_request_id: requestId(),
    });
    if (next && accept(next)) {
      setNotice("天气位置已保存。");
      setLocations([]);
      setSearched(false);
      void refresh();
    }
  }
  return (
    <DetailCard title={"位置与天气设置"}>
      <div className="weather-settings" aria-busy={loading || saving}>
        <p className="muted">
          按这位角色所选位置显示天气和当地时间，日程仍按角色原有时区推进。
        </p>
        {error && (
          <p className="life-weather-warning" role="alert">
            {error}
          </p>
        )}
        {view && !view.can_manage && (
          <p className="muted">当前账号没有天气连接管理权限。</p>
        )}
        <form onSubmit={configure} className="life-weather-form">
          <label>
            和风天气 API Host
            <input
              value={host}
              required
              maxLength={255}
              disabled={saving || !view?.can_manage}
              placeholder="例如 abcxyz.re.qweatherapi.com"
              autoComplete="off"
              onChange={(event) => setHost(event.target.value)}
            />
          </label>
          <label>
            API Key
            <input
              type="password"
              value={key}
              required={!view?.credential_configured}
              disabled={saving || !view?.can_manage}
              maxLength={4096}
              autoComplete="new-password"
              placeholder={
                view?.credential_configured
                  ? "已保存，留空保留原凭据"
                  : "输入和风天气 API Key"
              }
              onChange={(event) => setKey(event.target.value)}
            />
          </label>
          <p className="muted">
            连接供所有角色共用；位置分别保存。凭据仅提交到天枢服务端，不会回显。
          </p>
          <button
            className="button"
            disabled={saving || loading || !view?.can_manage}
          >
            保存连接
          </button>
        </form>
        <form onSubmit={search} className="life-weather-search">
          <label>
            搜索城市或区县
            <input
              value={query}
              maxLength={100}
              required
              disabled={saving || !view?.can_manage}
              placeholder="例如：上海、杭州西湖"
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <button
            className="button"
            disabled={
              saving || !view?.can_manage || !view.configured || !query.trim()
            }
          >
            <Search size={18} />
            搜索位置
          </button>
        </form>
        {view?.location && (
          <p className="muted">
            当前：{locationLabel(view.location)} · {view.location.tz}
          </p>
        )}
        {formError && (
          <p className="life-weather-warning" role="alert">
            {formError}
          </p>
        )}
        {notice && <p role="status">{notice}</p>}
        {searched && locations.length === 0 && (
          <p role="status">没有找到位置，请尝试城市名或经纬度。</p>
        )}
        <ul className="life-weather-results">
          {locations.map((location) => (
            <li key={location.id}>
              <button
                type="button"
                disabled={saving}
                onClick={() => void choose(location)}
              >
                <strong>{locationLabel(location)}</strong>
                <span>
                  {location.country} · {location.adm1} · {location.tz}
                </span>
              </button>
            </li>
          ))}
        </ul>
        <footer className="weather-settings-sources">
          <div className="external-actions">
            <a
              href="https://www.qweather.com/"
              target="_blank"
              rel="noreferrer"
            >
              和风天气
            </a>
            <a
              href="https://dev.qweather.com/docs/configuration/api-config/"
              target="_blank"
              rel="noreferrer"
            >
              查看天气接入说明 ↗
            </a>
            <button
              type="button"
              disabled={loading || saving}
              onClick={() => void refresh()}
            >
              <RefreshCw size={14} aria-hidden="true" />
              重新读取
            </button>
          </div>
          {view?.weather?.attributions?.length ? (
            <p className="muted">
              数据归因：
              {view.weather.attributions.map((source, index) => {
                let url: URL | null = null;
                try {
                  const parsed = new URL(source);
                  if (["https:", "http:"].includes(parsed.protocol))
                    url = parsed;
                } catch {
                  /* A provider attribution may be a plain name. */
                }
                return (
                  <span key={`${index}:${source}`}>
                    {index > 0 && " · "}
                    {url ? (
                      <a href={url.href} target="_blank" rel="noreferrer">
                        {url.hostname}
                      </a>
                    ) : (
                      source
                    )}
                  </span>
                );
              })}
            </p>
          ) : null}
        </footer>
      </div>
    </DetailCard>
  );
}
