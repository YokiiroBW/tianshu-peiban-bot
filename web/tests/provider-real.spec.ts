import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect } from "@playwright/test";

const info = JSON.parse(
  readFileSync(
    resolve("test-results/provider-real-info.json"),
    "utf8",
  ),
) as { upstream_url: string };
const eventsPath = resolve("test-results/provider-real-events.json");
const modePath = resolve("test-results/provider-real-mode.txt");
const events = () => JSON.parse(readFileSync(eventsPath, "utf8")) as string[];

test("first chat without a model leads to provider configuration", async ({
  page,
}) => {
  await page.goto("/#/roles/chat");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "前往配置模型供应商" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "前往配置模型供应商" }).click();
  await expect(page).toHaveURL(/#\/settings\/2/);
});

test("real same-origin browser to platform, gateway and recorded TLS upstream", async ({
  page,
}, testInfo) => {
  await page.goto("/#/settings/models");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "验证管理员密码后管理供应商" }),
  ).toBeVisible();
  await page.getByLabel("管理员密码").fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "解锁模型管理" }).click();
  await expect(
    page.getByRole("heading", { name: "还没有供应商" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "添加供应商" }).click();
  await page.getByLabel("名称").fill("隔离录制服务");
  await page.getByLabel("API 基础地址").fill(info.upstream_url);
  await page.getByLabel("API Key").fill("synthetic-browser-only-key");
  await page.getByRole("button", { name: "保存供应商" }).click();
  await expect(
    page.getByRole("heading", { name: "隔离录制服务" }),
  ).toBeVisible();
  expect(events().filter((item) => item === "completion")).toHaveLength(0);
  await expect(page.getByRole("button", { name: "设为默认" })).toBeDisabled();
  await page.getByRole("button", { name: "获取模型" }).click();
  await expect(
    page.getByRole("button", { name: "fixture-text-model" }),
  ).toBeVisible();
  expect(events().filter((item) => item === "models")).toHaveLength(1);
  expect(events().filter((item) => item === "completion")).toHaveLength(0);
  await page.getByRole("button", { name: "fixture-text-model" }).click();
  await expect(page.getByLabel("API Key")).toHaveValue("");
  await page.getByRole("button", { name: "保存供应商" }).click();
  await expect(page.locator(".provider-card")).toContainText(
    "fixture-text-model",
  );
  await page.getByRole("button", { name: "测试回复" }).click();
  await expect(
    page.locator(".provider-card dd").filter({ hasText: /^测试通过/ }),
  ).toBeVisible();
  expect(events().filter((item) => item === "completion")).toHaveLength(1);
  await page.getByRole("button", { name: "设为默认" }).click();
  await expect(page.getByText("默认对话模型", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "开始对话" })).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("provider-real-same-origin.png"),
    fullPage: true,
  });
  await page.getByRole("link", { name: "开始对话" }).click();
  await expect(page).toHaveURL(/#\/companion/);
});

test("real browser manual model, switch default, clear key, disable and delete", async ({
  page,
}) => {
  const browserOrigins: string[] = [];
  page.on("request", (request) =>
    browserOrigins.push(new URL(request.url()).origin),
  );
  await page.goto("/#/settings/models");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByLabel("管理员密码").fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "解锁模型管理" }).click();
  await expect(
    page.getByRole("heading", { name: "隔离录制服务" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "添加供应商" }).click();
  await page.getByLabel("名称").fill("手动模型服务");
  await page.getByLabel("API 基础地址").fill(info.upstream_url);
  await page.getByLabel("模型 ID（可稍后选择）").fill("fixture-text-model");
  await page.getByLabel("API Key").fill("synthetic-second-browser-key");
  await page.getByRole("button", { name: "保存供应商" }).click();
  const second = page
    .locator(".provider-card")
    .filter({ hasText: "手动模型服务" });
  const first = page
    .locator(".provider-card")
    .filter({ hasText: "隔离录制服务" });
  await expect(second).toContainText("尚未测试");
  await second.getByRole("button", { name: "测试回复" }).click();
  await expect(second).toContainText("测试通过");
  await second.getByRole("button", { name: "设为默认" }).click();
  await expect(second).toContainText("默认对话模型");
  await first.getByRole("button", { name: "清除密钥" }).click();
  await expect(first).toContainText("未设置");
  await expect(first).toContainText("尚未测试");
  await first.getByRole("button", { name: "停用" }).click();
  await expect(first).toContainText("已停用");
  await first.getByRole("button", { name: "删除" }).click();
  await first.getByRole("button", { name: "确认删除" }).click();
  await expect(first).toHaveCount(0);
  await expect(second).toContainText("默认对话模型");
  expect(
    await page.evaluate(() => JSON.stringify(Object.entries(localStorage))),
  ).not.toContain("synthetic-second-browser-key");
  expect(
    browserOrigins.every((origin) => origin === "http://127.0.0.1:4814"),
  ).toBe(true);
});

test("real browser explains unsupported enumeration and failed key without leaking upstream text", async ({
  page,
}) => {
  await page.goto("/#/settings/models");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByLabel("管理员密码").fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "解锁模型管理" }).click();
  await page.getByRole("button", { name: "添加供应商" }).click();
  await page.getByLabel("名称").fill("故障录制服务");
  await page.getByLabel("API 基础地址").fill(info.upstream_url);
  await page.getByLabel("API Key").fill("synthetic-bad-key");
  await page.getByRole("button", { name: "保存供应商" }).click();
  const card = page
    .locator(".provider-card")
    .filter({ hasText: "故障录制服务" });
  writeFileSync(modePath, "unsupported");
  await card.getByRole("button", { name: "获取模型" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "该服务不支持获取模型列表",
  );
  await card.getByRole("button", { name: "编辑" }).click();
  await page.getByLabel("模型 ID（可稍后选择）").fill("fixture-text-model");
  await page.getByRole("button", { name: "保存供应商" }).click();
  writeFileSync(modePath, "wrong-key");
  const before = events().filter((item) => item === "completion").length;
  await card.getByRole("button", { name: "测试回复" }).click();
  await expect(page.getByRole("alert")).toContainText("服务拒绝了密钥");
  await expect(card).toContainText("测试失败");
  await expect(card).toContainText("请检查 API Key");
  await expect(card.getByRole("button", { name: "设为默认" })).toBeDisabled();
  expect(events().filter((item) => item === "completion")).toHaveLength(
    before + 1,
  );
  expect(await page.locator("body").innerText()).not.toContain(
    "synthetic-secret-must-not-appear",
  );
  writeFileSync(modePath, "normal");
});

test("contract error fixture keeps unknown paid-test result explicit and does not resend", async ({
  page,
}) => {
  await page.goto("/#/settings/models");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByLabel("管理员密码").fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "解锁模型管理" }).click();
  await page.getByRole("button", { name: "添加供应商" }).click();
  await page.getByLabel("名称").fill("未知回执服务");
  await page.getByLabel("API 基础地址").fill(info.upstream_url);
  await page.getByLabel("模型 ID（可稍后选择）").fill("fixture-text-model");
  await page.getByLabel("API Key").fill("synthetic-unknown-test-key");
  await page.getByRole("button", { name: "保存供应商" }).click();
  let attempts = 0;
  await page.route("**/api/v1/providers/test", async (route) => {
    attempts += 1;
    await route.fulfill({
      status: 409,
      json: {
        schema_version: 1,
        request_id: "request:fixture",
        code: "result_unknown",
        execution_state: "unknown",
        retryable: false,
      },
    });
  });
  const card = page
    .locator(".provider-card")
    .filter({ hasText: "未知回执服务" });
  await card.getByRole("button", { name: "测试回复" }).click();
  await expect(page.getByRole("alert")).toContainText("模型可能已被调用");
  await expect(page.getByRole("alert")).toContainText("人工核对后台状态");
  await expect(card.getByRole("button", { name: "设为默认" })).toBeDisabled();
  await page.waitForTimeout(300);
  expect(attempts).toBe(1);
});

test("real invalid upstream response stays unknown in the provider card", async ({
  page,
}) => {
  await page.goto("/#/settings/models");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByLabel("管理员密码").fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "解锁模型管理" }).click();
  await page.getByRole("button", { name: "添加供应商" }).click();
  await page.getByLabel("名称").fill("未知测试状态服务");
  await page.getByLabel("API 基础地址").fill(info.upstream_url);
  await page.getByLabel("模型 ID（可稍后选择）").fill("fixture-text-model");
  await page.getByLabel("API Key").fill("synthetic-unknown-outcome-key");
  await page.getByRole("button", { name: "保存供应商" }).click();
  const card = page
    .locator(".provider-card")
    .filter({ hasText: "未知测试状态服务" });
  writeFileSync(modePath, "invalid");
  try {
    await card.getByRole("button", { name: "测试回复" }).click();
    await expect(page.getByRole("alert")).toContainText(
      "模型服务返回了无法识别的内容",
    );
    await expect(card).toContainText("结果未知");
    await expect(card).not.toContainText("测试失败");
    await expect(card.getByRole("button", { name: "设为默认" })).toBeDisabled();
  } finally {
    writeFileSync(modePath, "normal");
  }
});

test("real management lock removes stale provider addresses", async ({
  page,
}) => {
  await page.goto("/#/settings/models");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByLabel("管理员密码").fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "解锁模型管理" }).click();
  const card = page
    .locator(".provider-card")
    .filter({ hasText: "未知测试状态服务" });
  await expect(card).toBeVisible();
  const locked = await page.evaluate(async () => {
    const session = await (
      await fetch("/api/v1/session", { credentials: "same-origin" })
    ).json();
    const response = await fetch("/api/v1/models/lock", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": session.csrf,
      },
      body: "{}",
    });
    return response.status;
  });
  expect(locked).toBe(200);
  await card.getByRole("button", { name: "获取模型" }).click();
  await expect(
    page.getByRole("heading", { name: "验证管理员密码后管理供应商" }),
  ).toBeVisible();
  await expect(page.locator(".provider-card")).toHaveCount(0);
  expect(await page.locator("body").innerText()).not.toContain(
    info.upstream_url,
  );
});
