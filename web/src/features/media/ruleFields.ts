import type { Rule, Rules } from "./types";

export const ruleFields: {
  value: Rule["field"];
  label: string;
  kind: "text" | "date" | "number" | "boolean";
}[] = [
  { value: "title", label: "标题", kind: "text" },
  { value: "description", label: "简介", kind: "text" },
  { value: "uploader_id", label: "UP 主 ID", kind: "text" },
  { value: "uploader_name", label: "UP 主昵称", kind: "text" },
  { value: "tags", label: "标签", kind: "text" },
  { value: "published_at", label: "发布时间", kind: "date" },
  { value: "favorited_at", label: "收藏时间", kind: "date" },
  { value: "part_count", label: "分 P 数量", kind: "number" },
  { value: "duration_seconds", label: "视频总时长（秒）", kind: "number" },
  { value: "is_collaboration", label: "联合投稿", kind: "boolean" },
];
export const ruleOperations: Record<Rule["op"], string> = {
  equals: "等于",
  not_equals: "不等于",
  contains: "包含",
  prefix: "开头为",
  suffix: "结尾为",
  regex: "正则表达式",
  gt: "大于 / 晚于",
  gte: "大于等于 / 不早于",
  lt: "小于 / 早于",
  lte: "小于等于 / 不晚于",
  between: "范围（含两端）",
};
export const fieldDefinition = (field: Rule["field"]) =>
  ruleFields.find((item) => item.value === field)!;
export function fieldOperations(field: Rule["field"]): Rule["op"][] {
  const kind = fieldDefinition(field).kind;
  if (kind === "text")
    return ["contains", "equals", "not_equals", "prefix", "suffix", "regex"];
  if (kind === "boolean") return ["equals", "not_equals"];
  return ["equals", "not_equals", "gt", "gte", "lt", "lte", "between"];
}
export function ruleInputError(rule: Rule): string {
  const kind = fieldDefinition(rule.field).kind;
  const values = Array.isArray(rule.value) ? rule.value : [rule.value];
  if (rule.op === "between" && values.length !== 2) return "请填写范围的两端。";
  if (kind === "boolean")
    return typeof rule.value === "boolean" ? "" : "请选择是或否。";
  if (kind === "text") {
    if (typeof rule.value !== "string" || !rule.value) return "请填写匹配值。";
    return Array.from(rule.value).length > (rule.op === "regex" ? 512 : 4096)
      ? "匹配值超过字符上限。"
      : "";
  }
  if (kind === "number") {
    if (
      values.some(
        (value) =>
          typeof value !== "number" ||
          !Number.isFinite(value) ||
          value < 0 ||
          (rule.field === "part_count" && !Number.isInteger(value)),
      )
    )
      return rule.field === "part_count"
        ? "请填写非负整数。"
        : "请填写非负秒数。";
  } else if (
    values.some(
      (value) =>
        typeof value !== "string" ||
        !/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) ||
        !Number.isFinite(Date.parse(value)),
    )
  ) {
    return "请填写有效的日期和时间。";
  }
  const comparable = values.map((value) =>
    kind === "date" ? Date.parse(String(value)) : Number(value),
  );
  return rule.op === "between" && comparable[0] > comparable[1]
    ? "范围起点不能晚于或大于终点。"
    : "";
}
export const hasInvalidRules = (rules: Rules) =>
  [...rules.blacklist, ...rules.whitelist].some((group) =>
    group.rules.some((rule) => !!ruleInputError(rule)),
  );

/** Saved timestamps keep their original instant until the user edits a field. */
export function localDateTime(value: unknown): string {
  if (
    typeof value !== "string" ||
    !value ||
    !Number.isFinite(Date.parse(value))
  )
    return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 23);
}
export function dateTimeValue(value: string): string {
  if (!value || !Number.isFinite(new Date(value).getTime())) return "";
  return new Date(value).toISOString();
}
