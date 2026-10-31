import { timestamp, word } from "../wording";
import type { Source, SourceObservation, SourceStage } from "./types";

const stages: [SourceStage, string][] = [
  ["connectivity", "入口探测"],
  ["search", "搜索"],
  ["directory", "章节目录"],
  ["preview", "预览"],
  ["download", "完整下载"],
  ["package", "无损封装"],
  ["archive", "AssetLibrary 归档"],
  ["reader", "Komga 阅读"],
];

export function observations(source: Source) {
  const records = { ...source.health };
  if (!records.search && source.search_checked_at) {
    records.search = {
      code: source.code,
      checked_at: source.search_checked_at,
    };
  }
  if (!records.download && source.download_checked_at) {
    records.download = {
      code: source.download_code ?? "comic_source_unchecked",
      checked_at: source.download_checked_at,
    };
  }
  return records;
}

export function needsAttention(source: Source) {
  return (
    source.available === false ||
    Object.values(observations(source)).some(
      (record) => record && record.code !== "ready",
    )
  );
}

function result(stage: SourceStage, observation: SourceObservation) {
  if (observation.code === "comic_search_partial")
    return `${word(observation.code)} · ${observation.count ?? 0} 条匹配`;
  if (observation.code !== "ready") return word(observation.code);
  if (stage === "connectivity") return "入口响应成功，未验证内容功能";
  if (stage === "search" && observation.count !== undefined) {
    const prefix =
      observation.search_mode === "catalogue" ? "目录筛选" : "搜索响应成功";
    return `${prefix} · ${observation.count} 条匹配${observation.returned !== undefined ? `（返回 ${observation.returned} 条）` : ""}`;
  }
  if (stage === "directory" && observation.count !== undefined) {
    return `已读取 ${observation.count} 章`;
  }
  if (stage === "preview") return "样本图片读取成功，不等同完整下载";
  return `${observation.cached ? "已核对已有回执" : "已验证"}${observation.page_count !== undefined ? ` · ${observation.page_count} 页` : ""}`;
}

export function SourceHealth({ source }: { source: Source }) {
  const records = observations(source);
  return (
    <dl className="comic-source-health" aria-label={`${source.name} 验证记录`}>
      {stages
        .filter(([stage]) => stage !== "connectivity" || records.connectivity)
        .map(([stage, title]) => {
          const record = records[stage];
          return (
            <div key={stage} data-stage={stage}>
              <dt>{title}</dt>
              <dd>
                <span>{record ? result(stage, record) : "尚未验证"}</span>
                {record?.library_kind && (
                  <span>
                    {" "}
                    ·{" "}
                    {record.library_kind === "general"
                      ? "漫画目标"
                      : "私藏目标"}
                  </span>
                )}
                {record?.http_status && (
                  <span> · HTTP {record.http_status}</span>
                )}
                {record && <small>{timestamp(record.checked_at)}</small>}
                {record &&
                  Date.now() / 1000 - record.checked_at > 7 * 86400 && (
                    <small>记录已超过 7 天，当前情况需重新验证。</small>
                  )}
                {!!record?.query_errors?.length && (
                  <small>{record.query_errors.map(word).join("；")}</small>
                )}
                {record?.code !== "ready" && record?.last_success_at && (
                  <small>上次成功：{timestamp(record.last_success_at)}</small>
                )}
                {record?.code === "ready" && record.last_failure_at && (
                  <small>
                    最近失败：{word(record.last_failure_code ?? "")} ·{" "}
                    {timestamp(record.last_failure_at)}
                  </small>
                )}
              </dd>
            </div>
          );
        })}
    </dl>
  );
}

export function sourceAdvice(source: Source) {
  const last = Object.values(observations(source))
    .filter((r) => r && r.code !== "ready")
    .sort((a, b) => b!.checked_at - a!.checked_at)[0];
  const code = last?.code;
  if (!code) return "";
  if (code === "comic_source_plugin_incompatible")
    return "需核对插件与 Suwayomi 兼容版本；反复重试下载通常无法解决。";
  if (
    code === "comic_source_format_changed" ||
    code === "comic_source_not_found"
  )
    return "检查插件更新和源地址，恢复后可继续原任务。";
  if (
    [
      "comic_source_login_required",
      "comic_source_verification_required",
      "comic_source_address_required",
    ].includes(code)
  )
    return "在 Suwayomi 打开对应源处理账号、验证或地址设置，再重新搜索或继续原任务。";
  if (code === "comic_source_access_denied")
    return "先在 Suwayomi 核对该源是否能正常访问，再决定重试；403 本身不能确认具体原因。";
  if (
    code.includes("timeout") ||
    code === "comic_source_rate_limited" ||
    code === "comic_source_server_error"
  )
    return "稍后重试；已取得的页面会由原任务继续使用。";
  return "查看失败阶段和时间；处理来源或连接问题后，可从原章节继续任务。";
}
