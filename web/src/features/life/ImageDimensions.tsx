export const imageSizePresets = [
  { id: "portrait", label: "竖幅 · 人像", width: 768, height: 1024 },
  { id: "landscape", label: "横幅 · 日常场景", width: 1024, height: 768 },
  { id: "square", label: "方形 · 相册", width: 1024, height: 1024 },
  { id: "wide", label: "宽幅 · 环境", width: 1344, height: 768 },
] as const;

export function ImageDimensions({
  width,
  height,
  onChange,
}: {
  width: number;
  height: number;
  onChange: (width: number, height: number) => void;
}) {
  const preset = imageSizePresets.find(
    (item) => item.width === width && item.height === height,
  );
  return (
    <>
      <label>
        尺寸与方向
        <select
          value={preset?.id ?? "custom"}
          onChange={(event) => {
            const selected = imageSizePresets.find(
              (item) => item.id === event.target.value,
            );
            if (selected) onChange(selected.width, selected.height);
          }}
        >
          {imageSizePresets.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label} · {item.width} × {item.height}
            </option>
          ))}
          <option value="custom">自定义尺寸</option>
        </select>
      </label>
      <div className="life-form-row">
        <label>
          宽度
          <input
            type="number"
            min={64}
            max={4096}
            step={64}
            value={width}
            onChange={(event) => onChange(Number(event.target.value), height)}
          />
        </label>
        <label>
          高度
          <input
            type="number"
            min={64}
            max={4096}
            step={64}
            value={height}
            onChange={(event) => onChange(width, Number(event.target.value))}
          />
        </label>
        <button
          className="button"
          type="button"
          onClick={() => onChange(height, width)}
        >
          交换横竖方向
        </button>
      </div>
    </>
  );
}
