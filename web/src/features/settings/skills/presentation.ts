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
const capabilityReasons: Record<string, string> = {
  voice_profile_not_configured:
    "此角色尚未绑定音色。连接、音色查询和转写可分别查看状态；需要语音表达时再选择声线。",
  speech_profile_connection_unavailable:
    "角色声线已绑定，但其合成连接当前不可用，请查看原连接。",
  speech_not_configured:
    "尚未配置语音连接，可先保存连接，再按需要选择角色音色。",
  image_profile_not_configured:
    "此角色尚未保存形象与图像引擎绑定，暂不能进行角色自拍。",
  image_profile_engine_unavailable:
    "角色形象已保存，但所绑定的图像引擎当前不可用。",
  image_engine_not_configured: "尚未配置可用图像引擎。",
};
export function capabilityReason(reason: string) {
  return capabilityReasons[reason] ?? reason;
}
export function stateOf(skill: Skill) {
  const available = skill.availability;
  if (!available.implemented) return "待接入执行适配器";
  if (!skill.enabled || !available.enabled) return "已停用";
  if (available.reason === "voice_profile_not_configured")
    return "角色未绑定音色";
  if (available.reason === "image_profile_not_configured")
    return "角色形象待配置";
  if (!available.configured) return "尚未配置";
  if (available.provider_supported === false) return "对端不支持";
  return skill.can_execute ? "当前可执行" : "当前不可执行";
}
