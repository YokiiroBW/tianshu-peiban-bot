import { expect, test } from "@playwright/test";
import {
  answer,
  apiPath,
  command,
  emptyPage,
  nativeSession,
} from "./native-fixture";

test("persona edits the same document and refreshes its content without version controls", async ({
  page,
}, info) => {
  await nativeSession(page);
  const persona = {
    persona_id: "persona",
    revision: 7,
    name: "澄汐",
    author_text: "完整人格正文：保留角色的性格、习惯和相处背景。\n".repeat(100),
    tone: "平静温柔",
    style: "简短自然",
    address: "按熟悉程度称呼",
    world: "日常生活背景",
  };
  const writes: Record<string, unknown>[] = [];
  let roleWrites = 0;
  let historyReads = 0;
  await page.route(apiPath("companion/personas/catalog"), (route) =>
    answer(route, {
      can_manage: true,
      items: [
        {
          persona_id: "persona",
          name: persona.name,
          revision: persona.revision,
        },
      ],
    }),
  );
  await page.route(apiPath("companion/personas/view"), (route) =>
    answer(route, persona),
  );
  await page.route(apiPath("companion/personas/history"), (route) => {
    historyReads++;
    return answer(route, emptyPage);
  });
  await page.route(apiPath("companion/roles/get"), (route) =>
    answer(route, {
      actor_id: "actor",
      name: "澄汐",
      revision: 9,
      enabled: true,
      persona_id: "persona",
      persona_revision: 6,
      can_manage: true,
    }),
  );
  await page.route(apiPath("companion/roles/save"), (route) => {
    roleWrites++;
    return answer(route, {});
  });
  await page.route(apiPath("companion/personas/save"), (route) => {
    const body = route.request().postDataJSON();
    writes.push(body);
    Object.assign(persona, { ...body, revision: 8 });
    return answer(
      route,
      command(body, { persona_id: "persona", revision: 8 }, 8),
    );
  });
  await page.goto("/#/roles/persona");
  const listItem = page.getByRole("button", {
    name: "澄汐 当前使用",
    exact: true,
  });
  await expect(listItem).toBeVisible();
  await listItem.click();
  const detail = page.getByRole("region", { name: "人格详情" });
  await expect(detail).toBeVisible();
  await expect(page.getByText("作者修订记录", { exact: true })).toHaveCount(0);
  await expect(detail.getByRole("button", { name: "供澄汐使用" })).toHaveCount(
    0,
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  const textBox = await detail.locator(".persona-text").boundingBox();
  expect(textBox!.width).toBeLessThanOrEqual(768);
  await page.screenshot({
    animations: "disabled",
    path: `../.runtime/persona-storage/persona-detail-${info.project.name}.png`,
    fullPage: true,
  });
  await page.getByRole("button", { name: "编辑人格", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "编辑人格",
    exact: true,
  });
  await expect(dialog).toBeVisible();
  const name = dialog.getByLabel("人格名称", { exact: true });
  const body = dialog.getByRole("textbox", {
    name: "完整作者人格",
    exact: true,
  });
  await expect(body).toHaveValue(persona.author_text);
  const formBox = await dialog.locator("form").boundingBox();
  const nameBox = await name.boundingBox(),
    bodyBox = await body.boundingBox();
  expect(formBox && nameBox && bodyBox).toBeTruthy();
  expect(bodyBox!.width).toBeGreaterThan(formBox!.width * 0.95);
  expect(bodyBox!.y).toBeGreaterThan(nameBox!.y + nameBox!.height);
  expect(
    await dialog.evaluate(
      (element) => element.scrollWidth <= element.clientWidth + 1,
    ),
  ).toBe(true);
  await page.screenshot({
    animations: "disabled",
    path: `../.runtime/persona-storage/persona-editor-${info.project.name}.png`,
    fullPage: true,
  });
  await dialog
    .getByRole("textbox", { name: "语气", exact: true })
    .fill("新的语气");
  await expect(
    dialog.getByRole("textbox", { name: "本次修订说明", exact: true }),
  ).toHaveCount(0);
  await dialog.getByRole("button", { name: "保存人格", exact: true }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toMatchObject({
    persona_id: persona.persona_id,
    name: persona.name,
    author_text: persona.author_text,
    style: persona.style,
    address: persona.address,
    world: persona.world,
    expected_revision: 7,
    tone: "新的语气",
  });
  expect(writes[0]).not.toHaveProperty("note");
  await expect(dialog).not.toBeVisible();
  await expect(
    detail.locator(".persona-body").filter({ hasText: "新的语气" }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("navigation", { name: "人格列表", includeHidden: true })
      .locator(".persona-subject"),
  ).toHaveCount(1);
  expect(roleWrites).toBe(0);
  expect(historyReads).toBe(0);
});

test("persona detail hides empty sections and can return to the list on mobile", async ({
  page,
}, info) => {
  await nativeSession(page);
  await page.route(apiPath("companion/personas/catalog"), (route) =>
    answer(route, {
      can_manage: true,
      items: [
        { persona_id: "other", name: "另一个人格", revision: 1 },
        { persona_id: "bound", name: "澄汐", revision: 4 },
      ],
    }),
  );
  await page.route(apiPath("companion/roles/get"), (route) =>
    answer(route, {
      actor_id: "actor",
      name: "澄汐",
      enabled: true,
      revision: 3,
      persona_id: "bound",
      persona_revision: 4,
      can_manage: true,
    }),
  );
  await page.route(apiPath("companion/personas/view"), (route) => {
    const id = route.request().postDataJSON().persona_id;
    return answer(route, {
      persona_id: id,
      name: id === "bound" ? "澄汐" : "另一个人格",
      revision: 4,
      author_text: "完整正文\n" + "长文字".repeat(200),
      tone: "",
      style: "",
      address: "",
      world: "",
    });
  });
  await page.goto("/#/roles/persona");
  const bound = page.getByRole("button", {
    name: "澄汐 当前使用",
    exact: true,
  });
  await expect(bound).toHaveAttribute("aria-pressed", "true");
  await bound.click();
  const detail = page.getByRole("region", { name: "人格详情" });
  await expect(
    detail.getByRole("heading", { name: "澄汐", exact: true }),
  ).toBeVisible();
  await expect(
    detail.getByRole("heading", { name: "语气", exact: true }),
  ).toHaveCount(0);
  await expect(detail.getByText("未单独填写", { exact: true })).toHaveCount(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  if (info.project.name === "mobile") {
    await detail
      .getByRole("button", { name: "返回人格列表", exact: true })
      .click();
  }
  await page.getByRole("button", { name: "另一个人格", exact: true }).click();
  await expect(
    detail.getByRole("heading", { name: "另一个人格", exact: true }),
  ).toBeVisible();
  await expect(
    detail.getByRole("button", { name: /应用|使用.*修订|供.*使用/ }),
  ).toHaveCount(0);
  if (info.project.name === "mobile") {
    await detail
      .getByRole("button", { name: "返回人格列表", exact: true })
      .click();
    await expect(
      page.getByRole("navigation", { name: "人格列表" }),
    ).toBeVisible();
    await expect(detail).not.toBeVisible();
  }
});

test("role profile selects a persona document without a version", async ({
  page,
}) => {
  await nativeSession(page);
  const actor = {
    actor_id: "actor",
    name: "澄汐",
    enabled: false,
    revision: 5,
    persona_id: "first",
    persona_revision: 2,
    can_manage: true,
  };
  const writes: Record<string, unknown>[] = [];
  await page.route(apiPath("companion/roles/list"), (route) =>
    answer(route, { ...emptyPage, items: [actor] }),
  );
  await page.route(apiPath("companion/roles/get"), (route) =>
    answer(route, actor),
  );
  await page.route(apiPath("companion/personas/catalog"), (route) =>
    answer(route, {
      can_manage: true,
      items: [
        { persona_id: "first", name: "原人格", revision: 2 },
        { persona_id: "next", name: "新的人格", revision: 9 },
      ],
    }),
  );
  await page.route(apiPath("companion/roles/save"), (route) => {
    const body = route.request().postDataJSON();
    writes.push(body);
    return answer(route, command(body, { actor_id: "actor" }, 6));
  });
  await page.goto("/#/roles");
  await page.getByRole("button", { name: "基本资料", exact: true }).click();
  const dialog = page.getByRole("dialog").filter({
    has: page.getByRole("heading", { name: "角色基本资料", exact: true }),
  });
  await dialog
    .getByRole("combobox", { name: "人格档案", exact: true })
    .selectOption("next");
  await dialog
    .getByRole("button", { name: "保存基本资料", exact: true })
    .click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toMatchObject({
    actor_id: "actor",
    persona_id: "next",
    expected_revision: 5,
  });
  expect(writes[0]).not.toHaveProperty("persona_revision");
});
