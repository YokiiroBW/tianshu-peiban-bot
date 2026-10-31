import type { NativeProtocol } from "./providerTypes";
/** Endpoint templates never infer a protocol from the host or a model name. */
export const providerPresets: {
  id: string;
  name: string;
  base_url: string;
  protocol: NativeProtocol;
}[] = [
  {
    id: "custom",
    name: "自定义原生接口",
    base_url: "",
    protocol: "openai.chat",
  },
  {
    id: "openai-chat",
    name: "OpenAI Chat 模板",
    base_url: "https://api.openai.com/v1",
    protocol: "openai.chat",
  },
  {
    id: "openai-responses",
    name: "OpenAI Responses 模板",
    base_url: "https://api.openai.com/v1",
    protocol: "openai.responses",
  },
  {
    id: "anthropic",
    name: "Anthropic 模板",
    base_url: "https://api.anthropic.com/v1",
    protocol: "anthropic.messages",
  },
  {
    id: "gemini",
    name: "Gemini 模板",
    base_url: "https://generativelanguage.googleapis.com/v1beta",
    protocol: "gemini.generate",
  },
];
