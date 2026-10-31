import { expect, test, type Page } from "@playwright/test";

const admin = "synthetic-admin";
const password = "synthetic-local-password-014";

async function api(page: Page, path: string, body: object) {
  const origin = new URL(page.url()).origin;
  const session = await page.request.get(`${origin}/api/v1/session`);
  const { csrf } = (await session.json()) as { csrf: string };
  return page.request.post(`${origin}/api/v1/personas/${path}`, {
    headers: { "X-CSRF-Token": csrf, Origin: origin },
    data: body,
  });
}

async function signIn(page: Page) {
  await page.goto("/#/roles/persona");
  await page.getByLabel("管理员账号").fill(admin);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.locator(".persona-author-list .persona-subject"),
  ).toHaveCount(2);
}

test("clean persona page creates, edits and applies while preserving hidden data", async ({
  page,
}) => {
  await signIn(page);
  const panel = page.locator(".persona-page");
  await expect(panel.getByRole("heading", { name: "人格列表" })).toBeVisible();
  await expect(panel.locator(".persona-body")).toContainText("甲的初始人格");
  await expect(panel).not.toContainText("actor:a");
  await expect(panel).not.toContainText("版本历史");
  await expect(panel).not.toContainText("修订");

  await panel.getByRole("button", { name: "新建人格" }).click();
  await panel.getByLabel("名称").fill("温和伙伴");
  await panel
    .getByLabel("人设正文")
    .fill("你是温和、可信的陪伴者。\n保持真诚。");
  await panel.getByText("更多设置").click();
  await panel.getByLabel("简介").fill("隔离演练档案");
  await panel.getByLabel("语气").fill("平静而亲切");
  await panel.getByLabel("表达风格").fill("用简短的自然语言回答");
  await panel.getByLabel("称呼").fill("朋友");
  await panel.getByText("更多设置").click();
  await page.screenshot({
    path: "test-results/persona-clean-edit.png",
    fullPage: true,
  });
  let backPrompts = 0;
  page.once("dialog", async (dialog) => {
    backPrompts++;
    await dialog.dismiss();
  });
  await page.evaluate(() => window.history.back());
  await expect.poll(() => backPrompts).toBe(1);
  await expect(page).toHaveURL(/#\/companion\/2$/);
  await expect(panel.getByLabel("人设正文")).toHaveValue(
    "你是温和、可信的陪伴者。\n保持真诚。",
  );
  await panel.getByRole("button", { name: "保存草稿" }).click();
  await expect(
    panel.getByRole("status").filter({ hasText: "草稿已保存" }),
  ).toBeVisible();
  await expect(
    panel.locator(".persona-author-list .persona-subject"),
  ).toHaveCount(3);
  const created = await api(page, "profiles", {});
  const items = (await created.json()) as {
    profiles: { id: string; name: string }[];
  };
  const profile = items.profiles.find((item) => item.name === "温和伙伴")!;
  const saved = await api(page, "view", { id: profile.id });
  const savedItem = (await saved.json()) as {
    item: {
      content: {
        persona: string;
        tone: string;
        style: string;
        address: string;
      };
      description: string;
    };
  };
  expect(savedItem.item.description).toBe("隔离演练档案");
  expect(savedItem.item.content.tone).toBe("平静而亲切");
  expect(savedItem.item.content.style).toBe("用简短的自然语言回答");
  expect(savedItem.item.content.address).toBe("朋友");

  await panel.getByRole("button", { name: "编辑", exact: true }).click();
  await panel.getByLabel("人设正文").fill("你是温和、耐心的陪伴者。");
  await panel.getByRole("button", { name: "保存并应用" }).click();
  await expect(panel.getByLabel("应用到")).toBeVisible();
  await panel.getByLabel("应用到").selectOption("actor:a");
  await panel.getByRole("button", { name: "保存并应用" }).click();
  await expect(
    panel.getByRole("status").filter({ hasText: "已保存并应用" }),
  ).toBeVisible();
  const roleA = await api(page, "view", { id: "actor:a" });
  const roleAItem = (await roleA.json()) as {
    item: {
      content: { persona: string };
      additional_fields: string[];
      published_revision: string | null;
    };
  };
  expect(roleAItem.item.content.persona).toBe("你是温和、耐心的陪伴者。");
  expect(roleAItem.item.additional_fields).toContain("custom");
  expect(roleAItem.item.published_revision).not.toBeNull();
  await page.screenshot({
    path: "test-results/persona-clean-desktop.png",
    fullPage: true,
  });

  await panel
    .locator(".persona-author-list .persona-subject")
    .filter({ hasText: "角色 2" })
    .click();
  await panel.getByRole("button", { name: "编辑", exact: true }).click();
  await panel.getByLabel("名称").fill("乙的角色");
  await panel.getByLabel("人设正文").fill("乙的新人格");
  await panel.getByRole("button", { name: "保存并应用" }).click();
  await expect(
    panel.getByRole("status").filter({ hasText: "已保存并应用" }),
  ).toBeVisible();
  await expect(panel.locator(".persona-author-list")).toContainText("乙的角色");

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(panel.getByRole("button", { name: "人格列表" })).toBeVisible();
  await page.screenshot({
    path: "test-results/persona-clean-mobile-detail.png",
    fullPage: true,
  });
  await panel.getByRole("button", { name: "人格列表" }).click();
  await expect(panel.locator(".persona-author-list")).toBeVisible();
  await page.screenshot({
    path: "test-results/persona-clean-mobile-list.png",
    fullPage: true,
  });
  await panel
    .locator(".persona-author-list .persona-subject")
    .filter({ hasText: "乙的角色" })
    .click();
  await expect(panel.locator(".persona-body")).toContainText("乙的新人格");
  await page.setViewportSize({ width: 1440, height: 1000 });

  const latest = await api(page, "view", { id: "actor:b" });
  const latestItem = (await latest.json()) as { item: { version: number } };
  await panel.getByRole("button", { name: "编辑", exact: true }).click();
  await panel.getByLabel("人设正文").fill("本地尚未保存的编辑");
  const concurrent = await api(page, "save", {
    id: "actor:b",
    name: "乙的角色",
    description: "",
    expected: latestItem.item.version,
    client_request_id: "concurrent-role-save-clean-0001",
    content: { persona: "另一个编辑", tone: "", style: "", address: "" },
  });
  expect(concurrent.ok()).toBeTruthy();
  await panel.getByRole("button", { name: "保存草稿" }).click();
  await expect(panel.getByRole("alert")).toContainText("version_conflict");
  await expect(panel.getByLabel("人设正文")).toHaveValue("本地尚未保存的编辑");
  page.once("dialog", (dialog) => void dialog.accept());
  await panel
    .locator(".persona-author-list .persona-subject")
    .filter({ hasText: "乙的角色" })
    .click();
  await expect(panel.locator(".persona-body")).toContainText("另一个编辑");
  await page.reload();
  await expect(
    panel
      .locator(".persona-author-list .persona-subject")
      .filter({ hasText: "乙的角色" }),
  ).toContainText("有新草稿");
  await panel
    .locator(".persona-author-list .persona-subject")
    .filter({ hasText: "乙的角色" })
    .click();
  await panel.getByLabel("更多操作").click();
  await panel.getByRole("button", { name: "复制为新人格" }).click();
  await expect(panel.getByLabel("名称")).toHaveValue("乙的角色 副本");
  await expect(panel.getByLabel("人设正文")).toHaveValue("另一个编辑");
});

test("read-only account sees detail and cannot write", async ({ page }) => {
  await page.goto("https://127.0.0.1:4841/#/roles/persona");
  await page.getByLabel("管理员账号").fill(admin);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  const panel = page.locator(".persona-page");
  await expect(panel.locator(".persona-body")).not.toBeEmpty();
  await expect(panel.getByRole("button", { name: "新建人格" })).toBeDisabled();
  await expect(
    panel.getByRole("button", { name: "编辑", exact: true }),
  ).toHaveCount(0);
  const refused = await api(page, "create", {
    name: "不可创建",
    description: "",
    client_request_id: "read-only-create-clean-0001",
    content: { persona: "不可保存", tone: "", style: "", address: "" },
  });
  expect(refused.status()).toBe(403);
  expect((await refused.json()).code).toBe("persona_write_required");
});
