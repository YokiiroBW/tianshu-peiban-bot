export type GroupMode = "disabled" | "observe" | "reply" | "active";
export type GroupRule = {
  mode: GroupMode;
  actor_id: string | null;
  keywords: string[];
};
export type GroupPolicy = {
  version: 2;
  default: GroupRule;
  groups: Record<string, GroupRule>;
};
export const GROUP_MODE_LABELS: Record<GroupMode, string> = {
  disabled: "停用",
  observe: "仅观察",
  reply: "启用回复",
  active: "主动",
};

export function parseGroupKeywords(value: string) {
  return [...new Set(value.split(/\r?\n/).filter((word) => word.trim()))];
}

export function GroupRuleEditor({
  name,
  value,
  actors,
  disabled,
  onChange,
}: {
  name: string;
  value: GroupRule;
  actors: { id: string; label: string }[];
  disabled: boolean;
  onChange: (rule: GroupRule) => void;
}) {
  return (
    <fieldset className="bot-form group-rule-editor" disabled={disabled}>
      <legend>{name}</legend>
      <label>
        状态
        <select
          aria-label={`${name}状态`}
          value={value.mode}
          onChange={(e) =>
            onChange({ ...value, mode: e.target.value as GroupMode })
          }
        >
          {Object.entries(GROUP_MODE_LABELS).map(([mode, label]) => (
            <option key={mode} value={mode}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <label>
        群角色
        <select
          aria-label={`${name}角色`}
          value={value.actor_id ?? ""}
          onChange={(e) =>
            onChange({ ...value, actor_id: e.target.value || null })
          }
        >
          <option value="">请选择负责此群的角色</option>
          {value.actor_id &&
            !actors.some((actor) => actor.id === value.actor_id) && (
              <option value={value.actor_id}>原角色当前不可用</option>
            )}
          {actors.map((actor) => (
            <option key={actor.id} value={actor.id}>
              {actor.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        回复关键词
        <textarea
          aria-label={`${name}关键词`}
          rows={2}
          value={value.keywords.join("\n")}
          onChange={(e) =>
            onChange({
              ...value,
              keywords: parseGroupKeywords(e.target.value),
            })
          }
          placeholder="每行一个原文关键词，不使用正则"
        />
      </label>
    </fieldset>
  );
}
