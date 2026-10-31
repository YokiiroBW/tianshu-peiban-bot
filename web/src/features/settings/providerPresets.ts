/** OpenCode's documented Chat Completions models, checked 2026-10-04.
 * Sources: https://opencode.ai/docs/zen/ and https://opencode.ai/docs/go/.
 * Other endpoint families are intentionally absent from the Chat model picker.
 */
export const providerPresets = [
  { id: "custom", name: "自定义兼容服务", baseUrl: "", modelId: "" },
  {
    id: "openai",
    name: "OpenAI",
    baseUrl: "https://api.openai.com/v1",
    modelId: "",
  },
  {
    id: "deepseek",
    name: "DeepSeek",
    baseUrl: "https://api.deepseek.com",
    modelId: "",
  },
  {
    id: "opencode",
    name: "OpenCode Zen",
    baseUrl: "https://opencode.ai/zen/v1",
    modelId: "deepseek-v4.1-flash",
  },
  {
    id: "opencode-go",
    name: "OpenCode Go",
    baseUrl: "https://opencode.ai/zen/go/v1",
    modelId: "deepseek-v4.1-flash",
  },
] as const;

const common = [
  "deepseek-v4.1-flash",
  "deepseek-v4-pro",
  "deepseek-v4-flash",
  "deepseek-v4-flash-vision-exp",
  "glm-5.3-flash",
  "glm-5.3",
  "glm-5.2",
  "kimi-k3",
  "kimi-k2.7-code",
  "kimi-k2.6",
  "longcat-2.5-preview-free",
  "space-bunny-free",
];
const chatModels = {
  opencode: new Set([
    ...common,
    "qwen3.8-max",
    "minimax-m3",
    "minimax-m2.7",
    "minimax-m2.5",
    "glm-5.1",
    "glm-5",
    "kimi-k2.5",
    "big-pickle",
    "fledge-alpha-free",
    "mimo-v2.6-flash-free",
    "mimo-v2.5-free",
    "ling-3.1-flash-free",
    "ling-3.0-flash-fin-free",
    "nemotron-3-ultra-free",
    "nemotron-3.5-lightning-free",
  ]),
  "opencode-go": new Set([
    ...common,
    "longcat-2.0",
    "mimo-v2.6-flash",
    "mimo-v2.6-pro",
    "mimo-v2.5",
    "mimo-v2.5-pro",
    "hy4-preview",
    "hy3",
  ]),
};

export function openCodePreset(
  baseUrl: string,
): keyof typeof chatModels | null {
  try {
    const url = new URL(baseUrl);
    if (
      url.protocol !== "https:" ||
      url.host !== "opencode.ai" ||
      url.search ||
      url.hash
    )
      return null;
    const path = url.pathname.replace(/\/$/, "");
    return path === "/zen/v1"
      ? "opencode"
      : path === "/zen/go/v1"
        ? "opencode-go"
        : null;
  } catch {
    return null;
  }
}

export function compatibleModels(baseUrl: string, models: string[]) {
  const preset = openCodePreset(baseUrl);
  return preset ? models.filter((id) => chatModels[preset].has(id)) : models;
}
