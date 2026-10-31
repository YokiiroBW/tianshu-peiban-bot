import { useId } from "react";
import type { JsonSchema } from "./types";

function initial(schema: JsonSchema): unknown {
  if (schema.default !== undefined) return structuredClone(schema.default);
  if (schema.type === "object") return {};
  if (schema.type === "array") return [];
  if (schema.type === "boolean") return false;
  return "";
}

function Field({
  schema,
  value,
  onChange,
  name,
  required = false,
}: {
  schema: JsonSchema;
  value: unknown;
  onChange: (value: unknown) => void;
  name: string;
  required?: boolean;
}) {
  const id = useId();
  const title = schema.title || name;
  if (schema.type === "object")
    return (
      <fieldset className="plugins-fields">
        <legend>{title}</legend>
        <SchemaFields
          schema={schema}
          value={(value ?? {}) as Record<string, unknown>}
          onChange={onChange}
        />
      </fieldset>
    );
  if (schema.type === "array")
    return (
      <fieldset className="plugins-fields">
        <legend>{title}</legend>
        {((value ?? []) as unknown[]).map((item, index) => (
          <div className="plugins-array" key={index}>
            <Field
              schema={schema.items ?? { type: "string" }}
              name={`${name} ${index + 1}`}
              value={item}
              onChange={(next) => {
                const items = [...(value as unknown[])];
                items[index] = next;
                onChange(items);
              }}
            />
            <button
              type="button"
              onClick={() =>
                onChange((value as unknown[]).filter((_, at) => at !== index))
              }
            >
              移除
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() =>
            onChange([
              ...((value as unknown[]) ?? []),
              initial(schema.items ?? {}),
            ])
          }
        >
          添加一项
        </button>
      </fieldset>
    );
  if (schema.type === "boolean")
    return (
      <label className="plugins-check" htmlFor={id}>
        <input
          id={id}
          type="checkbox"
          checked={Boolean(value)}
          onChange={(event) => onChange(event.target.checked)}
        />
        {title}
      </label>
    );
  const text = value === undefined || value === null ? "" : String(value);
  const numeric = schema.type === "number" || schema.type === "integer";
  return (
    <label className="plugins-field" htmlFor={id}>
      <span>
        {title}
        {required ? " *" : ""}
      </span>
      {schema.enum ? (
        <select
          id={id}
          value={value === undefined ? "" : JSON.stringify(value)}
          required={required}
          onChange={(event) =>
            onChange(
              event.target.value ? JSON.parse(event.target.value) : undefined,
            )
          }
        >
          <option value="">请选择</option>
          {schema.enum.map((option) => (
            <option key={JSON.stringify(option)} value={JSON.stringify(option)}>
              {String(option)}
            </option>
          ))}
        </select>
      ) : schema["x-ui"]?.multiline ? (
        <textarea
          id={id}
          value={text}
          required={required}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : (
        <input
          id={id}
          type={numeric ? "number" : "text"}
          value={text}
          required={required}
          min={schema.minimum}
          max={schema.maximum}
          minLength={schema.minLength}
          maxLength={schema.maxLength}
          step={schema.type === "integer" ? 1 : "any"}
          onChange={(event) =>
            onChange(
              numeric
                ? event.target.value === ""
                  ? undefined
                  : Number(event.target.value)
                : event.target.value || (required ? "" : undefined),
            )
          }
        />
      )}
      {schema.description && <small>{schema.description}</small>}
    </label>
  );
}

export function SchemaFields({
  schema,
  value,
  onChange,
}: {
  schema: JsonSchema;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
}) {
  return (
    <>
      {Object.entries(schema.properties ?? {})
        .filter(([, item]) => !item["x-secret"])
        .map(([key, item]) => (
          <Field
            key={key}
            name={key}
            schema={item}
            value={value[key] ?? item.default}
            required={schema.required?.includes(key)}
            onChange={(next) => {
              const result = { ...value };
              if (next === undefined) delete result[key];
              else result[key] = next;
              onChange(result);
            }}
          />
        ))}
    </>
  );
}
