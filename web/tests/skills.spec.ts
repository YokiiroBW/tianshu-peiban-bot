import { expect, test, type Page } from "@playwright/test";

// Isolated UI fixture; the backend suite exercises real owner HTTP and encrypted storage.
async function fixture(page: Page) {
  const calls: Record<string, any>[] = [];
  const errors: string[] = [];
  let failNextEnable = false;
  page.on("pageerror", (error) => errors.push(error.message));
  function item(id: string, title: string, handler: string, installed = true) {
    const definition = {
      id,
      version: "1.0",
      title,
      description: `${title}的合成目录条目`,
      domain: id.split(".")[0],
      operations: [id],
      handler_id: handler,
    };
    const { handler_id: _, ...metadata } = definition;
    return {
      ...metadata,
      definition,
      source_id: installed ? "builtin" : "source:example",
      enabled: id === "image.generate",
      installed,
      revision: "a".repeat(64),
      availability: {
        state:
          id === "image.generate"
            ? "available"
            : installed
              ? "not_configured"
              : "unsupported",
        can_execute: id === "image.generate",
        reason_code: installed ? null : "handler_not_installed",
      },
      config: {
        provider: null,
        base_url: null,
        options: {},
        credential_configured: false,
      },
    };
  }
  const profiles: Record<string, any> = Object.fromEntries(
    ["actor:a", "actor:b"].map((actor) => [
      actor,
      {
        actor_version: 1,
        catalog_version: "b".repeat(64),
        skills: [
          item("image.generate", "角色生图", "image.generate"),
          item("game.guides", "GSCore 游戏攻略", "gscore.query"),
          item("media.aggregate", "视频图片漫画聚合", "future.media", false),
        ],
        sources: [
          {
            source_id: "source:example",
            name: "示例扩展目录",
            manifest_url: "https://directory.example.invalid/skills.json",
            enabled: true,
            expected_sha256: "c".repeat(64),
            credential_configured: true,
            version: 1,
            state: "ready",
            last_refreshed_at: "2026-10-06T00:00:00Z",
            content_sha256: "d".repeat(64),
            error_code: null,
          },
        ],
      },
    ]),
  );
  let revision = 1;
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(
      "/api/v1/",
      "",
    );
    const body = route.request().postDataJSON() ?? {};
    const profile = profiles[body.actor_id ?? "actor:a"];
    let response: any = {};
    calls.push({ path, ...body });
    if (path === "session")
      response = {
        authenticated: true,
        csrf: "skills-fixture",
        username: "skills-preview",
        conversations: [],
        dialogue: { available: false, code: "not_configured", model: "" },
      };
    else if (path === "life/actors") {
      expect(body.limit).toBeLessThanOrEqual(50);
      response = {
        schema_version: 1,
        fictional: true,
        items: [
          { actor_id: "actor:a", label: "角色甲" },
          { actor_id: "actor:b", label: "角色乙" },
        ],
        next_after_actor_id: null,
      };
    } else if (path === "skills/credentials")
      response = {
        revision,
        credential_configured: body.source_id
          ? true
          : profile.skills[1].config.credential_configured,
      };
    else if (
      path === "skills/read" ||
      path === "skills/manage" ||
      path === "skills/configure"
    ) {
      if (path !== "skills/read") {
        if (body.operation === "skill.enable" && failNextEnable) {
          failNextEnable = false;
          await route.abort("failed");
          return;
        }
        if (body.expected_revision !== profile.actor_version) {
          await route.fulfill({
            status: 409,
            contentType: "application/json",
            body: JSON.stringify({ code: "version_conflict" }),
          });
          return;
        }
        if (
          body.operation === "skill.enable" ||
          body.operation === "skill.disable"
        ) {
          const skill = profile.skills.find(
            (item: any) => item.id === body.value.skill_id,
          );
          skill.enabled = body.operation === "skill.enable";
          skill.availability = {
            state: skill.enabled
              ? skill.config.base_url
                ? "unavailable"
                : "not_configured"
              : "disabled",
            can_execute: false,
            reason_code: skill.enabled
              ? "connection_not_verified"
              : "skill_disabled",
          };
        } else if (body.operation === "skill.update") {
          expect(body.value.config.provider).toBe("gscore");
          expect(body.value.config.credential_ref).toBeUndefined();
          const skill = profile.skills.find(
            (item: any) => item.id === body.value.definition.id,
          );
          skill.config = {
            ...body.value.config,
            credential_configured:
              body.credential.action === "replace" ||
              (body.credential.action === "keep" &&
                skill.config.credential_configured),
          };
          revision += 1;
        } else if (body.operation === "source.refresh") {
          profile.sources[0].state = "unreachable";
          profile.sources[0].error_code = "connection_failed";
        }
        profile.actor_version += 1;
      }
      response = {
        schema_version: 1,
        request_id: "fixture:skills",
        actor_id: body.actor_id,
        result: profile,
      };
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(response),
    });
  });
  await page.goto("/#/settings/skills");
  await expect(
    page.getByRole("heading", { name: "角色技能", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "GSCore 游戏攻略" }),
  ).toBeVisible();
  return {
    calls,
    errors,
    failEnable: () => {
      failNextEnable = true;
    },
  };
}

test("skills first configuration, role isolation and enablement", async ({
  page,
}, info) => {
  const state = await fixture(page);
  const game = page
    .locator("article")
    .filter({ has: page.getByRole("heading", { name: "GSCore 游戏攻略" }) });
  await game.getByRole("button", { name: "配置 GSCore" }).click();
  await game
    .getByLabel("服务地址", { exact: true })
    .fill("http://192.0.2.10:28765");
  await game
    .getByLabel("GSCore WS_TOKEN", { exact: true })
    .fill("synthetic-browser-token-0001");
  await game.getByRole("button", { name: "保存 GSCore 连接" }).click();
  await expect(page.getByRole("status")).toContainText("已保存");
  await page.keyboard.press("Escape");
  await game.getByRole("button", { name: "启用技能" }).click();
  await expect(game.getByRole("button", { name: "停用技能" })).toBeVisible();
  await expect(game).toContainText("暂时不可用");
  await game.getByRole("button", { name: "停用技能" }).click();
  await expect(game).toContainText("已停用");
  await page.getByLabel("选择角色").selectOption("actor:b");
  await expect(game.getByRole("button", { name: "启用技能" })).toBeVisible();
  await game.getByRole("button", { name: "配置 GSCore" }).click();
  await expect(game.getByLabel("服务地址", { exact: true })).toHaveValue("");
  await expect(game.getByLabel("GSCore WS_TOKEN", { exact: true })).toHaveValue(
    "",
  );
  await page.keyboard.press("Escape");
  await page.getByLabel("选择角色").selectOption("actor:a");
  await game.getByRole("button", { name: "配置 GSCore" }).click();
  await expect(
    game.getByLabel("GSCore WS_TOKEN", { exact: true }),
  ).toHaveAttribute("placeholder", "已保存；留空保留");
  await expect(game.getByLabel("GSCore WS_TOKEN", { exact: true })).toHaveValue(
    "",
  );
  await expect(
    page.getByRole("link", { name: "管理生图与 ComfyUI" }),
  ).toHaveAttribute("href", "#/roles/life");
  expect(
    state.calls.filter((row) => row.path === "skills/configure"),
  ).toHaveLength(1);
  expect(state.errors).toEqual([]);
  await page.keyboard.press("Escape");
  await page.getByRole("heading", { name: "角色技能", exact: true }).click();
  await page.screenshot({
    path: info.outputPath("skills-management.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("failed source refresh keeps metadata and credentials; unsupported has no actions", async ({
  page,
}) => {
  const state = await fixture(page);
  const media = page
    .locator("article")
    .filter({ has: page.getByRole("heading", { name: "视频图片漫画聚合" }) });
  await expect(media).toContainText("待接入");
  await expect(
    media.getByRole("button", { name: /启用|停用|配置/ }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "刷新目录", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("目录刷新未完成");
  await expect(page.getByText("状态码：connection_failed")).toBeVisible();
  await expect(media).toBeVisible();
  await page.getByRole("button", { name: "编辑目录" }).click();
  await expect(page.getByLabel("固定内容 SHA256（可选）")).toHaveValue(
    "c".repeat(64),
  );
  await expect(page.getByLabel("目录访问令牌（可选）")).toHaveValue("");
  await expect(page.getByLabel("目录访问令牌（可选）")).toHaveAttribute(
    "placeholder",
    "已保存；留空保留",
  );
  expect(state.calls.some((row) => row.path === "skills/configure")).toBe(
    false,
  );
  expect(state.errors).toEqual([]);
});

test("unknown mutation does not auto retry and retains the original request id", async ({
  page,
}) => {
  const state = await fixture(page);
  const game = page
    .locator("article")
    .filter({ has: page.getByRole("heading", { name: "GSCore 游戏攻略" }) });
  state.failEnable();
  await game.getByRole("button", { name: "启用技能" }).click();
  await expect(page.getByText(/写入结果尚未确认/)).toBeVisible();
  expect(
    state.calls.filter((row) => row.operation === "skill.enable"),
  ).toHaveLength(1);
  await game.getByRole("button", { name: "启用技能" }).click();
  await expect(game.getByRole("button", { name: "停用技能" })).toBeVisible();
  const calls = state.calls.filter((row) => row.operation === "skill.enable");
  expect(calls).toHaveLength(2);
  expect(calls[0].client_request_id).toBe(calls[1].client_request_id);
  expect(state.errors).toEqual([]);
});
