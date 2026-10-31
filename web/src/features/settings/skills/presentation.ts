import type { Skill } from "./types";
export const settings: Record<string, string> = {
  speech: "#/settings/speech",
  images: "#/settings/images",
  plugins: "#/plugins",
  memory: "#/memory",
  content: "#/roles/life",
  weather: "#/settings/weather",
  device: "#/home/devices",
  channels: "#/settings/channels",
  books: "#/media/books",
  music: "#/media/music",
  comics: "#/media/comics",
};
export function stateOf(skill: Skill) {
  const available = skill.availability;
  if (!available.implemented) return "待接入执行适配器";
  if (!skill.enabled || !available.enabled) return "已停用";
  if (!available.configured) return "尚未配置";
  if (available.provider_supported === false) return "对端不支持";
  return skill.can_execute ? "当前可执行" : "当前不可执行";
}
