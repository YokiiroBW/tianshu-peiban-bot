import type { Comic, LanguageEvidence } from "./types";

export function languageName(value: string) {
  return (
    (
      {
        zh: "中文",
        "zh-Hans": "简体中文",
        "zh-Hant": "繁体中文",
        en: "英文",
        ja: "日文",
        unknown: "语言待确认",
        all: "多语言",
      } as Record<string, string>
    )[value] || value
  );
}

export function languageEvidence(value?: LanguageEvidence, available = false) {
  if (!value || value.language === "unknown")
    return available ? "已按中文筛选，章节语言待核对" : "语言待确认";
  const basis =
    (
      {
        source: "源声明",
        metadata: "作品资料",
        user_metadata: "手动资料",
        chapter_metadata: "章节资料",
        chapter_tag: "章节标记",
        language_tag: "语种标记",
        translation_tag: "汉化标记",
      } as Record<string, string>
    )[value.basis] || "来源资料";
  return `${languageName(value.language)} · ${basis}`;
}

export function chineseResult(comic: Comic) {
  return (
    !!comic.chinese_available ||
    /^zh(?:-|$)/i.test(
      comic.language_evidence?.language || comic.metadata.language,
    )
  );
}
