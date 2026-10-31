import { Drawer } from "../../components/Drawer";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { AccountField, QualityField, TargetField } from "./Fields";
import { RuleEditor } from "./RuleEditor";
import { RuleResults } from "./RuleResults";
import { hasInvalidRules } from "./ruleFields";
import { streamPolicyError, streamPolicySummary } from "./streamPolicy";
import {
  destinationLabel,
  normalizeSubdirectory,
  subdirectoryInputError,
} from "./destination";
import {
  bestQuality,
  emptyRules,
  type DestinationMode,
  type Preview,
  type RulePreview,
  type Rules,
  type Source,
  type Subscription,
} from "./types";
import type { MediaController } from "./useMediaController";
import { useDestinationPreview } from "./useDestinationPreview";
import {
  errorWords,
  destinationModeWords,
  qualityWord,
  sourceWords,
} from "./wording";

type Draft = {
  subscription_id: string | null;
  expected_revision: number;
  label: string;
  source: Source;
  account_id: string;
  target_id: string;
  destination_subdirectory: string;
  destination_mode: DestinationMode;
  initial_sync: "future_only" | "history";
  quality: ReturnType<typeof bestQuality>;
  rules: Rules;
  interval_seconds: number;
};
function draft(item?: Subscription): Draft {
  return item
    ? {
        subscription_id: item.subscription_id,
        expected_revision: item.revision,
        label: item.label,
        source: { ...item.source },
        account_id: item.account_id ?? "",
        target_id: item.target_id,
        destination_subdirectory: item.destination_subdirectory ?? "",
        destination_mode: item.destination_mode ?? "custom",
        initial_sync: item.initial_sync,
        quality: { ...item.quality },
        rules: structuredClone({
          ...item.rules,
          revision: item.rules.revision + 1,
        }),
        interval_seconds: item.interval_seconds,
      }
    : {
        subscription_id: null,
        expected_revision: 0,
        label: "",
        source: { kind: "favorite", id: "" },
        account_id: "",
        target_id: "",
        destination_subdirectory: "",
        destination_mode: "source",
        initial_sync: "future_only",
        quality: bestQuality(),
        rules: emptyRules(),
        interval_seconds: 900,
      };
}

export function SubscriptionEditor({
  item,
  media,
  close,
}: {
  item?: Subscription;
  media: MediaController;
  close: () => void;
}) {
  const [value, setValue] = useState(() => draft(item));
  const [step, setStep] = useState(0);
  const [sample, setSample] = useState("");
  const [testing, setTesting] = useState(false);
  const [results, setResults] = useState<RulePreview | null>(null);
  const version = useRef(0);

  const controller = useRef(media);
  controller.current = media;
  useEffect(() => {
    return () => {
      controller.current.cancel("rule-preview");
      version.current += 1;
    };
  }, []);
  const patch = (next: Partial<Draft>) => {
    version.current += 1;
    media.cancel("rule-preview");
    setTesting(false);
    setResults(null);
    setValue((current) => ({ ...current, ...next }));
  };
  const test = async () => {
    if (!sample.trim()) return;
    version.current += 1;
    const mine = version.current;
    setTesting(true);
    setResults(null);
    media.clearError();
    const preview = await media.request<Preview>("rule-preview", "resolve", {
      url: sample.trim(),
      account_id: value.account_id || null,
    });
    if (mine !== version.current) return;
    if (preview) {
      const response = await media.request<RulePreview>(
        "rule-preview",
        "rules/preview",
        {
          preview_id: preview.preview_id,
          rules: value.rules,
          source: value.source,
        },
      );
      if (mine !== version.current) return;
      if (response) setResults(response);
    }
    setTesting(false);
  };
  const ruleError =
    results?.results.some((result) => result.requires_rule_attention) ?? false;
  const emptyRule = hasInvalidRules(value.rules);
  const qualityError = streamPolicyError(value.quality);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (step < 2) {
      setStep(step + 1);
      return;
    }
    if (
      directoryError ||
      pathPreview.status !== "ready" ||
      accountMissing ||
      emptyRule ||
      qualityError
    )
      return;
    const result = await media.command<{ subscription: Subscription }>(
      "subscriptions/save",
      {
        ...value,
        account_id: value.account_id || null,
        destination_subdirectory:
          value.destination_mode === "source"
            ? (pathPreview.preview?.destination_subdirectory ?? "")
            : normalizeSubdirectory(value.destination_subdirectory),
      },
    );
    if (result) {
      media.setNotice("订阅已保存。完整建立成员基线后，才会处理新发现的视频。");
      close();
    }
  };
  const target = media.view?.targets.find(
    (entry) => entry.target_id === value.target_id,
  );
  const account = media.view?.accounts.find(
    (entry) => entry.account_id === value.account_id,
  );
  const assetManaged = target?.kind === "assetlibrary";
  const sourceDirectoryUnsupported =
    assetManaged || target?.destination_subdirectory_supported === false;
  const watchLater = value.source.kind === "watch_later";
  const accountMissing = watchLater && account?.state !== "ready";
  const directoryError =
    value.destination_mode === "source"
      ? sourceDirectoryUnsupported
        ? "此目标的目录由资产库管理，无法按来源归类。请选择自定义目录并留空。"
        : ""
      : sourceDirectoryUnsupported &&
          normalizeSubdirectory(value.destination_subdirectory)
        ? errorWords.destination_not_supported
        : subdirectoryInputError(value.destination_subdirectory);
  const pathPreview = useDestinationPreview(
    step === 2 &&
      value.label.trim() &&
      value.source.id.trim() &&
      value.target_id &&
      !directoryError &&
      !accountMissing
      ? {
          subscription_id: value.subscription_id,
          label: value.label,
          source: value.source,
          account_id: value.account_id || null,
          target_id: value.target_id,
          destination_mode: value.destination_mode,
          destination_subdirectory:
            value.destination_mode === "custom"
              ? normalizeSubdirectory(value.destination_subdirectory)
              : "",
        }
      : null,
    media,
  );
  const directory = directoryError
    ? value.destination_mode === "custom"
      ? "请修正子目录"
      : "请修正目录设置"
    : accountMissing
      ? "请先选择有效 B 站账号"
      : !target
        ? "请先选择媒体库"
        : pathPreview.status === "pending"
          ? "正在读取整理路径…"
          : pathPreview.status === "failed"
            ? "目录预览未完成，请重试"
            : assetManaged
              ? "由资产库管理"
              : pathPreview.preview
                ? destinationLabel(
                    pathPreview.preview.destination_directory,
                    pathPreview.preview.destination_directory
                      ? ""
                      : pathPreview.preview.destination_subdirectory,
                  )
                : "填写账号与来源后预览目录";
  return (
    <Drawer
      title={item ? `编辑订阅 · ${item.label}` : "新建订阅"}
      variant="card"
      open={true}
      onClose={close}
    >
      {media.error && (
        <p className="media-message media-warning" role="alert">
          {media.error}
        </p>
      )}
      {media.notice && (
        <p className="media-message" role="status">
          {media.notice}
        </p>
      )}
      <section
        className="media-stack media-subscription-editor"
        aria-label={item ? "编辑订阅" : "新建订阅"}
      >
        <ol className="media-steps" aria-label="订阅创建步骤">
          {["账号与来源", "筛选与下载策略", "媒体库与目录"].map(
            (title, index) => (
              <li
                key={title}
                aria-current={step === index ? "step" : undefined}
              >
                <span>{index + 1}</span>
                {title}
              </li>
            ),
          )}
        </ol>
        <form onSubmit={(event) => void submit(event)} className="media-stack">
          {step === 0 && (
            <>
              <div className="media-form-grid">
                <label>
                  订阅名称
                  <input
                    required
                    value={value.label}
                    maxLength={120}
                    onChange={(event) => patch({ label: event.target.value })}
                  />
                </label>
                <AccountField
                  accounts={media.view?.accounts ?? []}
                  value={value.account_id}
                  onChange={(account_id) => patch({ account_id })}
                  required={watchLater}
                  disabled={!!item && watchLater}
                />
              </div>
              <div className="media-form-grid">
                <label>
                  来源类型
                  <select
                    value={value.source.kind}
                    disabled={!!item}
                    onChange={(event) =>
                      patch({
                        source: {
                          kind: event.target.value as Source["kind"],
                          id:
                            event.target.value === "watch_later" ? "self" : "",
                        },
                      })
                    }
                  >
                    {Object.entries(sourceWords)
                      .filter(([kind]) =>
                        media.view?.capabilities.source_kinds.includes(kind),
                      )
                      .map(([kind, title]) => (
                        <option key={kind} value={kind}>
                          {title}
                        </option>
                      ))}
                  </select>
                </label>
                {!watchLater && (
                  <label>
                    来源链接或 ID
                    <input
                      required
                      disabled={!!item}
                      value={value.source.id}
                      maxLength={4096}
                      placeholder={`粘贴${sourceWords[value.source.kind]}的 B 站链接`}
                      onChange={(event) =>
                        patch({
                          source: { ...value.source, id: event.target.value },
                        })
                      }
                    />
                  </label>
                )}
              </div>
              <p className="muted">
                {watchLater
                  ? item
                    ? "此订阅跟随该账号的稍后再看，其他账号可新建订阅。"
                    : "选择有效 B 站账号后，自动读取该账号的稍后再看列表，无需填写来源 ID。"
                  : item
                    ? "来源保持固定；其他来源可新建订阅。"
                    : "粘贴来源页面的 B 站链接即可，也可填写已知来源 ID。保存后会显示识别出的来源标识。"}
              </p>
              {accountMissing && (
                <p className="media-warning" role="status">
                  {item
                    ? "请先在账号与媒体库页更新该账号的 B 站登录。"
                    : "稍后再看需要选择有效 B 站账号。"}
                </p>
              )}
              <fieldset className="media-history">
                <legend>首次同步</legend>
                <label className="media-check">
                  <input
                    type="radio"
                    name="history"
                    checked={value.initial_sync === "future_only"}
                    onChange={() => patch({ initial_sync: "future_only" })}
                  />
                  仅处理后续新增
                </label>
                <label className="media-check">
                  <input
                    type="radio"
                    name="history"
                    checked={value.initial_sync === "history"}
                    onChange={() => patch({ initial_sync: "history" })}
                  />
                  导入来源中的历史视频
                </label>
                <p className="muted">
                  两种模式都会先完整扫描成员。视频发布时间早，不影响它作为新增收藏被发现。
                </p>
              </fieldset>
              <label>
                扫描间隔（分钟）
                <input
                  type="number"
                  min={1}
                  max={10080}
                  step={1}
                  required
                  value={value.interval_seconds / 60}
                  onChange={(event) =>
                    patch({ interval_seconds: Number(event.target.value) * 60 })
                  }
                />
              </label>
            </>
          )}
          {step === 1 && (
            <>
              <RuleEditor
                value={value.rules}
                onChange={(rules) => patch({ rules })}
                disabled={!!media.busy}
              />
              <div className="media-rule-test">
                <label>
                  试算视频链接
                  <input
                    type="url"
                    value={sample}
                    placeholder="粘贴一条视频链接，查看规则命中原因"
                    onChange={(event) => {
                      version.current += 1;
                      media.cancel("rule-preview");
                      setTesting(false);
                      setResults(null);
                      setSample(event.target.value);
                    }}
                  />
                </label>
                <button
                  type="button"
                  className="button"
                  disabled={
                    !sample.trim() || testing || emptyRule || !!media.busy
                  }
                  onClick={() => void test()}
                >
                  {testing ? "正在试算…" : "试算规则"}
                </button>
              </div>
              {results && (
                <RuleResults
                  results={results.results}
                  context={results.context}
                  revision={value.rules.revision}
                />
              )}
              {ruleError && (
                <p role="alert" className="media-warning">
                  规则存在执行错误。请修正后重新试算，不能以未命中继续自动下载。
                </p>
              )}
              <QualityField
                value={value.quality}
                onChange={(quality) => patch({ quality })}
                disabled={!!media.busy}
              />
            </>
          )}
          {step === 2 && (
            <>
              <TargetField
                targets={media.view?.targets ?? []}
                value={value.target_id}
                onChange={(target_id) => patch({ target_id })}
              />
              <label>
                整理方式
                <select
                  value={value.destination_mode}
                  onChange={(event) =>
                    patch({
                      destination_mode: event.target.value as DestinationMode,
                    })
                  }
                  aria-describedby="media-subdirectory-help"
                >
                  <option
                    value="source"
                    disabled={!!target && sourceDirectoryUnsupported}
                  >
                    {destinationModeWords.source}
                  </option>
                  <option value="custom">{destinationModeWords.custom}</option>
                </select>
              </label>
              {value.destination_mode === "custom" && (
                <label>
                  整理子目录（可选）
                  <input
                    value={value.destination_subdirectory}
                    maxLength={1024}
                    placeholder="例如 UP主、收藏夹/学习"
                    aria-describedby="media-subdirectory-help media-subdirectory-preview"
                    aria-invalid={!!directoryError}
                    onChange={(event) =>
                      patch({ destination_subdirectory: event.target.value })
                    }
                  />
                </label>
              )}
              <p className="muted" id="media-subdirectory-help">
                {value.destination_mode === "source"
                  ? "自动归入 UP主、收藏夹、合集或稍后再看；各来源目录使用订阅名称与稳定来源标识。"
                  : assetManaged
                    ? "此目标的目录由资产库管理，整理子目录请留空。切换目标会保留当前输入。"
                    : "相对于所选媒体库的整理根目录，留空使用原根目录。不同订阅可以选择同一媒体库的不同子目录。"}
                {value.destination_mode === "source" &&
                  item &&
                  "修改订阅名称会保留原目录，切换媒体库只更换根目录。"}
                {value.destination_mode === "custom" &&
                  !assetManaged &&
                  item &&
                  "修改目录后，下次扫描会将现有成员整理到新目录；已入队任务仍按原目录继续。"}
              </p>
              <p
                className="media-directory-preview"
                id="media-subdirectory-preview"
              >
                整理路径预览：
                <span>{directory}</span>
              </p>
              {pathPreview.status === "failed" && (
                <button
                  className="button"
                  type="button"
                  onClick={pathPreview.retry}
                >
                  重新预览目录
                </button>
              )}
              {directoryError && (
                <p role="alert" className="media-warning">
                  {directoryError}
                </p>
              )}
              <p className="muted">
                已有下载满足视频、分
                P、画质和高级策略时会优先复用，各目录分别整理；仅调整编码顺序不会重新编码。
              </p>
              <div className="media-summary">
                <h4>保存前预览</h4>
                <dl className="media-facts">
                  <div>
                    <dt>来源</dt>
                    <dd>
                      {sourceWords[value.source.kind]}
                      {!watchLater && ` · ${value.source.id}`}
                    </dd>
                  </div>
                  <div>
                    <dt>账号</dt>
                    <dd>{account?.label ?? "公开访问"}</dd>
                  </div>
                  <div>
                    <dt>首次同步</dt>
                    <dd>
                      {value.initial_sync === "history"
                        ? "导入历史"
                        : "仅后续新增"}
                    </dd>
                  </div>
                  <div>
                    <dt>画质</dt>
                    <dd>
                      {value.quality.mode === "best"
                        ? "实际可用最高画质"
                        : `指定 ${qualityWord(value.quality.quality_id)}`}{" "}
                      · {value.quality.allow_fallback ? "允许降级" : "不降级"}
                      <p className="muted">
                        {streamPolicySummary(value.quality)}
                      </p>
                    </dd>
                  </div>
                  <div>
                    <dt>规则</dt>
                    <dd>
                      黑名单 {value.rules.blacklist.length} 组 / 白名单{" "}
                      {value.rules.whitelist.length} 组
                    </dd>
                  </div>
                  <div>
                    <dt>目标库</dt>
                    <dd>{target?.label ?? "尚未选择"}</dd>
                  </div>
                  <div>
                    <dt>整理方式</dt>
                    <dd>{destinationModeWords[value.destination_mode]}</dd>
                  </div>
                  <div>
                    <dt>整理目录</dt>
                    <dd>{directory}</dd>
                  </div>
                  <div>
                    <dt>媒体服务器</dt>
                    <dd>
                      {target?.servers.length
                        ? target.servers
                            .map((server) => server.label)
                            .join("、")
                        : "未配置，任务仅标记发布完成"}
                    </dd>
                  </div>
                </dl>
              </div>
            </>
          )}
          <div className="media-actions">
            {step > 0 && (
              <button
                type="button"
                className="button"
                onClick={() => setStep(step - 1)}
              >
                上一步
              </button>
            )}
            <button
              type="submit"
              className="button primary"
              disabled={
                !!media.busy ||
                testing ||
                ruleError ||
                accountMissing ||
                (step === 1 && emptyRule) ||
                (step === 1 && !!qualityError) ||
                (step === 2 &&
                  (!!directoryError || pathPreview.status !== "ready"))
              }
            >
              {media.busy === "subscriptions/save"
                ? "正在保存…"
                : step === 2
                  ? "保存订阅"
                  : "下一步"}
            </button>
          </div>
        </form>
      </section>
    </Drawer>
  );
}
