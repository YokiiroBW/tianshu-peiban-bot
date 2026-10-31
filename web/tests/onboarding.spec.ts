import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// Explicit UI-only server fixture. Production always asks its same-origin backend.
const anonymous = { authenticated: false, csrf: "synthetic-anonymous-csrf" };
const signedIn = {
  authenticated: true,
  csrf: "synthetic-authenticated-csrf",
  username: "synthetic-admin",
  conversations: [
    { id: "fixture", label: "测试会话", actors: ["fixture-role"] },
  ],
  dialogue: {
    available: false,
    model: "not_configured",
    code: "model_not_configured",
  },
};
async function fillAccount(page: Page) {
  await page.getByLabel("管理员账号", { exact: true }).fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-password-only");
}

test("fresh install waits for server, confirms password and submits setup once", async ({
  page,
}, info) => {
  let session: object = {
    ...anonymous,
    onboarding: {
      state: "create_admin",
      credential_source: "setup",
      setup_token_required: true,
    },
  };
  let reads = 0;
  let setups = 0;
  let release!: () => void;
  const waiting = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/v1/session", async (route) => {
    if (++reads === 1) await waiting;
    await route.fulfill({ json: session });
  });
  await page.route("**/api/v1/setup", async (route) => {
    setups++;
    expect(route.request().headers()["x-csrf-token"]).toBe(anonymous.csrf);
    expect(route.request().postDataJSON()).toEqual({
      username: "synthetic-admin",
      password: "synthetic-password-only",
      setup_token: "synthetic-setup-token-only-123",
    });
    session = signedIn;
    await route.fulfill({ json: signedIn });
  });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "正在确认账号状态" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "创建管理员账号" }),
  ).toHaveCount(0);
  release();
  await expect(page).toHaveURL(/#\/setup/);
  await page.getByLabel("管理员账号", { exact: true }).fill("synthetic-admin");
  await page
    .getByLabel("设置密码", { exact: true })
    .fill("synthetic-password-only");
  await page
    .getByLabel("确认密码", { exact: true })
    .fill("synthetic-password-wrong");
  await page.getByLabel("安装验证码").fill("synthetic-setup-token-only-123");
  await page.getByRole("button", { name: "创建管理员账号" }).click();
  await expect(page.getByRole("alert")).toContainText("不一致");
  expect(setups).toBe(0);
  await page
    .getByLabel("确认密码", { exact: true })
    .fill("synthetic-password-only");
  await page.screenshot({
    path: info.outputPath("first-run-setup.png"),
    fullPage: true,
  });
  expect(
    (
      await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze()
    ).violations,
  ).toEqual([]);
  await page
    .locator(".account-panel form")
    .evaluate((form: HTMLFormElement) => {
      form.requestSubmit();
      form.requestSubmit();
    });
  await expect(
    page.getByRole("heading", { name: "开始使用天枢" }),
  ).toBeVisible();
  expect(setups).toBe(1);
  await expect(
    page.getByRole("link", { name: "配置模型", exact: true }),
  ).toHaveAttribute("href", "#/settings/models");
  await expect(
    page.getByRole("link", { name: "选择角色 / 开始对话" }),
  ).toHaveCount(0);
  await page.screenshot({
    path: info.outputPath("first-run-next-step.png"),
    fullPage: true,
  });
});

test("deployment account uses one login and returns to deep link across pages", async ({
  page,
}, info) => {
  let session: object = {
    ...anonymous,
    onboarding: {
      state: "sign_in",
      credential_source: "deployment",
      setup_token_required: false,
    },
  };
  let logins = 0;
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({ json: session }),
  );
  await page.route("**/api/v1/login", async (route) => {
    logins++;
    session = signedIn;
    await route.fulfill({ json: signedIn });
  });
  await page.route("**/api/v1/access/view", (route) =>
    route.fulfill({ status: 503, json: { code: "not_configured" } }),
  );
  await page.goto("/#/settings/connections");
  await expect(page).toHaveURL(/#\/login\?next=/);
  await expect(
    page.getByText("部署时已设置管理员账号。", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "创建管理员账号" }),
  ).toHaveCount(0);
  await page.screenshot({
    path: info.outputPath("unified-login.png"),
    fullPage: true,
  });
  await fillAccount(page);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(/#\/settings\/1$/);
  await expect(
    page.getByRole("heading", { name: "连接设置入口" }),
  ).toBeVisible();
  await expect(page.locator(".app-shell")).toBeVisible();
  await expect(page.locator(".auth-screen")).toHaveCount(0);
  await page.goto("/#/roles/chat");
  await expect(
    page.getByRole("heading", { name: "留一点时间，慢慢聊" }),
  ).toBeVisible();
  await expect(page.getByLabel("管理员账号")).toHaveCount(0);
  expect(logins).toBe(1);
});

test("legacy session never offers registration; network failure does not become setup", async ({
  page,
}) => {
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({ json: anonymous }),
  );
  await page.goto("/#/setup");
  await expect(page.getByRole("heading", { name: "登录天枢" })).toBeVisible();
  await expect(page.getByLabel("确认密码")).toHaveCount(0);
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({ status: 503, json: { code: "dependency_unavailable" } }),
  );
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "暂时无法连接天枢" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "创建管理员账号" }),
  ).toHaveCount(0);
  await expect(page.getByLabel("管理员账号")).toHaveCount(0);
});

test("expired session returns to unified login; logout clears all private page content", async ({
  page,
}) => {
  let session: object = signedIn;
  let expired = false;
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({ json: session }),
  );
  await page.route("**/api/v1/login", async (route) => {
    session = signedIn;
    await route.fulfill({ json: signedIn });
  });
  await page.route("**/api/v1/logout", async (route) => {
    session = anonymous;
    await route.fulfill({ json: anonymous });
  });
  await page.goto("/#/roles/chat");
  await expect(
    page.getByRole("heading", { name: "留一点时间，慢慢聊" }),
  ).toBeVisible();
  session = anonymous;
  expired = true;
  await page.getByRole("button", { name: "重新连接", exact: true }).click();
  await expect(page.getByRole("heading", { name: "登录天枢" })).toBeVisible();
  expect(expired).toBe(true);
  await fillAccount(page);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(/#\/companion$/);
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page.getByRole("heading", { name: "登录天枢" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "留一点时间，慢慢聊" }),
  ).toHaveCount(0);
  await page.goto("/#/settings/models");
  await expect(page).toHaveURL(/#\/login/);
});

test("claiming deployment account requires original password then refreshes session", async ({
  page,
}) => {
  let session: object = {
    ...signedIn,
    onboarding: {
      state: "claim_admin",
      credential_source: "deployment",
      setup_token_required: false,
    },
  };
  let claims = 0;
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({ json: session }),
  );
  await page.route("**/api/v1/account/claim", async (route) => {
    claims++;
    expect(route.request().postDataJSON()).toEqual({
      username: "my-admin",
      password: "my-private-password",
      current_password: "synthetic-deployment-password",
    });
    session = {
      ...signedIn,
      username: "my-admin",
      onboarding: {
        state: "ready",
        credential_source: "account",
        setup_token_required: false,
      },
    };
    await route.fulfill({ json: session });
  });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "设置你自己的账号" }),
  ).toBeVisible();
  await page.getByLabel("新管理员账号").fill("my-admin");
  await page.getByLabel("原部署密码").fill("synthetic-deployment-password");
  await page
    .getByLabel("设置密码", { exact: true })
    .fill("my-private-password");
  await page.getByLabel("确认密码").fill("my-private-password");
  await page.getByRole("button", { name: "保存自己的账号" }).click();
  await expect(page.getByText("账号已就绪 · my-admin")).toBeVisible();
  expect(claims).toBe(1);
});

test("connection missing is distinct from model not configured and ready dialogue", async ({
  page,
}) => {
  let session = {
    ...signedIn,
    dialogue: {
      available: false,
      model: "ready",
      code: "core_web_not_connected",
    },
  };
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({ json: session }),
  );
  await page.goto("/");
  await expect(
    page.getByRole("link", { name: "检查连接与授权" }),
  ).toHaveAttribute("href", "#/settings/connections");
  await expect(
    page.getByRole("link", { name: "配置模型", exact: true }),
  ).toHaveCount(0);
  session = {
    ...session,
    dialogue: { available: true, model: "ready", code: "ready" },
  };
  await page.getByRole("button", { name: "刷新准备状态" }).click();
  await expect(
    page.getByRole("link", { name: "选择角色 / 开始对话" }),
  ).toHaveAttribute("href", "#/roles/chat");
});

test("a refused request with a live session does not send the user back to login", async ({
  page,
}) => {
  let reads = 0;
  const session = {
    ...signedIn,
    dialogue: { available: true, model: "unverified", code: "ready" },
  };
  await page.route("**/api/v1/session", (route) => {
    reads++;
    return route.fulfill({ json: session });
  });
  await page.route("**/api/v1/snapshot", (route) =>
    route.fulfill({ status: 401, json: { code: "unauthorized" } }),
  );
  await page.goto("/#/roles/chat");
  await expect.poll(() => reads).toBeGreaterThanOrEqual(3);
  await expect(page).toHaveURL(/#\/companion$/);
  await expect(
    page.getByRole("heading", { name: "留一点时间，慢慢聊" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
  await expect(page.getByLabel("管理员账号")).toHaveCount(0);
});

test("unverified configuration invites a first attempt and unknown model is never ready", async ({
  page,
}) => {
  let session = {
    ...signedIn,
    dialogue: { available: true, model: "unverified", code: "ready" },
  };
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({ json: session }),
  );
  await page.goto("/");
  await expect(
    page.getByText("配置已登记，可以尝试第一条对话。", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByText("模型配置已登记，尚未验证实际回复"),
  ).toBeVisible();
  session = {
    ...session,
    dialogue: { available: true, model: "unknown", code: "ready" },
  };
  await page.getByRole("button", { name: "刷新准备状态" }).click();
  await expect(
    page.getByRole("link", { name: "选择角色 / 开始对话" }),
  ).toHaveCount(0);
});

test("setup completed elsewhere has a recovery action returning to login", async ({
  page,
}) => {
  let session: object = {
    ...anonymous,
    onboarding: {
      state: "create_admin",
      credential_source: "setup",
      setup_token_required: false,
    },
  };
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({ json: session }),
  );
  await page.route("**/api/v1/setup", async (route) => {
    session = anonymous;
    await route.fulfill({ status: 409, json: { code: "setup_closed" } });
  });
  await page.goto("/#/setup");
  await page.getByLabel("管理员账号", { exact: true }).fill("synthetic-admin");
  await page
    .getByLabel("设置密码", { exact: true })
    .fill("synthetic-password-only");
  await page
    .getByLabel("确认密码", { exact: true })
    .fill("synthetic-password-only");
  await page.getByRole("button", { name: "创建管理员账号" }).click();
  await expect(page.getByRole("alert")).toContainText("首次创建已关闭");
  await page.getByRole("button", { name: "刷新账号状态" }).click();
  await expect(page.getByRole("heading", { name: "登录天枢" })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "创建管理员账号" }),
  ).toHaveCount(0);
});
