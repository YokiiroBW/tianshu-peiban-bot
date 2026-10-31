import { Plus, Trash2 } from "lucide-react";
import type { Group, Rule, Rules } from "./types";
import { requestId } from "../../app/requestId";
import {
  dateTimeValue,
  fieldDefinition,
  fieldOperations,
  localDateTime,
  ruleFields,
  ruleInputError,
  ruleOperations,
} from "./ruleFields";

const id = () => "r_" + requestId().replaceAll("-", "");
const newRule = (): Rule => ({
  id: id(),
  field: "title",
  op: "contains",
  value: "",
  case_sensitive: false,
});

function RuleValue({
  rule,
  change,
}: {
  rule: Rule;
  change: (rule: Rule) => void;
}) {
  const kind = fieldDefinition(rule.field).kind;
  const error = ruleInputError(rule);
  const errorId = rule.id + "-error";
  const values = Array.isArray(rule.value) ? rule.value : [rule.value];
  const setValue = (value: string, index: number) => {
    const next =
      kind === "date"
        ? dateTimeValue(value)
        : kind === "number" && value !== ""
          ? Number(value)
          : value;
    change({
      ...rule,
      value:
        rule.op === "between"
          ? index === 0
            ? [next, (values[1] as string | number) ?? ""]
            : [values[0] as string | number, next]
          : next,
    });
  };
  return (
    <div className="media-rule-value">
      {kind === "boolean" ? (
        <label>
          是否联合投稿
          <select
            value={String(rule.value)}
            onChange={(event) =>
              change({ ...rule, value: event.target.value === "true" })
            }
          >
            <option value="true">是</option>
            <option value="false">否</option>
          </select>
        </label>
      ) : (
        <div className={rule.op === "between" ? "media-rule-range" : undefined}>
          {(rule.op === "between" ? [0, 1] : [0]).map((index) => (
            <label key={index}>
              {rule.op === "between"
                ? index === 0
                  ? "范围起点"
                  : "范围终点"
                : "匹配值"}
              <input
                type={
                  kind === "date"
                    ? "datetime-local"
                    : kind === "number"
                      ? "number"
                      : "text"
                }
                value={
                  kind === "date"
                    ? localDateTime(values[index])
                    : typeof values[index] === "boolean"
                      ? ""
                      : (values[index] ?? "")
                }
                min={kind === "number" ? 0 : undefined}
                step={
                  kind === "date"
                    ? "0.001"
                    : kind === "number"
                      ? rule.field === "part_count"
                        ? 1
                        : "any"
                      : undefined
                }
                aria-invalid={!!error || undefined}
                aria-describedby={error ? errorId : undefined}
                required
                onChange={(event) => setValue(event.target.value, index)}
              />
            </label>
          ))}
        </div>
      )}
      {kind === "text" && (
        <small className="muted">
          {Array.from(String(rule.value)).length} /{" "}
          {rule.op === "regex" ? 512 : 4096} 字符
        </small>
      )}
      {kind === "date" && (
        <small className="muted">
          时区：{Intl.DateTimeFormat().resolvedOptions().timeZone}
          ，保存实际时刻。
        </small>
      )}
      {rule.field === "duration_seconds" && (
        <small className="muted">全部分 P 的总时长，单位为秒。</small>
      )}
      {rule.field === "favorited_at" && (
        <small className="muted">
          仅使用所选收藏夹实际提供的收藏时间；其他来源或未核实成员时为未知。
        </small>
      )}
      {error && (
        <small className="media-warning" id={errorId}>
          {error}
        </small>
      )}
    </div>
  );
}

export function RuleEditor({
  value,
  onChange,
  disabled = false,
}: {
  value: Rules;
  onChange: (rules: Rules) => void;
  disabled?: boolean;
}) {
  const set = (kind: "whitelist" | "blacklist", groups: Group[]) =>
    onChange({ ...value, [kind]: groups });
  const change = (
    kind: "whitelist" | "blacklist",
    index: number,
    rules: Rule[],
  ) =>
    set(
      kind,
      value[kind].map((group, position) =>
        position === index ? { ...group, rules } : group,
      ),
    );
  return (
    <div className="media-rules">
      <h4>筛选规则</h4>
      <p className="muted">
        黑名单优先；同一组内全部满足（AND），任一组满足即可（OR）。白名单为空时不设门槛。字段未知时不会命中，取反（NOT）也不会将未知视为匹配。
      </p>
      {(["blacklist", "whitelist"] as const).map((kind) => (
        <section
          key={kind}
          className="media-rule-list"
          aria-label={kind === "blacklist" ? "黑名单" : "白名单"}
        >
          <div className="media-section-head">
            <h4>
              {kind === "blacklist"
                ? "黑名单 · 命中即跳过"
                : "白名单 · 命中才下载"}
            </h4>
            <button
              type="button"
              className="button"
              disabled={
                disabled ||
                value.blacklist.length + value.whitelist.length >= 20
              }
              onClick={() =>
                set(kind, [...value[kind], { id: id(), rules: [newRule()] }])
              }
            >
              <Plus aria-hidden="true" />
              添加{kind === "blacklist" ? "黑名单" : "白名单"}组
            </button>
          </div>
          {!value[kind].length ? (
            <p className="muted">
              {kind === "blacklist" ? "没有黑名单规则。" : "没有白名单限制。"}
            </p>
          ) : (
            value[kind].map((group, index) => (
              <fieldset
                className="media-rule-group"
                key={group.id}
                disabled={disabled}
              >
                <legend>
                  第 {index + 1} 组{index > 0 ? "（与上一组为 OR）" : ""}
                </legend>
                {group.rules.map((rule, position) => {
                  const update = (next: Rule) =>
                    change(
                      kind,
                      index,
                      group.rules.map((item) =>
                        item.id === rule.id ? next : item,
                      ),
                    );
                  return (
                    <div
                      className={`media-rule-row${fieldDefinition(rule.field).kind === "date" ? " has-date" : ""}`}
                      key={rule.id}
                    >
                      <label>
                        字段
                        <select
                          value={rule.field}
                          onChange={(event) => {
                            const field = event.target.value as Rule["field"];
                            update({
                              ...rule,
                              field,
                              op: fieldOperations(field)[0],
                              value:
                                fieldDefinition(field).kind === "boolean"
                                  ? false
                                  : "",
                            });
                          }}
                        >
                          {ruleFields.map((field) => (
                            <option key={field.value} value={field.value}>
                              {field.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        条件
                        <select
                          value={rule.op}
                          onChange={(event) => {
                            const op = event.target.value as Rule["op"];
                            update({
                              ...rule,
                              op,
                              value:
                                op === "between"
                                  ? [
                                      Array.isArray(rule.value)
                                        ? rule.value[0]
                                        : typeof rule.value === "boolean"
                                          ? ""
                                          : rule.value,
                                      "",
                                    ]
                                  : Array.isArray(rule.value)
                                    ? rule.value[0]
                                    : rule.value,
                            });
                          }}
                        >
                          {fieldOperations(rule.field).map((op) => (
                            <option key={op} value={op}>
                              {ruleOperations[op]}
                            </option>
                          ))}
                        </select>
                      </label>
                      <RuleValue rule={rule} change={update} />
                      <div className="media-rule-switches">
                        {fieldDefinition(rule.field).kind === "text" && (
                          <label className="media-check">
                            <input
                              type="checkbox"
                              checked={rule.case_sensitive}
                              onChange={(event) =>
                                update({
                                  ...rule,
                                  case_sensitive: event.target.checked,
                                })
                              }
                            />
                            区分大小写
                          </label>
                        )}
                        <label className="media-check">
                          <input
                            type="checkbox"
                            checked={rule.negate ?? false}
                            onChange={(event) =>
                              update({ ...rule, negate: event.target.checked })
                            }
                          />
                          取反（NOT）
                        </label>
                      </div>
                      <button
                        type="button"
                        className="icon-button"
                        aria-label={`删除${kind === "blacklist" ? "黑名单" : "白名单"}第${index + 1}组第${position + 1}条规则`}
                        disabled={group.rules.length === 1}
                        onClick={() =>
                          change(
                            kind,
                            index,
                            group.rules.filter((item) => item.id !== rule.id),
                          )
                        }
                      >
                        <Trash2 aria-hidden="true" />
                      </button>
                    </div>
                  );
                })}
                <div className="media-actions">
                  <button
                    type="button"
                    className="button"
                    disabled={group.rules.length >= 10}
                    onClick={() =>
                      change(kind, index, [...group.rules, newRule()])
                    }
                  >
                    添加 AND 条件
                  </button>
                  <button
                    type="button"
                    className="button"
                    onClick={() =>
                      set(
                        kind,
                        value[kind].filter((item) => item.id !== group.id),
                      )
                    }
                  >
                    删除此组
                  </button>
                </div>
              </fieldset>
            ))
          )}
        </section>
      ))}
    </div>
  );
}
