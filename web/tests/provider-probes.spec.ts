import { expect, test } from "@playwright/test";
import { answer, apiPath, command, nativeSession } from "./native-fixture";

test("capability probes register only successful tests and show the original failure", async ({
  page,
}) => {
  await nativeSession(page);
  const provider = {
    provider_id: "p",
    name: "测试供应商",
    base_url: "https://fixture.invalid/v1",
    protocol: "openai.chat",
    enabled: true,
    revision: 1,
  };
  const model = {
    route_id: "model-route",
    provider_id: "p",
    model: "deepseek-flash",
    quota_group: "quota",
    enabled: true,
    revision: 1,
    verified_capabilities: [] as string[],
    verification: null,
    parameters: {},
  };
  const catalog = {
    providers: [provider],
    routes: [model],
    quota_groups: [],
    bindings: [],
    functions: [],
    protocols: ["openai.chat"],
    probe_capabilities: {
      "openai.chat": [
        "text",
        "stream",
        "tools",
        "vision",
        "audio",
        "video",
        "json",
      ],
    },
  };
  const writes: Record<string, unknown>[] = [];
  let releaseTool!: () => void;
  await page.route(apiPath("models/catalog"), (route) =>
    answer(route, catalog),
  );
  await page.route(
    apiPath("models/routes/model-route/probe"),
    async (route) => {
      const body = route.request().postDataJSON();
      writes.push(body);
      if (body.capability === "tools")
        await new Promise<void>((resolve) => {
          releaseTool = resolve;
        });
      const passed = body.capability !== "video";
      if (passed) {
        model.revision += 1;
        model.verified_capabilities.push(body.capability);
      }
      await answer(
        route,
        command(
          body,
          {
            verification: passed ? "provider_test" : "not_verified",
            capability: body.capability,
            reason: passed ? null : "model_not_succeeded",
            receipt: {
              status: passed ? "succeeded" : "failed",
              error_code: passed ? null : "upstream_http_429",
              diagnostic: passed
                ? null
                : {
                    upstream: {
                      status: 429,
                      category: "quota_or_balance",
                      code: "insufficient_quota",
                    },
                  },
            },
            catalog,
          },
          model.revision,
        ),
      );
    },
  );
  await page.goto("/#/settings/models");
  await page
    .getByRole("button", { name: "测试与登记能力", exact: true })
    .click();
  const panel = page.getByRole("dialog", {
    name: "测试与登记模型能力",
    exact: true,
  });
  for (const name of [
    "测试文字",
    "测试流式输出",
    "测试工具调用",
    "测试图片理解",
    "测试音频",
    "测试视频理解",
    "测试结构化输出",
  ])
    await expect(
      panel.getByRole("button", { name, exact: true }),
    ).toBeVisible();
  await panel
    .getByRole("button", { name: "测试工具调用", exact: true })
    .click();
  await expect(
    panel.getByRole("button", { name: "测试图片理解", exact: true }),
  ).toBeDisabled();
  await expect.poll(() => writes.length).toBe(1);
  releaseTool();
  await expect(
    panel.getByText("工具调用：测试通过，已登记", { exact: true }),
  ).toBeVisible();
  await expect(
    panel.getByRole("button", { name: "测试工具调用（已登记）", exact: true }),
  ).toBeEnabled();
  await panel
    .getByRole("button", { name: "测试图片理解", exact: true })
    .click();
  await expect(
    panel.getByText("图片理解：测试通过，已登记", { exact: true }),
  ).toBeVisible();
  await panel
    .getByRole("button", { name: "测试视频理解", exact: true })
    .click();
  await expect(
    panel.getByText("视频理解：尚未通过测试", { exact: true }),
  ).toBeVisible();
  await expect(panel.getByText(/供应商额度或余额不足/)).toBeVisible();
  expect(writes.map((body) => body.expected_revision)).toEqual([1, 2, 3]);
  expect(model.verified_capabilities).toEqual(["tools", "vision"]);
});

test("embedding routes offer the native embedding test", async ({ page }) => {
  await nativeSession(page);
  await page.route(apiPath("models/catalog"), (route) =>
    answer(route, {
      providers: [
        {
          provider_id: "p",
          name: "向量供应商",
          base_url: "https://fixture.invalid/v1",
          protocol: "openai.embedding",
          enabled: true,
          revision: 1,
        },
      ],
      routes: [
        {
          route_id: "embedding",
          provider_id: "p",
          model: "embedding",
          quota_group: "quota",
          enabled: true,
          revision: 1,
          verified_capabilities: [],
          verification: null,
          parameters: {},
        },
      ],
      quota_groups: [],
      bindings: [],
      functions: [],
      protocols: ["openai.embedding"],
      probe_capabilities: { "openai.embedding": ["embed"] },
    }),
  );
  await page.goto("/#/settings/models");
  await page
    .getByRole("button", { name: "测试与登记能力", exact: true })
    .click();
  const panel = page.getByRole("dialog", {
    name: "测试与登记模型能力",
    exact: true,
  });
  await expect(
    panel.getByRole("button", { name: "测试语义嵌入", exact: true }),
  ).toBeVisible();
  await expect(
    panel.getByRole("button", { name: "测试文字", exact: true }),
  ).toHaveCount(0);
});
