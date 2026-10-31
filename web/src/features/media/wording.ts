import { videoQualities } from "./streamPolicy";
import { bookWords } from "./books/wording";
import { comicWords } from "./comics/wording";

export const storageReasonWords: Record<string, string> = {
  storage_scanning: "正在后台统计分类占用。",
  storage_subscription_unavailable: "订阅媒体目录暂无法完整统计。",
  storage_emby_unavailable: "Emby 媒体库暂不可读，容量尚未完整统计。",
  storage_path_unmapped: "部分 Emby 媒体目录尚未映射，容量暂未知。",
  storage_path_unavailable: "部分媒体目录暂不可读，容量尚未完整统计。",
  storage_measurement_inconsistent:
    "分类统计与磁盘容量不一致，暂不展示分类比例。",
  storage_capacity_unavailable: "暂未取得磁盘容量。",
  storage_logical_estimate: "按文件逻辑大小估算，分类占用为近似值。",
};

export const stateWords: Record<string, string> = {
  queued: "等待下载",
  downloading: "正在下载",
  validating: "校验成品",
  decoding: "自动解锁音频",
  metadata_ready: "元数据已就绪",
  metadata: "检查元数据",
  publishing: "正在发布",
  asset_indexed: "媒体已入库",
  library_verifying: "媒体服务器核对中",
  completed: "核对完成",
  published: "发布完成",
  auth_required: "需要重新登录账号",
  waiting_metadata: "等待补全元数据",
  retry_wait: "等待重试",
  cancelled: "已取消",
  failed: "失败",
  unknown: "结果未知",
  active: "订阅运行中",
  paused: "已暂停",
  rule_error: "规则需要修正",
  library_files_verified: "媒体服务器已收录文件",
  ready: "可用",
  revoked: "已撤销",
  not_configured: "未配置",
  available: "可用",
  unavailable: "暂不可用",
  waiting: "等待扫码",
  scanned: "已扫码，请在手机确认",
  expired: "二维码已过期",
};
export const sourceWords: Record<string, string> = {
  favorite: "收藏夹",
  collection: "合集",
  series: "系列",
  uploader: "UP 主投稿",
  watch_later: "稍后再看",
};
export const destinationModeWords = {
  source: "按来源自动归类",
  custom: "自定义目录",
};
export const reasonWords: Record<string, string> = {
  media_worker_failed: "此任务遇到内部错误，其他下载继续运行；可重试此任务。",
  media_background_restarting: "后台任务异常退出，正在自动恢复。",
  music_account_restored: "登录已恢复，已继续原下载任务。",
  music_request_deferred: "等待共享请求时段",
  music_request_budget: "本时段请求预算已用完，稍后自动继续",
  music_account_cooling: "平台请求冷却中，稍后自动继续",
  music_quality_check_queued: "等待音质巡检",
  music_quality_check_failed: "本轮音质巡检未完成，保留已归档文件",
  music_quality_check_partial: "部分渠道暂不可用，将择日复查",
  music_quality_review_paused: "音质巡检已暂停，已归档文件保留",
  music_archived: "音乐及元数据已归档",
  music_indexed: "本地曲库已登记",
  music_file_unreadable:
    "天枢没有读取此文件的权限，原文件已保留；修复目录权限后重新扫描。",
  music_match_unavailable: "其他已连接平台尚未找到可确认的同版本歌曲。",
  music_match_ambiguous: "找到多个版本，暂未自动替换；可检查歌名、专辑和时长。",
  music_unlocking: "正在自动解锁音频，完成后校验、写标签并归档。",
  music_comparing: "正在聚合查询各平台并比较最高可用音质。",
  music_decode_failed:
    "解锁未成功，可能是文件不完整或加密版本无法识别。原件已保留。",
  music_decode_key_required:
    "此加密版本需要官方客户端的文件密钥，当前无法解锁。原件已保留。",
  music_decode_unsupported: "当前解锁工具尚不支持此加密版本。原件已保留。",
  music_tool_unavailable: "音乐工具不可用，请检查服务安装。",
  music_tool_failed: "音乐工具暂时未能完成请求。",
  music_download_forbidden:
    "已连接的账号均未取得此歌曲的完整下载权限，请检查购买或会员权益。",
  music_preview_rejected: "上游返回试听片段或时长不符，未作为完整歌曲归档。",
  music_quality_unavailable:
    "当前音源未达到所选音质，可修改降级设置后重新下载。",
  music_quality_unknown: "上游未提供可核验的音质信息。",
  music_quality_mismatch: "实际音频编码与上游声明的音质不符。",
  invalid_music_quality: "请选择有效的音乐音质。",
  music_duration_unknown: "缺少完整歌曲时长，无法核验下载结果。",
  music_download_unavailable: "音源暂不可下载，稍后重试。",
  music_download_url_invalid: "音源返回的下载地址不符合所选平台来源。",
  music_download_incomplete: "下载大小或摘要不匹配，请重试。",
  music_audio_invalid: "音频不完整或无法正常解码。",
  music_format_unsupported: "当前音频格式暂不支持标签整理。",
  music_file_too_large: "此歌曲超过音频下载大小限制。",
  music_tag_failed: "歌曲标签写入失败，尚未归档。",
  music_album_unavailable: "专辑补充信息未取得，已保留歌曲自带资料。",
  music_cover_unavailable: "封面未取得，音频已保留。",
  music_lyrics_unavailable: "歌词未取得，音频已保留。",
  music_target_conflict: "归档路径被其他文件占用，原文件已保留。",
  music_staging_changed: "临时音频发生变化，需要重新下载。",
  music_publication_unverified: "归档文件尚未核验成功。",
  music_atomic_publish_unavailable:
    "目标文件系统未能完成文件归档，请检查目录写入与硬链接能力。",
  eligible: "符合规则，可以下载",
  inaccessible: "当前账号无法访问",
  quality_satisfied: "已有成品满足目标质量",
  blacklist_match: "命中黑名单，跳过",
  whitelist_no_match: "未命中白名单，跳过",
  regex_timeout: "正则执行超时，请修正规则",
  regex_worker_failed: "正则试算暂不可用",
  evaluation_timeout: "试算超时",
  busy: "试算服务繁忙",
  input_too_large: "来源字段超出试算预算",
  matched: "命中",
  not_matched: "未命中",
  field_missing: "字段值未知，取反也不会视为命中",
  not_evaluated: "未执行",
  error: "出错",
  missing_title: "需要补全视频标题",
  missing_description: "需要补全视频简介",
  missing_cover: "尚未取得来源封面",
  connection_failed: "媒体服务器连接失败，请稍后重试",
  scan_complete: "来源成员已完整扫描",
  published_no_media_servers: "发布完成，目标库未配置媒体服务器",
  package_ready: "元数据和成品已准备完成",
  metadata_incomplete: "元数据尚未完整，请补全资料或重新解析来源",
  rule_short_circuit: "组内前面的规则已决定结果，本条无需继续执行",
  priority_short_circuit: "前面的规则组已决定结果，本组无需继续执行",
  blacklist_matched: "黑名单已命中",
  whitelist_matched: "白名单已命中",
  error_short_circuit: "前面的规则执行失败，后续规则未执行",
};
export const errorWords: Record<string, string> = {
  ...bookWords,
  ...comicWords,
  music_request_deferred: reasonWords.music_request_deferred,
  music_request_budget: reasonWords.music_request_budget,
  music_account_cooling: reasonWords.music_account_cooling,
  invalid_destination_subdirectory:
    "请检查整理子目录：使用相对目录，不含空层级、.、..、保留文件名或不兼容字符。",
  destination_not_supported:
    "这个目标的目录由资产库管理，整理子目录请留空，或选择媒体服务器目标。",
  directory_overlap: "整理目录不能与暂存目录或其他目标重叠",
  target_mapping_in_use:
    "已有任务使用此目录映射；更换目录或库请添加新的连接，原连接可继续更新密钥",
  target_managed_by_config: "此连接通过部署配置维护",

  unsafe_library_path: "媒体库目录包含链接或不安全路径，请检查目录配置。",
  source_changed: "成品文件已变化，请重新校验后重试。",
  target_exists: "目标目录已有不同文件，已保留原文件，请检查后重试。",
  scope_changed: "媒体库配置已变化，请检查任务对应的目标库。",
  budget_exceeded: "请求超过媒体接口的 6 MiB 上限，请缩减本次输入。",
  media_not_configured: "此部署尚未登记视频订阅服务。",
  media_disabled: "此部署尚未启用视频订阅服务。",
  unauthorized: "网页登录已失效，请重新登录。",
  session_expired: "网页登录已过期，请重新登录。",
  forbidden: "本次操作未通过会话安全检查，请刷新账号状态。",
  operator_not_authorized: "当前账号没有视频订阅管理权限。",
  auth_required: "B 站会话已失效，请在账号页重新登录。",
  account_not_found: "这个 B 站账号已不存在，请重新选择。",
  subscription_account_immutable:
    "此订阅跟随该账号的稍后再看，其他账号可新建订阅。",
  invalid_input: "请检查填写的链接、来源和规则。",
  invalid_url: "请输入支持的 B 站视频链接。",
  unsupported_source: "这个来源类型目前不可用。",
  preview_expired: "解析预览已过期，请重新解析链接。",
  preview_not_found: "解析预览已失效，请重新解析链接。",
  quality_unavailable: "所选画质目前不可取得，请调整画质或重新登录。",
  quality_unverified:
    "暂时无法核实成品是否满足音视频策略，请读取任务状态后重试。",
  quality_mismatch: "实际音视频流不符合所选策略，请检查任务详情并调整策略。",
  preview_account_conflict:
    "试算账号与订阅登记账号不同，请使用相同的 B 站账号重新解析。",
  source_budget_exceeded: "来源成员扫描超时或超出范围，请稍后重新试算。",
  target_unavailable: "目标媒体库暂不可用，请检查账号与媒体库。",
  revision_conflict: "记录已被其他操作更新，请重新读取后修改。",
  job_not_deletable:
    "任务仍在执行，暂时不能删除记录。请等待任务结束后重新读取。",
  version_conflict: "记录版本已改变，请重新读取后修改。",
  idempotency_conflict: "这次请求与原操作不一致，请重新读取后操作。",
  dependency_unavailable: "后台或上游暂不可用，请稍后重试。",
  engine_unavailable: "下载引擎暂不可用。",
  cursor_conflict: "任务筛选已改变，请重新读取任务列表。",
  qr_expired: "二维码已过期，请重新生成。",
  qr_poll_too_fast: "扫码状态仍在等待更新，请稍后查看。",
  layout_change_requires_migration:
    "这个视频已按单视频发布。订阅需要分 P 剧集目录，请选择独立整理子目录或媒体库。",
  rule_error: "规则试算失败，请修正规则后启用。",
  rule_invalid: "规则格式或正则表达式不合法，请检查规则。",
  too_many_requests: "尝试过于频繁，请稍后重试。",
  not_found: "找不到这条记录，请重新读取。",
  invalid_music_id: "歌曲或歌单标识无效，请重新读取。",
  music_auth_required: "音乐账号登录已失效，请重新扫码登录。",
  music_account_unavailable: "音乐账号已断开，请重新连接。",
  music_upstream_unavailable: "音乐平台暂时无法连接，请稍后重试。",
  music_upstream_invalid: "音乐平台返回的数据不完整，本次未替换已保存歌单。",
  music_upstream_rejected:
    "音乐平台未能返回所请求的内容，请检查账号状态后重试。",
  music_scan_incomplete: "歌单未读取完整，已保留上次同步结果。",
  music_source_changed: "读取期间歌单发生变化，请重新同步。",
  music_snapshot_too_large: "歌单超过本次读取范围，已保留原记录。",
  music_rate_limited: "音乐平台请求暂受限制，请稍后重试。",
  music_subscription_paused: "此歌单已暂停，请先恢复同步。",
  music_directory_absolute: "请填写音乐目录的完整路径。",
  music_directory_unavailable: "音乐目录暂不可访问，请检查目录路径。",
  navidrome_auth_required: "Navidrome 登录未通过，请检查用户名和密码。",
  navidrome_not_configured: "请先保存并启用 Navidrome 连接。",
  navidrome_unavailable: "Navidrome 暂时无法连接，稍后会重新检查。",
  navidrome_invalid_response: "Navidrome 返回的数据不完整，已保留同步记录。",
  navidrome_server_required: "此地址未识别为 Navidrome 服务。",
  navidrome_folder_missing: "音乐库已变化，请重新测试连接并选择音乐库。",
  navidrome_mapping_changed: "天枢音乐目录已变化，请更新 Navidrome 目录映射。",
  navidrome_forbidden: "Navidrome 账号没有执行此操作的权限。",
  navidrome_not_found: "Navidrome 中这份歌单已不存在，可以重新创建。",
  navidrome_playlist_conflict: "无法唯一确认这份 Navidrome 歌单的归属。",
  navidrome_readonly_playlist: "Navidrome 将这份歌单标为只读，无法更新。",
  navidrome_catalog_incomplete: "Navidrome 曲库未读取完整，已保留原歌单。",
  navidrome_creation_unverified:
    "创建结果尚未确认，正在查找原请求创建的歌单，避免重复创建。",
  navidrome_sync_unverified: "同步后读回的歌单与预期不一致，尚未标记成功。",
  navidrome_scanning: "Navidrome 正在扫描音乐库，完成后自动补齐。",
  navidrome_request_failed: "Navidrome 未能完成请求，请检查服务状态。",
  navidrome_export_unavailable:
    "无法更新歌单导出目录的 Navidrome 导入规则，请检查存档目录权限。",
};
export function timestamp(seconds: number | null | undefined) {
  if (seconds == null || !Number.isFinite(seconds)) return "尚无记录";
  return new Date(seconds * 1000).toLocaleString("zh-CN", { hour12: false });
}
export function tone(state: string): "blue" | "yellow" | "red" | "gray" {
  if (
    [
      "completed",
      "published",
      "ready",
      "available",
      "active",
      "asset_indexed",
    ].includes(state)
  )
    return "blue";
  if (["failed", "auth_required", "rule_error", "unavailable"].includes(state))
    return "red";
  if (
    ["paused", "cancelled", "revoked", "unknown", "not_configured"].includes(
      state,
    )
  )
    return "gray";
  return "yellow";
}
export function word(state: string) {
  return stateWords[state] ?? reasonWords[state] ?? errorWords[state] ?? state;
}
export function qualityWord(value: string | null) {
  if (!value) return "待下载确认";
  return (
    videoQualities.find((quality) => quality.quality_id === value)?.label ??
    value
  );
}
