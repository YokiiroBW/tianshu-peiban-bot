// Local visual parameters only. This is not a core room contract or living state.
export type RoomValues = {
  window: number;
  sheer: number;
  blackout: number;
  desk: number;
  bedside: number;
  deskWarmth: number;
  bedsideWarmth: number;
};
export type Overrides = Partial<RoomValues>;
export type PreviewInput = {
  hour: number | null;
  overrides: Overrides;
  pose?: "reading" | "idle" | "resting" | "away";
  garment?: HTMLImageElement | null;
};

const clock = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Shanghai",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});
export function localHour(now = new Date(), timezone = "Asia/Shanghai") {
  const formatter =
    timezone === "Asia/Shanghai"
      ? clock
      : new Intl.DateTimeFormat("en-GB", {
          timeZone: timezone,
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hourCycle: "h23",
        });
  const [h, m, s] = formatter.format(now).split(":").map(Number);
  return h + m / 60 + s / 3600 + now.getMilliseconds() / 3600000;
}
export function formatHour(hour: number) {
  const minutes = Math.floor(hour * 60) % 1440;
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}
export function daylight(hour: number) {
  // Deliberately fictional 06:00–19:00 day; no location or weather inference.
  return Math.max(0, Math.sin(((hour - 6) / 13) * Math.PI));
}
export function automaticRoom(hour: number): RoomValues {
  const day = daylight(hour);
  const night = 1 - Math.min(1, day * 3);
  return {
    window: day * 0.65,
    sheer: 0.45 + day * 0.5,
    blackout: 0.06 + 0.94 * Math.min(1, day * 4),
    desk: night * 0.8,
    bedside: night * 0.65,
    deskWarmth: 0.8,
    bedsideWarmth: 1,
  };
}
export function resolveRoom(hour: number, overrides: Overrides): RoomValues {
  return { ...automaticRoom(hour), ...overrides };
}
export function approach(
  current: number,
  target: number,
  dt: number,
  rate: number,
) {
  return target + (current - target) * Math.exp(-rate * dt);
}
