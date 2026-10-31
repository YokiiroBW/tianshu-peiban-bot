import { useEffect, useState } from "react";
import type { Command, Projection } from "./api";

export const types: Record<string, string> = {
  unspecified: "未指定",
  friend: "朋友",
  partner: "伴侣",
  family: "家人",
  custom: "自定义",
};

export function RelationshipForm({
  projection,
  disabled,
  onSave,
}: {
  projection: Projection;
  disabled: boolean;
  onSave: (command: Command) => Promise<void>;
}) {
  const [kind, setKind] = useState(projection.relationship_type);
  const [label, setLabel] = useState(projection.display_label);
  const [delta, setDelta] = useState("0");
  const [reason, setReason] = useState("");
  function reset() {
    setKind(projection.relationship_type);
    setLabel(projection.display_label);
    setDelta("0");
    setReason("");
  }
  useEffect(reset, [projection.version]);
  return (
    <fieldset className="relationship-form" disabled={disabled}>
      <legend>管理当前关系</legend>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void onSave({
            operation: "set_binding",
            relationship_type: kind,
            display_label: label,
          });
        }}
      >
        <label>
          关系类型
          <select
            value={kind}
            onChange={(event) => setKind(event.target.value)}
          >
            {Object.entries(types).map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        </label>
        <label>
          称呼
          <input
            maxLength={40}
            value={label}
            onChange={(event) => setLabel(event.target.value)}
          />
        </label>
        <button className="button" type="submit">
          保存关系
        </button>
      </form>
      <p>
        冻结只影响这一角色与人物：暂停自动增减和自然衰减。解冻不补扣冻结期间，短期情绪仍正常变化。
      </p>
      <button
        className="button"
        type="button"
        onClick={() =>
          void onSave({ operation: "set_freeze", frozen: !projection.frozen })
        }
      >
        {projection.frozen ? "解冻好感" : "冻结好感"}
      </button>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (Number.isInteger(Number(delta)) && reason.trim())
            void onSave({
              operation: "adjust_affinity",
              delta: Number(delta),
              reason: reason.trim(),
            });
        }}
      >
        <label>
          人工调整值
          <input
            type="number"
            min={-100}
            max={100}
            step={1}
            required
            value={delta}
            onChange={(event) => setDelta(event.target.value)}
          />
        </label>
        <label>
          调整原因
          <textarea
            required
            maxLength={200}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
        <button className="button" type="submit">
          调整好感
        </button>
      </form>
      <button className="button" type="button" onClick={reset}>
        取消修改
      </button>
    </fieldset>
  );
}
