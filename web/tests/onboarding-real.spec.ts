import { test, expect, type Page } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

// No route mocks: built UI, actual cookies/CSRF, SQLite and process restarts.
const root = resolve(import.meta.dirname, "../../..");
const chosen = "Eight123";
const original = "synthetic-local-password-014";
let server: ChildProcess | undefined;
async function start(mode: string, data: string) {
  const python = process.env.TS_ONBOARDING_PYTHON;
  if (!python)
    throw new Error("TS_ONBOARDING_PYTHON must name the test Python runtime");
  server = spawn(
    python,
    [
      "tests/backend/run_onboarding_fixture.py",
      "--mode",
      mode,
      "--data",
      data,
      "--port",
      "5192",
    ],
    { cwd: root, stdio: "pipe" },
  );
  const child = server;
  await new Promise<void>((ok, fail) => {
    let output = "";
    const timer = setTimeout(
      () => fail(new Error("fixture startup timeout: " + output)),
      15000,
    );
    child.stdout!.on("data", (chunk) => {
      output += chunk;
      if (output.includes("onboarding_fixture_ready")) {
        clearTimeout(timer);
        ok();
      }
    });
    child.stderr!.on("data", (chunk) => {
      output += chunk;
    });
    child.once("error", (error) => {
      clearTimeout(timer);
      fail(error);
    });
    child.once("exit", (code) => {
      clearTimeout(timer);
      fail(new Error(`fixture exited ${code}: ${output}`));
    });
  });
}
async function stop() {
  if (!server || server.exitCode !== null) return;
  const child = server;
  await new Promise<void>((ok, fail) => {
    const timer = setTimeout(() => {
      child.kill();
      fail(new Error("fixture cleanup timeout"));
    }, 8000);
    child.once("exit", (code) => {
      clearTimeout(timer);
      code === 0 ? ok() : fail(new Error(`fixture cleanup ${code}`));
    });
    child.stdin!.end("stop\n");
  });
  server = undefined;
}
test.afterEach(stop);
async function login(page: Page, username: string, password: string) {
  await page.locator('input[name="username"]').fill(username);
  await page.locator('input[name="password"]').fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
}
async function choose(page: Page) {
  await page.locator('input[name="username"]').fill("chosen-owner");
  await page.locator('input[name="password"]').fill(chosen);
  await page.locator('input[name="confirm"]').fill(chosen);
}

test("real first use creates account, shares login and survives server restart", async ({
  page,
}, info) => {
  const data = await mkdtemp(join(tmpdir(), "tianshu-onboarding-create-"));
  await start("create", data);
  await page.goto("/#/workbench");
  await expect(
    page.getByRole("heading", { name: "欢迎来到天枢" }),
  ).toBeVisible();
  await expect(
    page.getByText("密码至少 8 个字符", { exact: false }),
  ).toBeVisible();
  await choose(page);
  const password = page.locator('input[name="password"]');
  await expect(password).toHaveAttribute("minlength", "8");
  await password.fill("Seven77");
  await password.blur();
  expect(
    await password.evaluate(
      (input: HTMLInputElement) => input.validity.tooShort,
    ),
  ).toBe(true);
  await password.fill(chosen);
  expect(
    await password.evaluate((input: HTMLInputElement) => input.checkValidity()),
  ).toBe(true);
  await page
    .locator('input[name="setup_token"]')
    .fill("synthetic-first-install-token-20260926");
  await page.screenshot({
    path: info.outputPath("first-use.png"),
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "创建管理员账号", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "开始使用指引" }),
  ).toBeVisible();
  await expect(page.getByText("账号已就绪 · chosen-owner")).toBeVisible();
  await page.goto("/#/settings/models");
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
  await expect(page.locator('input[name="username"]')).toHaveCount(0);
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "登录天枢" })).toBeVisible();
  await stop();
  await start("create", data);
  await page.goto("/#/settings/models");
  await expect(page).toHaveURL(/login\?next=/);
  await login(page, "chosen-owner", chosen);
  await expect(page.getByRole("alert")).toContainText("登录页面已过期");
  await page.getByRole("button", { name: "刷新账号状态", exact: true }).click();
  await login(page, "chosen-owner", chosen);
  await expect(page).toHaveURL(/#\/settings\/2$/);
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
});

test("real deployment claim replaces original account and preserves chosen login", async ({
  page,
}, info) => {
  const data = await mkdtemp(join(tmpdir(), "tianshu-onboarding-claim-"));
  await start("claim", data);
  await page.goto("/#/roles/chat");
  await login(page, "synthetic-admin", original);
  await expect(
    page.getByRole("heading", { name: "设置你自己的账号" }),
  ).toBeVisible();
  await choose(page);
  await page.locator('input[name="current_password"]').fill(original);
  await page
    .getByRole("button", { name: "保存自己的账号", exact: true })
    .click();
  await expect(page).toHaveURL(/#\/companion$/);
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("claimed-account.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await login(page, "synthetic-admin", original);
  await expect(page.getByRole("alert")).toContainText("账号或密码不正确");
  await login(page, "chosen-owner", chosen);
  await expect(
    page.getByRole("region", { name: "开始使用指引" }),
  ).toBeVisible();
});
