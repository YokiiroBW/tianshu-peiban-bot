import { IntegrationError, readFailure } from "../../app/integrationApi";

export type WeatherLocation = {
  id: string;
  name: string;
  adm1: string;
  adm2: string;
  country: string;
  tz: string;
  utc_offset: string;
};
export type WeatherView = {
  revision: number;
  can_manage: boolean;
  configured: boolean;
  credential_configured: boolean;
  host: string | null;
  location: WeatherLocation | null;
  server_time: number;
  code: string;
  weather?: {
    temp: string;
    feels_like: string;
    text: string;
    icon?: string;
    wind_scale: string;
    observed_at: string | null;
    attributions?: string[];
  } | null;
  fetched_at?: string | null;
  stale?: boolean;
};
export type WeatherClock = {
  timezone: string;
  serverTime: number;
  receivedAt: number;
};
export const weatherProblems: Record<string, string> = {
  invalid_weather_host:
    "请填写和风控制台的 API Host，例如 abcxyz.re.qweatherapi.com，不要附带接口路径或参数。",
  weather_store_unavailable: "服务端尚未启用天气连接存储。",
  weather_unauthorized: "和风天气凭据未通过验证，请检查 API Key 和 API Host。",
  weather_unavailable: "和风天气暂时无法连接，请稍后刷新。",
  weather_invalid_response: "和风天气未返回完整数据，请稍后刷新。",
  weather_stale: "当前显示上次读数，等待天气更新。",
  weather_not_configured: "尚未连接和风天气，请先设置 API Host 和凭据。",
  weather_location_required: "请选择天气位置。",
  weather_location_missing: "请选择天气位置。",
  weather_credentials_rejected: "和风天气凭据未通过验证，请检查 API Key。",
  weather_upstream_unavailable: "天气服务暂时无法连接，请稍后刷新。",
  weather_not_available: "天气连接暂不可用。",
  external_not_configured: "服务端尚未配置连接存储。",
  revision_conflict: "配置已更新，请重新读取后再保存。",
};
export function weatherFailure(cause: unknown) {
  return cause instanceof IntegrationError && weatherProblems[cause.code]
    ? weatherProblems[cause.code]
    : readFailure(cause);
}
export function locationLabel(location: WeatherLocation) {
  return [...new Set([location.adm2, location.name].filter(Boolean))].join(
    " · ",
  );
}
