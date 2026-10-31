// The persisted mood vocabulary already displayed by DailyLife.
const moods: Record<string, string> = {
  calm: "平静",
  neutral: "平稳",
  happy: "愉悦",
  relaxed: "放松",
  focused: "专注",
  sad: "低落",
  tired: "疲倦",
  anxious: "不安",
};
export const showMood = (mood: string | null | undefined) =>
  moods[mood ?? ""] ?? mood ?? "暂无心情记录";
