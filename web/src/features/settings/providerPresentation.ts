import { functionNames, type ModelCatalog } from "./providerTypes";

export const capabilityNames: Record<string, string> = {
  text: "文字",
  stream: "流式输出",
  tools: "工具调用",
  vision: "图片理解",
  json: "结构化输出",
  audio: "音频",
  video: "视频理解",
  embed: "语义嵌入",
  embedding: "语义嵌入",
  rerank: "重排序",
  asr: "语音识别",
  tts: "语音合成",
};

/** Display names are separate from the immutable IDs used in requests. */
export function modelRouteName(catalog: ModelCatalog, routeId: string): string {
  const route = catalog.routes.find((item) => item.route_id === routeId);
  if (!route) return "模型配置不可用";
  const provider = catalog.providers.find(
    (item) => item.provider_id === route.provider_id,
  );
  const base = `${provider?.name || "未命名供应商"} / ${route.model}`;
  const duplicates = catalog.routes.filter(
    (item) =>
      item.provider_id === route.provider_id && item.model === route.model,
  );
  if (duplicates.length < 2) return base;
  const uses = [
    ...new Set(
      catalog.bindings
        .filter((item) => item.route_id === routeId && item.function !== "*")
        .map((item) => functionNames[item.function] ?? "其他功能"),
    ),
  ];
  const purpose = uses.slice(0, 2).join("、");
  return `${base} · ${purpose || "模型配置"}（${duplicates.indexOf(route) + 1}）`;
}

export function quotaGroupName(catalog: ModelCatalog, groupId: string): string {
  const providerIds = new Set(
    catalog.routes
      .filter((route) => route.quota_group === groupId)
      .map((route) => route.provider_id),
  );
  const names = catalog.providers
    .filter((provider) => providerIds.has(provider.provider_id))
    .map((provider) => provider.name);
  const index = catalog.quota_groups.findIndex(
    (group) => group.quota_group === groupId,
  );
  return `${names.join("、") || "共享账号"} · 容量组 ${index + 1}`;
}
