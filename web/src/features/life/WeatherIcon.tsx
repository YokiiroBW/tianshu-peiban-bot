import {
  Cloud,
  CloudFog,
  CloudLightning,
  CloudMoon,
  CloudRain,
  CloudSnow,
  CloudSun,
  CircleHelp,
  Haze,
  Moon,
  Sun,
  Wind,
} from "lucide-react";
import { localHour } from "./dayPeriod";

// QWeather condition codes: https://icons.qweather.com/icons/ . Unknown codes
// use a neutral symbol; text is a fallback only when the code is absent.
function condition(code?: string, text?: string) {
  if (code?.trim()) {
    const n = Number(code);
    if (n === 100 || n === 150) return "clear";
    if ([101, 102, 103, 151, 152, 153].includes(n)) return "partly-cloudy";
    if (n === 104) return "overcast";
    if ([302, 303, 304].includes(n)) return "thunder";
    if ([404, 405].includes(n)) return "sleet";
    if (n >= 300 && n <= 399) return "rain";
    if (n >= 400 && n <= 499) return "snow";
    if ([503, 504, 507, 508].includes(n)) return "dust";
    if ([502, 511, 512, 513].includes(n)) return "haze";
    if ([500, 501, 509, 510, 514, 515].includes(n)) return "fog";
    return "unknown";
  }
  if (/雷|冰雹|thunder|hail/i.test(text ?? "")) return "thunder";
  if (/雨夹雪|雨雪|sleet|rain and snow/i.test(text ?? "")) return "sleet";
  if (/雪|snow/i.test(text ?? "")) return "snow";
  if (/雨|rain|shower/i.test(text ?? "")) return "rain";
  if (/沙|尘|sand|dust/i.test(text ?? "")) return "dust";
  if (/霾|haze/i.test(text ?? "")) return "haze";
  if (/雾|fog|mist/i.test(text ?? "")) return "fog";
  if (/阴|overcast/i.test(text ?? "")) return "overcast";
  if (/云|cloud/i.test(text ?? "")) return "partly-cloudy";
  if (/晴|clear|sunny/i.test(text ?? "")) return "clear";
  return "unknown";
}
export function WeatherIcon({
  code,
  text,
  date,
  timezone,
}: {
  code?: string;
  text?: string;
  date: Date;
  timezone: string;
}) {
  const kind = condition(code, text);
  const hour = localHour(date, timezone);
  const night = hour < 6 || hour >= 19;
  const Icon = {
    clear: night ? Moon : Sun,
    "partly-cloudy": night ? CloudMoon : CloudSun,
    overcast: Cloud,
    thunder: CloudLightning,
    rain: CloudRain,
    sleet: CloudSnow,
    snow: CloudSnow,
    dust: Wind,
    haze: Haze,
    fog: CloudFog,
    unknown: CircleHelp,
  }[kind];
  return (
    <div
      className="life-weather-art"
      data-condition={kind}
      data-night={night || undefined}
    >
      {kind === "sleet" ? (
        <svg
          viewBox="0 0 24 24"
          aria-hidden="true"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M6 15a4 4 0 0 1-1-8 6 6 0 0 1 11-2 5 5 0 0 1 3 10" />
          <path d="m8 17-1 3m4-4-1 3m7-3v6m-3-4.5 6 3m-6 0 6-3" />
        </svg>
      ) : (
        <Icon aria-hidden="true" />
      )}
    </div>
  );
}
