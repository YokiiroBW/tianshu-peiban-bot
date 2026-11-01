import { useState } from "react";
import { currentMemory, type MemoryTimeQuery } from "./types";
import "./memory.css";

export function useMemoryTime() {
  return useState<MemoryTimeQuery>(currentMemory);
}

export function MemoryTimeFilter({
  value,
  onChange,
}: {
  value: MemoryTimeQuery;
  onChange: (query: MemoryTimeQuery) => void;
}) {
  const [mode, setMode] = useState(value.time_mode);
  const [validAt, setValidAt] = useState("");
  const [knownAt, setKnownAt] = useState("");
  return (
    <form
      className="memory-time-filter"
      onSubmit={(event) => {
        event.preventDefault();
        onChange({
          time_mode: mode,
          ...(mode !== "current" && validAt
            ? { valid_at: new Date(validAt).toISOString() }
            : {}),
          ...(mode === "known_at" && knownAt
            ? { known_at: new Date(knownAt).toISOString() }
            : {}),
        });
      }}
    >
      <label>
        时间查询
        <select
          value={mode}
          onChange={(event) =>
            setMode(event.target.value as MemoryTimeQuery["time_mode"])
          }
        >
          <option value="current">当前可用认识</option>
          <option value="valid_at">过去何时适用（按目前已纠正的认识）</option>
          <option value="known_at">当时已经知道什么</option>
        </select>
      </label>
      {mode !== "current" && (
        <label>
          {mode === "valid_at" ? "适用时点" : "适用时点（可选）"}
          <input
            type="datetime-local"
            required={mode === "valid_at"}
            value={validAt}
            onChange={(event) => setValidAt(event.target.value)}
          />
        </label>
      )}
      {mode === "known_at" && (
        <label>
          已知截止时点
          <input
            type="datetime-local"
            required
            value={knownAt}
            onChange={(event) => setKnownAt(event.target.value)}
          />
        </label>
      )}
      <button className="button">按时间读取</button>
      <p className="muted">
        时间按本机时区输入。未知的有效时间或首次记录时间保持未知；历史读取仍使用当前访问权限。
      </p>
      {value.time_mode !== "current" && (
        <p role="status">
          正在查看历史认识。更正会先读取当前版本，再保存到当前记录。
        </p>
      )}
    </form>
  );
}
