/**
 * 天枢前端「人类可读语义净化层」
 * 专门解决后端内部哈希 ID（如 memory_batch_284f...、person_8e242a...、group:conversation_b3455...）
 * 直接裸露在界面卡片上造成乱码刷屏的问题，将其转化为温润易读的中文业务语义。
 */

const HEX_ID_PATTERN =
  /\b([a-z_]+(?:[:_][a-z_]+)*)[-_:]([0-9a-f]{12,64})(?::[a-z][a-z0-9_.-]*)?\b/gi;
const UUID_PATTERN =
  /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi;

const ENTITY_PREFIX_LABELS: Record<string, string> = {
  memory_batch: "记忆归档批次",
  conversation: "对话会话",
  "group:conversation": "群聊会话",
  person: "关联人物",
  actor: "伴生角色",
  episode: "经历记录",
  activity: "起居活动",
  action: "执行任务",
  message: "对话消息",
  observation: "感知观测",
  reflection: "主观手记",
  diary: "日记卷宗",
  writing: "创作篇章",
  commitment: "约定事项",
  provider: "模型供应商",
  source: "来源记录",
  rule: "学习规则",
  request: "操作请求",
  attempt: "执行记录",
  connection: "渠道连接",
};

/**
 * 将 `group:conversation_b3455cc...` 或 `person_8e242a...` 转化为人类可读名称
 */
export function humanizeScopeTarget(
  raw: string | null | undefined,
  knownNames?: Record<string, string>,
): string {
  if (!raw) return "默认范围";
  const trimmed = raw.trim();
  if (knownNames && knownNames[trimmed]) {
    return knownNames[trimmed];
  }
  const withoutGroupPrefix = trimmed.replace(/^group:/i, "");
  if (knownNames && knownNames[withoutGroupPrefix]) {
    return knownNames[withoutGroupPrefix];
  }

  if (/^group:conversation_/i.test(trimmed)) {
    return "群聊会话";
  }
  if (/^conversation_/i.test(trimmed)) {
    return "对话会话";
  }
  if (/^person_/i.test(trimmed)) {
    return "对话伙伴";
  }
  if (/^actor[_:]/i.test(trimmed)) {
    return "伴生角色";
  }
  if (/^memory_batch_/i.test(trimmed)) {
    return "记忆整理批次";
  }

  return sanitizeInternalText(trimmed, knownNames);
}

/**
 * 净化任意文本中夹杂的内部长哈希标识
 */
export function sanitizeInternalText(
  text: string | null | undefined,
  knownNames?: Record<string, string>,
): string {
  if (!text) return "";
  if (knownNames?.[text]) return knownNames[text];
  return text
    .replace(HEX_ID_PATTERN, (full, prefix: string) => {
      if (knownNames?.[full]) return knownNames[full];
      const normalized = prefix.toLowerCase().replace(/_$/, "");
      const label = ENTITY_PREFIX_LABELS[normalized];
      return label ?? "关联记录";
    })
    .replace(UUID_PATTERN, (full) => knownNames?.[full] ?? "关联记录")
    .replace(/\b[0-9a-f]{24,64}\b/gi, "关联记录");
}

const codeNames: Record<string, string> = {
  pending: "等待处理",
  queued: "排队中",
  running: "正在执行",
  doing: "正在执行",
  succeeded: "执行成功",
  failed: "执行失败",
  cancelled: "已取消",
  unknown: "结果尚未确认",
  active: "已启用",
  disabled: "已停用",
  enabled: "已启用",
  ready: "已就绪",
  unavailable: "当前不可用",
  model_no_compatible_route: "当前没有符合请求要求的模型，等待配置更新",
  not_recorded: "未记录",
  not_started: "尚未提交",
  submitted: "已提交",
  delivered: "已送达",
  complete: "已完成",
  partial: "部分完成",
  not_requested: "未请求",
  verified: "已验证",
  unverified: "尚未验证",
  applied: "已应用",
  stale: "需要更新",
  private: "私聊",
  group: "群聊",
  web: "网页",
  qq: "QQ",
  onebot: "机器人连接",
  napcat: "QQ 机器人",
  tianshu: "天枢",
  external: "外部服务",
  reply: "允许回复",
  observe: "仅观察",
  ignore: "忽略",
  permission_denied: "当前账号没有操作权限",
  forbidden: "服务拒绝访问，请检查账号权限",
  unauthorized: "身份验证未通过，请检查登录或密钥",
  rate_limited: "请求过于频繁，请稍后再试",
  provider_rate_limited: "供应商请求额度暂时用尽",
  timeout: "服务响应超时",
  connection_failed: "服务连接中断",
  provider_unavailable: "供应商暂时不可用",
  outcome_unknown: "提交结果尚未确认，请查看原任务",
  not_found: "记录不存在或已不可用",
  revision_conflict: "内容已更新，请刷新后重试",
  version_conflict: "内容已更新，请刷新后重试",
  source_unavailable: "原始材料当前不可读取",
  source_withdrawn: "原始材料已撤回",
  context_window_exceeded: "输入内容超过模型容量",
  invalid_response: "服务未返回有效结果",
};

/** Known codes get a translation; unknown codes stay explicitly unresolved. */
export function humanizeCode(
  value: string | null | undefined,
  fallback = "详情尚未说明",
): string {
  if (!value) return fallback;
  if (codeNames[value]) return codeNames[value];
  if (/^[a-z0-9_.:/-]+$/i.test(value)) return fallback;
  return sanitizeInternalText(value);
}

/**
 * 判断手记/记录的标题与正文是否实质重复（解决截图1中同一句话粗体+细体连印两遍的问题）
 */
export function dedupeTitleAndBody(
  title: string | null | undefined,
  body: string | null | undefined,
): { displayTitle: string; displayBody: string | null } {
  const cleanTitle = (title ?? "").trim();
  const cleanBody = (body ?? "").trim();

  if (!cleanTitle && !cleanBody) {
    return { displayTitle: "未命名手记", displayBody: null };
  }
  if (!cleanTitle) {
    return { displayTitle: cleanBody, displayBody: null };
  }
  if (!cleanBody) {
    return { displayTitle: cleanTitle, displayBody: null };
  }

  // Normalize punctuation & whitespace for comparison
  const norm = (s: string) => s.replace(/[\s，。；：！？,.!?;:…「」『』]/g, "");
  const normTitle = norm(cleanTitle);
  const normBody = norm(cleanBody);

  // 如果正文与标题完全一致，或标题是正文的前缀截断（如标题 40 字被截断，正文是完整句）
  if (normTitle === normBody) {
    return { displayTitle: cleanBody, displayBody: null };
  }
  if (normBody.startsWith(normTitle) && normTitle.length >= 12) {
    // 标题只是正文的前半截截断，直接把完整句作为正文，提炼简短引首作为标题
    const firstClause =
      cleanBody.split(/[；。！？\n]/)[0]?.trim() || cleanTitle;
    const remainder = cleanBody
      .slice(firstClause.length)
      .replace(/^[；。！？\s]+/, "")
      .trim();
    if (remainder) {
      return { displayTitle: firstClause, displayBody: remainder };
    }
    return { displayTitle: cleanBody, displayBody: null };
  }
  if (normTitle.startsWith(normBody)) {
    return { displayTitle: cleanTitle, displayBody: null };
  }

  return { displayTitle: cleanTitle, displayBody: cleanBody };
}

/**
 * 来源类型的人类友好中文映射
 */
export function humanizeSourceKind(kind: string | null | undefined): string {
  switch (kind) {
    case "message":
    case "dialogue_turn":
    case "chat":
      return "聊天对话";
    case "episode":
    case "life_episode":
      return "生活经历";
    case "observation":
    case "weather":
      return "环境观测";
    case "activity":
    case "life_activity":
      return "起居活动";
    case "reflection":
      return "主观手记";
    case "diary":
      return "日常日记";
    case "writing":
      return "文学创作";
    case "document":
    case "knowledge":
      return "资料典籍";
    default:
      return "来源记录";
  }
}

/**
 * 经历分类的人类可读标签与印章字
 */
export function presentEpisodeCategory(category: string | null | undefined): {
  label: string;
  seal: string;
  tone: "celadon" | "cinnabar" | "neutral";
} {
  switch (category) {
    case "human_input":
      return { label: "伙伴发言", seal: "言", tone: "celadon" };
    case "actor_response":
      return { label: "角色回应", seal: "答", tone: "celadon" };
    case "life_activity":
      return { label: "起居践行", seal: "行", tone: "cinnabar" };
    case "observation":
      return { label: "环境感知", seal: "观", tone: "neutral" };
    case "action_result":
      return { label: "工具回执", seal: "执", tone: "neutral" };
    default:
      return { label: "日常记录", seal: "录", tone: "neutral" };
  }
}
