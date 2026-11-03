/**
 * 传统十二时辰、刻数与昼夜双境流转工具
 * 严格遵循定稿方案 8-07：「多宝博古 · 宋式格架规整列阵」× 昼夜双境流转系统
 */

export type CurioThemeMode = "day" | "night" | "auto";
export type ResolvedCurioTheme = "day" | "night";

export interface ShichenInfo {
  /** 地支单字，如「丑」 */
  branch: string;
  /** 时辰名，如「丑时」 */
  name: string;
  /** 刻数，如「二刻」 */
  ke: string;
  /** 完整时辰刻数，如「丑时二刻」 */
  fullLabel: string;
  /** 宋韵意境短语，如「万籁俱寂」 */
  poeticMood: string;
  /** 组合展示文案，如「丑时二刻 · 万籁俱寂」 */
  caption: string;
  /** 24小时制时间文本，如 "02:30" */
  clockText: string;
  /** 是否处于昼境时段 (06:00 - 18:30) */
  isDaytime: boolean;
  /** 当前角色时区下距下次晨光/夜幕的提示 */
  nextTransitionLabel: string;
  /** 剩余时间简述 */
  remainingLabel: string;
}

const SHICHEN_TABLE: Array<{
  branch: string;
  poetic: string;
  roomHint: string;
}> = [
  { branch: "子", poetic: "夜半钟声", roomHint: "夜深露重，案头留一盏微光" }, // 23:00 - 00:59
  {
    branch: "丑",
    poetic: "万籁俱寂",
    roomHint: "台灯留一盏暖黄微光，静候卯时晨光熹微",
  }, // 01:00 - 02:59
  { branch: "寅", poetic: "平旦微明", roomHint: "东方既白，晨雾尚未散尽" }, // 03:00 - 04:59
  { branch: "卯", poetic: "晨光熹微", roomHint: "旭日初升，推窗迎一缕清风" }, // 05:00 - 06:59
  { branch: "辰", poetic: "朝露清朗", roomHint: "晨读整卷，茶烟袅袅初起" }, // 07:00 - 08:59
  { branch: "巳", poetic: "日暖风恬", roomHint: "轩窗明净，宜展卷研墨" }, // 09:00 - 10:59
  { branch: "午", poetic: "日正天心", roomHint: "日影中天，小憩片刻养神" }, // 11:00 - 12:59
  { branch: "未", poetic: "午后清欢", roomHint: "日影西斜，临窗静画速写" }, // 13:00 - 14:59
  { branch: "申", poetic: "哺时风清", roomHint: "烹茶听风，整理今日所得" }, // 15:00 - 16:59
  { branch: "酉", poetic: "暮色晕染", roomHint: "落日熔金，归鸟掠过檐角" }, // 17:00 - 18:59
  { branch: "戌", poetic: "华灯初上", roomHint: "挑灯夜读，把夜晚安静下来" }, // 19:00 - 20:59
  { branch: "亥", poetic: "人定掩卷", roomHint: "掩卷静息，记下今日最后一页" }, // 21:00 - 22:59
];

const KE_NUMERALS = [
  "初刻",
  "一刻",
  "二刻",
  "三刻",
  "四刻",
  "五刻",
  "六刻",
  "七刻",
];

export function extractZonedHourMinute(
  date: Date = new Date(),
  timeZone?: string,
): { hour: number; minute: number; second: number; validZone: string } {
  const fallbackZone =
    Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai";
  const zone = timeZone?.trim() || fallbackZone;

  try {
    const parts = new Intl.DateTimeFormat("zh-CN", {
      timeZone: zone,
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }).formatToParts(date);

    const hour = Number(parts.find((p) => p.type === "hour")?.value ?? 0) % 24;
    const minute = Number(parts.find((p) => p.type === "minute")?.value ?? 0);
    const second = Number(parts.find((p) => p.type === "second")?.value ?? 0);
    return { hour, minute, second, validZone: zone };
  } catch {
    return {
      hour: date.getHours(),
      minute: date.getMinutes(),
      second: date.getSeconds(),
      validZone: fallbackZone,
    };
  }
}

export function getShichenInfo(
  date: Date = new Date(),
  timeZone?: string,
): ShichenInfo {
  const { hour, minute } = extractZonedHourMinute(date, timeZone);
  const shichenIndex = Math.floor(((hour + 1) % 24) / 2);
  const entry = SHICHEN_TABLE[shichenIndex] ?? SHICHEN_TABLE[0];

  // 计算当前时辰内的分钟数 (0 - 119)
  const startHour = (shichenIndex * 2 + 23) % 24;
  const elapsedHours = (hour - startHour + 24) % 24;
  const totalMinutesInShichen = elapsedHours * 60 + minute;
  const keIndex = Math.min(7, Math.floor(totalMinutesInShichen / 15));
  const ke = KE_NUMERALS[keIndex] ?? "初刻";

  const totalMinutes = hour * 60 + minute;
  // 06:00 (360) ~ 18:30 (1110) 为宣纸昼境
  const isDaytime = totalMinutes >= 360 && totalMinutes < 1110;

  const transitionMinute = isDaytime ? 1110 : 360;
  const minutesToTransition = (transitionMinute - totalMinutes + 1440) % 1440;
  const hoursLeft = Math.floor(minutesToTransition / 60);
  const minutesLeft = minutesToTransition % 60;

  const nextTransitionLabel = isDaytime
    ? "🌇 酉时 (18:30) 入夜"
    : "🌅 卯时 (06:00) 天明";
  const remainingLabel =
    hoursLeft > 0
      ? `⏳ 尚余 ${hoursLeft}h ${minutesLeft}m`
      : `⏳ 尚余 ${minutesLeft}m`;

  const clockText = `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;

  return {
    branch: entry.branch,
    name: `${entry.branch}时`,
    ke,
    fullLabel: `${entry.branch}时${ke}`,
    poeticMood: entry.poetic,
    caption: `${entry.branch}时${ke} · ${entry.poetic}`,
    clockText,
    isDaytime,
    nextTransitionLabel,
    remainingLabel,
  };
}

/** 已配置清醒时段的时间进度；不代表活动完成率。 */
export function routineTimeProgress(
  date: Date,
  timeZone: string,
  wakeAt: string,
  sleepAt: string,
): number | null {
  const parse = (value: string) =>
    /^([01]\d|2[0-3]):[0-5]\d$/.test(value)
      ? Number(value.slice(0, 2)) * 60 + Number(value.slice(3))
      : null;
  const wake = parse(wakeAt),
    sleep = parse(sleepAt);
  if (wake === null || sleep === null || wake === sleep) return null;
  const { hour, minute } = extractZonedHourMinute(date, timeZone);
  const duration = (sleep - wake + 1440) % 1440;
  const elapsed = (hour * 60 + minute - wake + 1440) % 1440;
  return elapsed < duration ? (elapsed / duration) * 100 : 0;
}

export function formatTimeWithShichen(
  isoOrText: string | null | undefined,
  timeZone?: string,
): string {
  if (!isoOrText) return "未记录";
  const parsed = new Date(isoOrText);
  if (Number.isNaN(parsed.getTime())) return isoOrText;
  const shichen = getShichenInfo(parsed, timeZone);
  try {
    const formatted = parsed.toLocaleString("zh-CN", {
      timeZone: timeZone || undefined,
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
    return `${formatted} (${shichen.name})`;
  } catch {
    return `${parsed.toLocaleString("zh-CN")} (${shichen.name})`;
  }
}

export function resolveCurioTheme(
  mode: CurioThemeMode,
  date: Date = new Date(),
  roleTimezone?: string,
): ResolvedCurioTheme {
  if (mode === "day") return "day";
  if (mode === "night") return "night";
  const info = getShichenInfo(date, roleTimezone);
  return info.isDaytime ? "day" : "night";
}
