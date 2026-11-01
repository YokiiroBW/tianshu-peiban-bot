/** Calendar inputs use the role's local time, independently of the browser zone. */
export function localDay(zone: string) {
  return new Date().toLocaleDateString("sv-SE", { timeZone: zone });
}

export function zonedInput(value: string, zone: string) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: zone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(value));
  const get = (kind: string) =>
    parts.find((part) => part.type === kind)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}`;
}

export function zonedInstant(input: string, zone: string) {
  const values = input.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/);
  if (!values) throw new Error("请选择有效的日期和时间。");
  const desired = Date.UTC(
    +values[1],
    +values[2] - 1,
    +values[3],
    +values[4],
    +values[5],
  );
  let guess = desired;
  for (let index = 0; index < 3; index++) {
    const represented = zonedInput(new Date(guess).toISOString(), zone);
    const asUtc = Date.parse(represented + ":00Z");
    guess += desired - asUtc;
  }
  if (zonedInput(new Date(guess).toISOString(), zone) !== input)
    throw new Error("这个当地时间不存在，请选择相邻的有效时间。");
  return new Date(guess).toISOString();
}
