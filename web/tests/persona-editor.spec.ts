import { expect, test } from "@playwright/test";
import {
  answer,
  apiPath,
  command,
  emptyPage,
  nativeSession,
} from "./native-fixture";

test("persona revision editor works on a fresh page with full-width text and lossless fields", async ({
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
  await page.route(apiPath("companion/personas/catalog"), (route) =>
    answer(route, {
      can_manage: true,
      items: [
        {
          persona_id: "persona",
          name: persona.name,
          revision: 7,
          current_revision: 7,
        },
      ],
    }),
  );
  await page.route(apiPath("companion/personas/view"), (route) =>
    answer(route, persona),
  );
  await page.route(apiPath("companion/personas/history"), (route) =>
    answer(route, emptyPage),
  );
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
    return answer(
      route,
      command(body, { persona_id: "persona", revision: 8 }, 8),
    );
  });
  await page.goto("/#/roles/persona");
  await page.getByRole("button", { name: "编辑为新修订", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "编辑为新修订",
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
    path: `../.runtime/people-groups-ui/persona-editor-${info.project.name}.png`,
    fullPage: true,
  });
  await dialog
    .getByRole("textbox", { name: "语气", exact: true })
    .fill("新的语气");
  await dialog
    .getByRole("textbox", { name: "本次修订说明", exact: true })
    .fill("只调整语气");
  await dialog
    .getByRole("button", { name: "保存作者修订", exact: true })
    .click();
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
    note: "只调整语气",
  });
  await expect(dialog).not.toBeVisible();
  expect(roleWrites).toBe(0);
});
