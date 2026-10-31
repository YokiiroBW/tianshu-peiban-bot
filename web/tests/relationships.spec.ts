import { expect, test, type Page } from "@playwright/test";

async function control(page: Page, action: string, values: object = {}) {
  return page.request.post("/__fixture/control", {
    headers: { "X-Fixture": "synthetic-only" },
    data: { action, ...values },
  });
}
async function signIn(page: Page) {
  await page.goto("/#/people");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: "查看角色", exact: true }),
  ).toBeEnabled();
}
async function personId(page: Page, account: string) {
  const session = await page.request.get("/api/v1/session");
  expect(session.ok()).toBeTruthy();
  const { csrf } = await session.json();
  const response = await page.request.post("/api/v1/qq-admin/profiles", {
    headers: {
      Origin: new URL(page.url()).origin,
      "X-CSRF-Token": csrf,
    },
    data: { limit: 100, after: null },
  });
  expect(response.ok()).toBeTruthy();
  const { items } = await response.json();
  const registered = items.find(
    (item: { qq_id: string; person_id: string }) => item.qq_id === account,
  );
  expect(registered).toBeDefined();
  return registered.person_id as string;
}
async function choosePerson(page: Page, account: string) {
  await page
    .getByLabel("用户目录", { exact: true })
    .getByRole("button")
    .filter({ has: page.getByText(`QQ ${account}`, { exact: true }) })
    .click();
  await page.getByRole("tab", { name: "关系", exact: true }).click();
}
async function select(page: Page, role = "actor:a", account = "10001") {
  await personId(page, account);
  await page
    .getByRole("combobox", { name: "查看角色", exact: true })
    .selectOption(role);
  await choosePerson(page, account);
  await expect(page.getByLabel("当前好感", { exact: true })).toBeVisible();
  await expect(page.getByLabel("称呼", { exact: true })).toBeEnabled();
}
async function saveLabel(page: Page, text: string) {
  await page
    .getByRole("combobox", { name: "关系类型", exact: true })
    .selectOption("friend");
  await page.getByLabel("称呼", { exact: true }).fill(text);
  await page.getByRole("button", { name: "保存关系", exact: true }).click();
  await expect(page.getByLabel("当前关系", { exact: true })).toContainText(
    text,
  );
  await expect(page.getByLabel("称呼", { exact: true })).toBeEnabled();
}

test.beforeEach(async ({ page }) => {
  await control(page, "permission", { enabled: true });
  await control(page, "delay", { seconds: 0 });
  await control(page, "release_response");
  await signIn(page);
  await select(page);
});

test("private relation, reason history, draft cancel and rapid role/person switches", async ({
  page,
}, info) => {
  await saveLabel(page, "合成甲私聊称呼");
  await page.getByLabel("人工调整值", { exact: true }).fill("3");
  await page
    .getByLabel("调整原因", { exact: true })
    .fill("仅甲与一号的合成原因");
  await page.getByRole("button", { name: "调整好感", exact: true }).click();
  await expect(page.getByLabel("好感原因历史")).toContainText(
    "仅甲与一号的合成原因",
  );
  await page.getByLabel("称呼", { exact: true }).fill("未保存草稿");
  await page.getByRole("button", { name: "取消修改", exact: true }).click();
  await expect(page.getByLabel("称呼", { exact: true })).toHaveValue(
    "合成甲私聊称呼",
  );
  await page.goto("/#/roles/chat");
  await page.goto("/#/people");
  await select(page);
  await expect(page.getByLabel("当前关系", { exact: true })).toContainText(
    "合成甲私聊称呼",
  );
  await expect(page.getByLabel("好感原因历史")).toContainText(
    "仅甲与一号的合成原因",
  );
  await page.screenshot({
    path: info.outputPath("saved-reentry.png"),
    fullPage: true,
  });
  const firstPerson = await personId(page, "10001");
  const secondPerson = await personId(page, "10002");
  expect(firstPerson).not.toBe(secondPerson);
  await control(page, "delay", { seconds: 0.35 });
  await choosePerson(page, "10002");
  await expect(page.getByLabel("当前关系", { exact: true })).not.toBeVisible();
  await expect(page.getByLabel("好感原因历史")).not.toBeVisible();
  await page
    .getByRole("combobox", { name: "查看角色", exact: true })
    .selectOption("actor:b");
  await choosePerson(page, "10001");
  await choosePerson(page, "10002");
  await expect(page.getByLabel("称呼", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("好感原因历史")).not.toContainText(
    "仅甲与一号的合成原因",
  );
  await expect(page.getByLabel("当前关系", { exact: true })).not.toContainText(
    "合成甲私聊称呼",
  );
  await control(page, "delay", { seconds: 0 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: info.outputPath("pair-isolation.png"),
    fullPage: true,
  });
});

test("concurrent save conflict requires refresh and never claims success", async ({
  page,
  context,
}, info) => {
  const other = await context.newPage();
  await other.goto("/#/people");
  await select(other);
  await saveLabel(page, "合成较新关系");
  await other.getByLabel("称呼", { exact: true }).fill("合成陈旧修改");
  await other.getByRole("button", { name: "保存关系", exact: true }).click();
  await expect(
    other
      .getByRole("region", { name: "角色人物关系管理", exact: true })
      .getByRole("alert"),
  ).toContainText("已被其他操作更新");
  await expect(
    other.getByRole("button", { name: "保存关系", exact: true }),
  ).toBeDisabled();
  await other.getByRole("button", { name: "刷新关系", exact: true }).click();
  await expect(other.getByLabel("称呼", { exact: true })).toHaveValue(
    "合成较新关系",
  );
  await other.screenshot({
    path: info.outputPath("conflict-refresh.png"),
    fullPage: true,
  });
  await other.close();
});

test("duplicate clicks produce one intent; cancellation refreshes actual owner", async ({
  page,
}, info) => {
  let writes = 0;
  page.on("request", (request) => {
    if (request.url().endsWith("/api/v1/relationships/manage")) writes++;
  });
  await control(page, "hold_response");
  await page.getByLabel("称呼", { exact: true }).fill("合成取消后核对");
  await page
    .getByRole("button", { name: "保存关系", exact: true })
    .evaluate((button: HTMLButtonElement) => {
      button.click();
      button.click();
    });
  await expect
    .poll(async () => {
      const response = await control(page, "state");
      return (await response.json()).committed;
    })
    .toBe(true);
  await page.getByRole("button", { name: "取消等待", exact: true }).click();
  await expect(
    page
      .getByRole("region", { name: "角色人物关系管理", exact: true })
      .getByRole("status"),
  ).toContainText("操作可能已提交");
  await expect(page.getByLabel("当前好感", { exact: true })).not.toBeVisible();
  expect(writes).toBe(1);
  await control(page, "release_response");
  await page.getByRole("button", { name: "刷新关系", exact: true }).click();
  await expect(page.getByLabel("称呼", { exact: true })).toHaveValue(
    "合成取消后核对",
  );
  await page.screenshot({
    path: info.outputPath("cancel-readback.png"),
    fullPage: true,
  });
});

test("unknown write is not replayed; explicit readback permits a reviewed retry", async ({
  page,
}, info) => {
  let writes = 0;
  page.on("request", (request) => {
    if (request.url().endsWith("/api/v1/relationships/manage")) writes++;
  });
  await control(page, "error");
  await page.getByLabel("称呼", { exact: true }).fill("合成失败重试");
  await page.getByRole("button", { name: "保存关系", exact: true }).click();
  await expect(
    page
      .getByRole("region", { name: "角色人物关系管理", exact: true })
      .getByRole("alert"),
  ).toContainText("提交结果尚未确认");
  await expect(
    page.getByRole("button", { name: "保存关系", exact: true }),
  ).toBeDisabled();
  expect(writes).toBe(1);
  await page.screenshot({
    path: info.outputPath("unknown-result.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "刷新关系", exact: true }).click();
  await expect(page.getByLabel("称呼", { exact: true })).toBeEnabled();
  await saveLabel(page, "合成失败重试");
  expect(writes).toBe(2);
});

test("twenty frozen days and unfreeze never catch up decay; history stays scoped", async ({
  page,
}, info) => {
  const unfreeze = page.getByRole("button", { name: "解冻好感", exact: true });
  if (await unfreeze.isVisible()) {
    await unfreeze.click();
    await expect(page.getByLabel("称呼", { exact: true })).toBeEnabled();
  }
  await page.getByLabel("人工调整值", { exact: true }).fill("20");
  await page.getByLabel("调整原因", { exact: true }).fill("合成冻结验收原因");
  await page.getByRole("button", { name: "调整好感", exact: true }).click();
  await expect(page.getByLabel("称呼", { exact: true })).toBeEnabled();
  const score = await page
    .getByLabel("当前好感", { exact: true })
    .textContent();
  await page.getByRole("button", { name: "冻结好感", exact: true }).click();
  await expect(unfreeze).toBeEnabled();
  await control(page, "advance", { seconds: 20 * 86400 });
  await page.getByRole("button", { name: "刷新关系", exact: true }).click();
  await expect(page.getByLabel("当前好感", { exact: true })).toHaveText(score!);
  await unfreeze.click();
  await expect(
    page.getByRole("button", { name: "冻结好感", exact: true }),
  ).toBeEnabled();
  await expect(page.getByLabel("当前好感", { exact: true })).toHaveText(score!);
  await page.screenshot({
    path: info.outputPath("unfreeze-no-catchup.png"),
    fullPage: true,
  });
  await select(page, "actor:b", "10002");
  await expect(page.getByLabel("好感原因历史")).not.toContainText(
    "合成冻结验收原因",
  );
});

test("permission withdrawal and session expiry remove private relation and history", async ({
  page,
}, info) => {
  await control(page, "permission", { enabled: false });
  await page.getByRole("button", { name: "刷新关系", exact: true }).click();
  await expect(page.getByLabel("当前好感", { exact: true })).not.toBeVisible();
  await expect(page.getByLabel("好感原因历史")).not.toBeVisible();
  await page.screenshot({
    path: info.outputPath("permission-withdrawn.png"),
    fullPage: true,
  });
  await control(page, "permission", { enabled: true });
  await control(page, "expire");
  await page.getByRole("button", { name: "刷新关系", exact: true }).click();
  await expect(page.getByLabel("当前好感", { exact: true })).not.toBeVisible();
  await expect(page.getByLabel("好感原因历史")).not.toBeVisible();
});
