/**
 * 天枢前端「人类可读语义净化层」
 * 专门解决后端内部哈希 ID（如 memory_batch_284f...、person_8e242a...、group:conversation_b3455...）
 * 直接裸露在界面卡片上造成乱码刷屏的问题，将其转化为温润易读的中文业务语义。
 */

const HEX_ID_PATTERN = /\b([a-z_]+?)[_:]?([0-9a-f]{12,64})\b/gi;

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
};

/**
 * 提取短码（如 #284f70），仅在二级技术溯源折叠面板内按需展示
 */
export function shortHashTag(raw: string | null | undefined): string {
  if (!raw) return "";
  const cleaned = raw.trim();
  const match = cleaned.match(/([0-9a-f]{6,64})$/i);
  if (match) {
    return `#${match[1].slice(0, 6)}`;
  }
  if (cleaned.length > 14) {
    return `#${cleaned.slice(-6)}`;
  }
  return cleaned;
}

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
    return `群聊会话 (${shortHashTag(trimmed)})`;
  }
  if (/^conversation_/i.test(trimmed)) {
    return `对话会话 (${shortHashTag(trimmed)})`;
  }
  if (/^person_/i.test(trimmed)) {
    return `对话伙伴 (${shortHashTag(trimmed)})`;
  }
  if (/^actor[_:]/i.test(trimmed)) {
    return `伴生角色 (${shortHashTag(trimmed)})`;
  }
  if (/^memory_batch_/i.test(trimmed)) {
    return `归档批次 ${shortHashTag(trimmed)}`;
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
  return text.replace(HEX_ID_PATTERN, (full, prefix: string, hash: string) => {
    if (knownNames?.[full]) return knownNames[full];
    const normalized = prefix.toLowerCase().replace(/_$/, "");
    const label = ENTITY_PREFIX_LABELS[normalized];
    const shortCode = `#${hash.slice(0, 6)}`;
    if (label) {
      return `${label} ${shortCode}`;
    }
    return shortCode;
  });
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
      return kind ? sanitizeInternalText(kind) : "原始记录";
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
