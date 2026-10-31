import { useState } from "react";
import {
  useDomainCommand,
  useDomainResource,
  GLOBAL_SCOPE,
} from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";
import type { WeatherLocation } from "./WeatherCard";
import type { LifeState } from "./domainTypes";

type WeatherView = {
  configured: boolean;
  reason: string | null;
  connection: {
    id: string;
    revision: number;
    host?: string;
    configured: boolean;
    enabled: boolean;
    quota_group?: string;
    account_id?: string;
  };
};
export function WeatherConnectionSettings() {
  const view = useDomainResource<WeatherView>("weather/view", GLOBAL_SCOPE);
  const action = useDomainCommand("weather-connection", "global");
  const row = view.data?.connection;
  return (
    <section className="panel">
      <h3>实际天气连接</h3>
      <p className="muted">使用已配置的QWeather主机与账号，密钥仅写入。</p>
      {view.error && <p role="alert">{view.error}</p>}
      {row && (
        <form
          key={row.revision}
          className="domain-form"
          onSubmit={(event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            void action
              .run(
                "weather/connections.save",
                {
                  host: data.get("host"),
                  quota_group: data.get("quota_group"),
                  account_id: data.get("account_id"),
                  enabled: data.get("enabled") === "on",
                  ...(data.get("api_key")
                    ? { api_key: data.get("api_key") }
                    : {}),
                  clear_credential: data.get("clear") === "on",
                },
                row.revision,
              )
              .then((result) => {
                if (result) void view.refresh();
              });
          }}
        >
          <p>
            {row.configured ? "已保存天气凭据" : "尚未配置天气凭据"} ·{" "}
            {view.data?.reason ?? "连接配置可用"}
          </p>
          <label>
            QWeather API主机
            <input
              name="host"
              defaultValue={row.host ?? ""}
              required
              placeholder="https://你的主机.qweatherapi.com"
            />
          </label>
          <label>
            账号标识
            <input
              name="account_id"
              defaultValue={row.account_id ?? "qweather"}
              required
            />
          </label>
          <label>
            资源配额组
            <input
              name="quota_group"
              defaultValue={row.quota_group ?? ""}
              required
            />
          </label>
          <label>
            新API Key
            <input
              name="api_key"
              type="password"
              autoComplete="new-password"
              placeholder={
                row.configured ? "留空保留已存密钥" : "填入实际API Key"
              }
            />
          </label>
          <label className="inline-check">
            <input
              name="enabled"
              type="checkbox"
              defaultChecked={row.enabled}
            />
            启用天气
          </label>
          <label className="inline-check">
            <input name="clear" type="checkbox" />
            清除已存密钥
          </label>
          <button className="button primary" disabled={action.busy}>
            保存天气连接
          </button>
          <CommandFeedback {...action} />
        </form>
      )}
    </section>
  );
}

export function WeatherLocationSettings({
  state,
  refresh,
}: {
  state: LifeState;
  refresh: () => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const search = useDomainResource<{ items: WeatherLocation[] }>(
    "weather/search",
    { query },
    !!query,
  );
  const action = useDomainCommand("weather-location", "actor");
  return (
    <section className="panel">
      <h3>角色的实际天气位置</h3>
      <p>当前保存的位置编号：{state.weather_location ?? "尚未选择"}</p>
      <form
        className="domain-form"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(
            String(new FormData(event.currentTarget).get("query") ?? ""),
          );
        }}
      >
        <label>
          城市或地区
          <input name="query" required placeholder="例如：杭州、北京或城市ID" />
        </label>
        <button className="button" disabled={search.loading}>
          查找实际位置
        </button>
      </form>
      {search.error && <p role="alert">{search.error}</p>}
      <div className="domain-grid">
        {search.data?.items.map((item) => (
          <article className="domain-record" key={item.id}>
            <strong>{item.name}</strong>
            <p>
              {[item.country, item.adm1, item.adm2].filter(Boolean).join(" · ")}{" "}
              · {item.tz}
            </p>
            <button
              className="button"
              disabled={action.busy}
              onClick={() =>
                void action
                  .run(
                    "life/room.save",
                    { controls: {}, weather_location: item.id },
                    state.revision,
                  )
                  .then((result) => {
                    if (result) void refresh();
                  })
              }
            >
              使用这个位置
            </button>
          </article>
        ))}
      </div>
      {state.weather_location && (
        <button
          className="text-link"
          disabled={action.busy}
          onClick={() =>
            void action
              .run(
                "life/room.save",
                { controls: {}, weather_location: null },
                state.revision,
              )
              .then((result) => {
                if (result) void refresh();
              })
          }
        >
          解除天气位置
        </button>
      )}
      <CommandFeedback {...action} />
    </section>
  );
}
