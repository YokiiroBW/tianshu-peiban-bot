import type { Projection } from "./api";
import { types } from "./api";
export function RelationshipForm({
  value,
  busy,
  save,
  close,
}: {
  value: Projection;
  busy: boolean;
  save: (body: object, revision: number) => Promise<unknown>;
  close: () => void;
}) {
  return (
    <form
      className="domain-form relationship-form"
      onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        const body: Record<string, unknown> = {
          relationship_type: data.get("relationship_type"),
          display_label: data.get("display_label"),
          summary: data.get("summary"),
          frozen: data.get("frozen") === "on",
        };
        for (const field of [
          "boundaries",
          "preferences",
          "shared_experiences",
        ] as const) {
          const original = value[field].map((item) => item.content).join("\n");
          const entered = String(data.get(field) ?? "");
          if (entered !== original)
            body[field] = entered
              .split("\n")
              .map((line) => line.trim())
              .filter(Boolean);
        }
        void save(body, value.revision).then((result) => {
          if (result) close();
        });
      }}
    >
      <label>
        关系类型
        <select name="relationship_type" defaultValue={value.relationship_type}>
          {Object.entries(types).map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <label>
        称呼
        <input
          name="display_label"
          defaultValue={value.display_label}
          required
          maxLength={256}
        />
      </label>
      <label>
        相处背景
        <textarea
          name="summary"
          defaultValue={value.summary}
          maxLength={10000}
          rows={4}
        />
      </label>
      {(
        [
          ["boundaries", "边界"],
          ["preferences", "相处偏好"],
          ["shared_experiences", "共同经历"],
        ] as const
      ).map(([field, label]) => (
        <label key={field}>
          {label}（每行一项）
          <textarea
            name={field}
            defaultValue={value[field].map((item) => item.content).join("\n")}
            rows={5}
          />
        </label>
      ))}
      <label className="inline-check">
        <input type="checkbox" name="frozen" defaultChecked={value.frozen} />
        冻结自动演变，继续保留实际经历与主观手记
      </label>
      <div className="domain-actions">
        <button className="button primary" disabled={busy}>
          保存明确修订
        </button>
        <button className="button" type="button" onClick={close}>
          收起编辑
        </button>
      </div>
    </form>
  );
}
