import type { Account, Format, Quality, Target } from "./types";
import { word } from "./wording";
import { StreamOptionsField } from "./StreamOptionsField";
import { videoQualities } from "./streamPolicy";

export function AccountField({
  accounts,
  value,
  onChange,
  disabled,
  required = false,
}: {
  accounts: Account[];
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  required?: boolean;
}) {
  return (
    <label>
      B 站账号
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        required={required}
      >
        <option value="">
          {required ? "请选择有效 B 站账号" : "公开访问（未登录 B 站）"}
        </option>
        {accounts
          .filter((account) => account.state !== "revoked")
          .map((account) => (
            <option
              key={account.account_id}
              value={account.account_id}
              disabled={account.state !== "ready"}
            >
              {account.label} · {word(account.state)}
            </option>
          ))}
      </select>
    </label>
  );
}
export function TargetField({
  targets,
  value,
  onChange,
  disabled,
}: {
  targets: Target[];
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  return (
    <label>
      目标媒体库
      <select
        required
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
      >
        <option value="">请选择已登记媒体库</option>
        {targets.map((target) => (
          <option key={target.target_id} value={target.target_id}>
            {target.label} · {word(target.state)}
          </option>
        ))}
      </select>
    </label>
  );
}
export function QualityField({
  value,
  onChange,
  formats,
  disabled,
}: {
  value: Quality;
  onChange: (value: Quality) => void;
  formats?: Format[];
  disabled?: boolean;
}) {
  return (
    <fieldset className="media-quality" disabled={disabled}>
      <legend>下载策略</legend>
      <label>
        质量策略
        <select
          value={value.mode === "best" ? "best" : (value.quality_id ?? "")}
          onChange={(event) =>
            onChange({
              ...value,
              mode: event.target.value === "best" ? "best" : "exact",
              quality_id:
                event.target.value === "best" ? null : event.target.value,
            })
          }
        >
          <option value="best">当前账号实际可用的最高画质</option>
          {(formats ?? [...videoQualities].reverse()).map((format) => (
            <option key={format.quality_id} value={format.quality_id}>
              {format.label}
            </option>
          ))}
        </select>
      </label>
      <label className="media-check">
        <input
          type="checkbox"
          checked={value.allow_fallback}
          onChange={(event) =>
            onChange({ ...value, allow_fallback: event.target.checked })
          }
        />
        明确允许所选画质不可用时降级
      </label>
      <p className="muted">
        {formats
          ? "这里列出选中分 P 均可取得的画质。"
          : "订阅会在扫描时核对每条视频的实际可用画质。"}
        指定画质默认不降级。
      </p>
      <StreamOptionsField value={value} onChange={onChange} />
    </fieldset>
  );
}
