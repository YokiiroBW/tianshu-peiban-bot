/** Display only: offset fallback never changes the role schedule's timezone. */
export function displayTime(
  date: Date,
  timezone: string,
  options: Intl.DateTimeFormatOptions,
  locale = "zh-CN",
) {
  const offset = /^UTC([+-])(\d{2}):(\d{2})$/.exec(timezone);
  const shifted = offset
    ? new Date(
        date.getTime() +
          (offset[1] === "+" ? 1 : -1) *
            (Number(offset[2]) * 60 + Number(offset[3])) *
            60_000,
      )
    : date;
  return new Intl.DateTimeFormat(locale, {
    ...options,
    timeZone: offset ? "UTC" : timezone,
  }).format(shifted);
}
export function displayTimezone(zone: string, offset: string) {
  try {
    new Intl.DateTimeFormat("en-GB", { timeZone: zone }).format();
    return zone;
  } catch {
    const parts = /^([+-])(\d{2}):(\d{2})$/.exec(offset);
    return parts && Number(parts[2]) <= 23 && Number(parts[3]) <= 59
      ? `UTC${offset}`
      : null;
  }
}
/** Periods describe the local hour, not astronomical sunrise/sunset. */
export function localHour(date: Date, timezone: string) {
  return Number(
    displayTime(
      date,
      timezone,
      {
        hour: "2-digit",
        hourCycle: "h23",
      },
      "en-GB",
    ),
  );
}
export const dayPeriods = [
  { id: "late-night", label: "深夜", end: 5, night: true },
  { id: "dawn", label: "清晨", end: 7, night: false },
  { id: "morning", label: "早晨", end: 11, night: false },
  { id: "noon", label: "中午", end: 13, night: false },
  { id: "afternoon", label: "下午", end: 17, night: false },
  { id: "dusk", label: "傍晚", end: 19, night: false },
  { id: "night", label: "夜晚", end: 24, night: true },
] as const;
export function dayPeriod(date: Date, timezone: string) {
  const hour = localHour(date, timezone);
  return dayPeriods.find((period) => hour < period.end) ?? dayPeriods[0];
}
