import { DetailCard } from "../../components/DetailCard";
import { useId } from "react";
import type { Quality, StreamOptions } from "./types";
import {
  audioQualities,
  codecWords,
  defaultStreamOptions,
  exactOutsidePolicy,
  streamPolicyError,
  streamPolicySummary,
  videoQualities,
  withStreamOptions,
} from "./streamPolicy";

export function StreamOptionsField({
  value,
  onChange,
}: {
  value: Quality;
  onChange: (quality: Quality) => void;
}) {
  const options = { ...defaultStreamOptions(), ...value.stream_options };
  const error = streamPolicyError(value);
  const errorId = useId();
  const patch = (next: Partial<StreamOptions>) =>
    onChange(withStreamOptions(value, { ...options, ...next }));
  const move = (index: number, direction: number) => {
    const codecs = [...options.codecs];
    [codecs[index], codecs[index + direction]] = [
      codecs[index + direction],
      codecs[index],
    ];
    patch({ codecs });
  };
  return (
    <DetailCard
      title="高级音视频策略"
      label={
        <>
          高级音视频策略
          <span className="media-policy-summary">
            {streamPolicySummary(value)}
          </span>
        </>
      }
      className="media-stream-options"
    >
      <p className="muted">
        在允许范围内选择最高画质，同画质再按编码顺序选择；音频选择允许的最高档位。这些限制不会增加账号可取得的画质。
      </p>
      <div className="media-form-grid">
        {(["video", "audio"] as const).map((kind) => (
          <fieldset key={kind} className="media-stack">
            <legend>
              {kind === "video" ? "视频质量范围" : "音频质量范围"}
            </legend>
            {(["min", "max"] as const).map((bound) => {
              const key = `${kind}_${bound}_quality` as const;
              return (
                <label key={key}>
                  {bound === "min" ? "最低" : "最高"}
                  {kind === "video" ? "视频" : "音频"}质量
                  <select
                    value={options[key] ?? ""}
                    aria-invalid={!!error || undefined}
                    aria-describedby={error ? errorId : undefined}
                    onChange={(event) =>
                      patch({ [key]: event.target.value || null })
                    }
                  >
                    <option value="">不限</option>
                    {(kind === "video" ? videoQualities : audioQualities).map(
                      (item) => (
                        <option key={item.quality_id} value={item.quality_id}>
                          {item.label}
                        </option>
                      ),
                    )}
                  </select>
                </label>
              );
            })}
          </fieldset>
        ))}
      </div>
      {error && (
        <p role="alert" className="media-warning" id={errorId}>
          {error}
        </p>
      )}
      <fieldset className="media-stack">
        <legend>允许的视频编码与优先顺序</legend>
        <p className="muted">
          空列表为自动选择；添加后仅允许列表中的编码。可用上移、下移调整同画质下的优先顺序。符合允许集合和其他条件的已有文件会优先复用；调整同一集合的顺序不会重新编码。
        </p>
        <ol className="media-codec-list" aria-label="编码优先顺序">
          {options.codecs.map((codec, index) => (
            <li key={codec}>
              <span>
                {index + 1}. {codecWords[codec]}
              </span>
              <div className="media-actions">
                <button
                  type="button"
                  className="button"
                  aria-label={`上移 ${codecWords[codec]}`}
                  disabled={index === 0}
                  onClick={() => move(index, -1)}
                >
                  上移
                </button>
                <button
                  type="button"
                  className="button"
                  aria-label={`下移 ${codecWords[codec]}`}
                  disabled={index === options.codecs.length - 1}
                  onClick={() => move(index, 1)}
                >
                  下移
                </button>
                <button
                  type="button"
                  className="button"
                  aria-label={`移除 ${codecWords[codec]}`}
                  onClick={() =>
                    patch({
                      codecs: options.codecs.filter((item) => item !== codec),
                    })
                  }
                >
                  移除
                </button>
              </div>
            </li>
          ))}
        </ol>
        <div className="media-actions">
          {(Object.keys(codecWords) as (keyof typeof codecWords)[])
            .filter((codec) => !options.codecs.includes(codec))
            .map((codec) => (
              <button
                type="button"
                className="button"
                key={codec}
                onClick={() => patch({ codecs: [...options.codecs, codec] })}
              >
                添加 {codecWords[codec]}
              </button>
            ))}
        </div>
      </fieldset>
      <fieldset className="media-form-grid">
        <legend>特殊流</legend>
        {(
          [
            ["allow_hdr", "允许 HDR 视频"],
            ["allow_dolby_vision", "允许杜比视界视频"],
            ["allow_dolby_audio", "允许杜比音频"],
            ["allow_hires", "允许 Hi-Res 音频"],
          ] as const
        ).map(([key, label]) => (
          <label className="media-check" key={key}>
            <input
              type="checkbox"
              checked={options[key]}
              onChange={(event) => patch({ [key]: event.target.checked })}
            />
            {label}
          </label>
        ))}
      </fieldset>
      <p className="muted">
        允许降级仍受上述范围、编码和特殊流限制；没有符合条件的流时会说明原因。容器由实际音视频格式决定。
      </p>
      {exactOutsidePolicy(value) && (
        <p className="media-warning" role="status">
          指定画质在当前允许策略之外。保持不降级时将没有符合条件的流；开启降级后只在策略允许的流中选择。
        </p>
      )}
      <button
        type="button"
        className="button"
        onClick={() =>
          onChange(withStreamOptions(value, defaultStreamOptions()))
        }
      >
        恢复高级策略默认值
      </button>
    </DetailCard>
  );
}
