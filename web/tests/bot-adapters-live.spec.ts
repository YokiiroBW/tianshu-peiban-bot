import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { resolve } from "node:path";

// Built UI and real local services. No page.route or adapter API interception.
const root = resolve(import.meta.dirname, "../../..");
const password = "synthetic-local-password-014";
type Fixture = {
  platform_url: string;
  host_url: string;
  host_key: string;
  account_id: string;
  actor_id: string;
  contact_id: string;
};
let service: ChildProcess | undefined;

async function start(): Promise<Fixture> {
  const python = process.env.TS_BOT_ADAPTER_LIVE_PYTHON;
  if (!python)
    throw new Error(
      "TS_BOT_ADAPTER_LIVE_PYTHON must name the H test Python runtime",
    );
  service = spawn(python, ["scripts/dev/bot_adapters_live_fixture.py"], {
    cwd: root,
    stdio: "pipe",
  });
  const child = service;
  return await new Promise<Fixture>((ok, fail) => {
    let output = "";
    let errors = "";
    let settled = false;
    const finish = (error?: Error, fixture?: Fixture) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) fail(error);
      else ok(fixture!);
    };
    const timer = setTimeout(
      () => finish(new Error(`fixture startup timeout: ${errors}`)),
      30000,
    );
    child.stdout!.on("data", (chunk: Buffer) => {
      output += chunk.toString();
      const lines = output.split(/\r?\n/);
      output = lines.pop() ?? "";
      for (const line of lines) {
        if (line.startsWith("bot_adapter_live_ready ")) {
          finish(
            undefined,
            JSON.parse(line.slice("bot_adapter_live_ready ".length)) as Fixture,
          );
        }
      }
    });
    child.stderr!.on("data", (chunk: Buffer) => {
      errors += chunk.toString();
    });
    child.once("error", (error) => finish(error));
    child.once("exit", (code) =>
      finish(new Error(`fixture exited ${code}: ${errors}`)),
    );
  });
}

async function stop() {
  const child = service;
  service = undefined;
  if (!child || child.exitCode !== null) return;
  await new Promise<void>((ok, fail) => {
    const timer = setTimeout(() => {
      child.kill();
      fail(new Error("fixture cleanup timeout"));
    }, 12000);
    child.once("exit", (code) => {
      clearTimeout(timer);
      code === 0 ? ok() : fail(new Error(`fixture cleanup exited ${code}`));
    });
    child.stdin!.end("stop\n");
  });
}
test.afterEach(stop);

async function submit(page: Page, operation: string, button: string) {
  const path = `/api/v1/bot-adapters/${operation}`;
  const response = page.waitForResponse(
    (item) => item.url().endsWith(path) && item.request().method() === "POST",
  );
  await page.getByRole("button", { name: button, exact: true }).click();
  const result = await response;
  expect(result.ok(), `${operation} status: ${result.status()}`).toBeTruthy();
  const headers = await result.request().allHeaders();
  expect(headers["x-csrf-token"], `${operation} CSRF`).toBeTruthy();
  expect(headers.cookie, `${operation} session cookie`).toBeTruthy();
  return await result.json();
}

async function accept(page: Page, info: TestInfo) {
  const fixture = await start();
  await page.goto(`${fixture.platform_url}/#/settings/channels`);
  await expect(page.getByRole("heading", { name: "登录天枢" })).toBeVisible();
  await page.locator('input[name="username"]').fill("synthetic-admin");
  await page.locator('input[name="password"]').fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  const panel = page.getByRole("region", { name: "机器人适配器管理" });
  await expect(panel).toBeVisible();
  await expect(
    panel.getByRole("button", { name: "解锁连接管理" }),
  ).toBeVisible();
  await panel.getByLabel("管理员密码（二次验证）").fill(password);
  await panel.getByRole("button", { name: "解锁连接管理" }).click();
  await expect(panel.getByRole("button", { name: "添加适配器" })).toBeVisible();

  await panel.getByRole("button", { name: "添加适配器" }).click();
  await panel.getByLabel("机器人平台").selectOption("nonebot");
  await panel.getByLabel("插件地址").fill(fixture.host_url);
  await panel.getByLabel("插件连接密钥").fill(fixture.host_key);
  await panel.getByLabel("允许局域网 HTTP（仅私有或本机地址）").check();
  const probe = await submit(page, "probe", "检测连接并读取账号");
  expect(probe.protocol).toBe("tianshu.bot-adapter/v1");
  expect(probe.accounts).toEqual([
    { id: fixture.account_id, platform: "qq", label: "synthetic" },
  ]);
  await expect(panel.getByText("NoneBot 插件已响应。")).toBeVisible();
  await expect(panel.getByLabel("在线机器人账号")).toHaveValue(
    fixture.account_id,
  );
  await page.screenshot({ path: info.outputPath("probe.png"), fullPage: true });

  await panel.getByLabel("连接名称").fill("Synthetic NoneBot browser");
  await panel.getByLabel("回复角色").selectOption(fixture.actor_id);
  await panel.getByLabel("会话类型").selectOption("private");
  await panel.getByLabel("联系人 ID", { exact: true }).fill(fixture.contact_id);
  const created = await submit(page, "create", "保存为停用");
  expect(created.connection).toMatchObject({
    adapter: "nonebot",
    account_id: fixture.account_id,
    actor_id: fixture.actor_id,
    enabled: false,
    state: "disabled",
    conversation: { kind: "private", id: fixture.contact_id },
    allowed_authors: [fixture.contact_id],
  });
  const row = panel.locator("ul.bot-adapter-list > li");
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("已保存 · 未启用");
  await page.screenshot({
    path: info.outputPath("created-disabled.png"),
    fullPage: true,
  });

  const enabled = await submit(page, "enable", "启用");
  expect(enabled.connection).toMatchObject({
    id: created.connection.id,
    enabled: true,
    state: "ready",
    revision: created.connection.revision + 1,
  });
  await expect(row).toContainText("已启用");
  await expect(
    panel.getByText("适配器已启用，插件与后台状态已确认。"),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("enabled.png"),
    fullPage: true,
  });

  const disabled = await submit(page, "disable", "停用");
  expect(disabled.connection).toMatchObject({
    id: created.connection.id,
    enabled: false,
    state: "disabled",
    revision: enabled.connection.revision + 1,
  });
  await expect(row).toContainText("已保存 · 未启用");
  await expect(panel.getByText("适配器已停用，后台状态已确认。")).toBeVisible();
  await page.screenshot({
    path: info.outputPath("disabled.png"),
    fullPage: true,
  });
}

test("real browser and NoneBot host: login, probe, create, enable, disable", async ({
  page,
}, info) => {
  await accept(page, info);
});
