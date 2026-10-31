import { test, expect, type Page } from "@playwright/test";

// UI fixture only. The backend suite separately exercises real HTTP and encrypted credentials.
async function fixture(page: Page) {
  const requests: Record<string, any>[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const profiles: Record<string, any> = Object.fromEntries(
    ["actor:a", "actor:b"].map((actor_id) => [
      actor_id,
      {
        id: `image-actor:${actor_id}`,
        actor_id,
        version: 1,
        state: "not_configured",
        workflow_id: null,
        workflow_version: null,
        character_prompt: "",
        defaults: {},
        bindings: {},
      },
    ]),
  );
  const target = (
    node: string,
    input: string,
    class_type = "AnimaSelector",
  ) => ({ node, class_type, input, mode: "replace" });
  const inspection = (id: string) => ({
    workflow_id: id,
    workflow_version: "a".repeat(64),
    format: "ui",
    state: "ready",
    nodes: [
      {
        node_id: "2",
        class_type: "AnimaSelector",
        title: "角色与穿搭",
        inputs: [
          { name: "character", type: "STRING", value: "", linked: false },
          { name: "outfit", type: "STRING", value: "", linked: false },
        ],
      },
      {
        node_id: "4",
        class_type: "AnimaSelector",
        title: "替代穿搭",
        inputs: [{ name: "outfit", type: "STRING", value: "", linked: false }],
      },
      {
        node_id: "8",
        class_type: "ResolutionMaster",
        title: "实际尺寸",
        inputs: [
          { name: "width", type: "INT", value: 768, linked: false },
          { name: "height", type: "INT", value: 1024, linked: false },
        ],
      },
    ],
    bindings: {
      character: target("2", "character"),
      outfit: target("2", "outfit"),
      width: target("8", "width", "ResolutionMaster"),
      height: target("8", "height", "ResolutionMaster"),
    },
    candidates: {
      character: [target("2", "character")],
      outfit: [target("2", "outfit"), target("4", "outfit")],
      width: [target("8", "width", "ResolutionMaster")],
      height: [target("8", "height", "ResolutionMaster")],
    },
    unresolved: [],
    protected_nodes: [{ node_id: "20", class_type: "LoraLoader" }],
    outputs: ["25"],
    conversion: {
      strategy: "node-aware",
      evidence: { resolution_order: "validated" },
    },
  });
  let revision = 0;
  let connectionVersion = 0;
  let connected = true;
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(
      "/api/v1/",
      "",
    );
    const body = route.request().postDataJSON() ?? {};
    const actor = body.actor_id ?? "actor:a";
    const profile = profiles[actor];
    let value: any;
    if (path === "session")
      value = {
        authenticated: true,
        csrf: "comfy-fixture",
        username: "preview",
        conversations: [],
        dialogue: { available: false, code: "not_configured", model: "" },
      };
    else if (path === "life/state")
      value = {
        available: true,
        code: "ready",
        peer: { configured: true, code: "ok", verified_at: null },
      };
    else if (path === "life/actors")
      value = {
        schema_version: 1,
        fictional: true,
        items: ["actor:a", "actor:b"].map((actor_id, index) => ({
          actor_id,
          label: index ? "珀羽" : "澄汐",
          actor_version: 1,
          world_id: "world:fixture",
          room_id: "room:fixture",
        })),
        next_after_actor_id: null,
      };
    else if (path === "life/snapshot")
      value = {
        schema_version: 1,
        fictional: true,
        actor_id: actor,
        actor_version: 1,
        world_id: "world:fixture",
        room_id: "room:fixture",
        timezone: "Asia/Shanghai",
        activity: "阅读",
        mood: "calm",
        outfit_ref: null,
        changed_at: 1791100000,
        observed_at: 1791104400,
        state_basis: "last_persisted",
      };
    else if (path === "life/diaries")
      value = { items: [], next_after: null, fictional: true };
    else if (path === "life/runtime/read")
      value = {
        schema_version: 2,
        actor_id: actor,
        resource: body.resource,
        request_id: "fixture:read",
        next_cursor: null,
        items:
          body.resource === "image_backend"
            ? [
                {
                  id: "backend",
                  version: connectionVersion,
                  state: connected ? "configured" : "unreachable",
                  base_url: "http://127.0.0.1:8188",
                  profile: "standard_sd",
                  checkpoint: null,
                  models: [],
                  checked_at: null,
                  error_code: null,
                },
              ]
            : body.resource === "activities"
              ? [
                  {
                    id: "activity:read",
                    version: 1,
                    actor_id: actor,
                    title: "午后阅读",
                    state: "running",
                    checkpoint: {},
                    sources: [],
                    result_refs: [],
                  },
                ]
              : [],
      };
    else if (path === "life/image-backend/status")
      value = {
        configured: revision > 0,
        credential_configured: revision > 0,
        revision,
      };
    else if (path === "life/image-backend/save") {
      requests.push({ path, ...body });
      revision++;
      connectionVersion++;
      value = {
        schema_version: 1,
        actor_id: actor,
        request_id: body.client_request_id,
        result: { version: connectionVersion, state: "configured" },
      };
    } else if (path === "life/image-backend/read") {
      if (!connected && body.resource === "workflows")
        return route.fulfill({
          status: 503,
          json: { code: "dependency_unavailable" },
        });
      const result =
        body.resource === "status"
          ? {
              id: "backend",
              version: connectionVersion,
              state: !connected
                ? "unreachable"
                : profile.workflow_id
                  ? "configured"
                  : "workflow_required",
              provider: "comfyui",
              base_url: "http://127.0.0.1:8188",
              enabled: true,
              workflow_id: profile.workflow_id,
              workflow_version: profile.workflow_version,
              checked_at: 1791100000,
              error_code: connected ? null : "dependency_unavailable",
              capabilities: {
                discovery: true,
                model_assistance: true,
                structured_prompt: true,
                reference: true,
              },
              models: [],
            }
          : body.resource === "actor"
            ? profile
            : body.resource === "workflows"
              ? {
                  source: "comfyui_userdata",
                  items: ["角色/澄汐/澄汐-分类测试版.json", "珀羽.json"].map(
                    (id) => ({ id, name: id, source: "comfyui_userdata" }),
                  ),
                }
              : inspection(body.workflow_id);
      value = {
        schema_version: 1,
        actor_id: actor,
        request_id: "fixture:comfy",
        result,
      };
    } else if (path === "life/image-backend/manage") {
      requests.push({ path, ...body });
      if (body.operation === "workflow.analyze") {
        if (body.value.goal === "未配置模型")
          return route.fulfill({
            status: 503,
            json: { code: "dependency_unavailable" },
          });
        value = {
          schema_version: 1,
          actor_id: actor,
          request_id: body.client_request_id,
          result: {
            ...inspection(
              body.value.workflow_id ?? "角色/澄汐/澄汐-分类测试版.json",
            ),
            model_receipt: { state: "succeeded" },
            selection_reason: "按竖幅生活照需求选择角色模板",
          },
        };
      } else {
        expect(body.expected_revision).toBe(profile.version);
        if (body.operation === "workflow.select")
          Object.assign(profile, {
            workflow_id: body.value.workflow_id,
            bindings: body.value.bindings,
            workflow_version: "a".repeat(64),
            state: "configured",
          });
        if (body.operation === "bindings.update")
          profile.bindings = body.value.bindings;
        if (body.operation === "actor.configure")
          Object.assign(profile, body.value);
        profile.version++;
        value = {
          schema_version: 1,
          actor_id: actor,
          request_id: body.client_request_id,
          result: profile,
        };
      }
    } else if (path === "life/image-backend/compile") {
      requests.push({ path, ...body });
      value = {
        schema_version: 1,
        actor_id: actor,
        request_id: "fixture:compile",
        result: {
          state: "ready",
          provider: "comfyui",
          workflow_id: profile.workflow_id,
          workflow_version: "a".repeat(64),
          graph: {
            "8": { class_type: "ResolutionMaster", inputs: body.parameters },
          },
          graph_sha256: "b".repeat(64),
          prompts: body.intent,
          dimensions: body.parameters,
          changes: [
            {
              semantic: "width",
              node_id: "8",
              input: "width",
              before: 768,
              after: body.parameters.width,
            },
          ],
          preserved_nodes: ["20"],
          warnings: [],
          model_receipt: body.assist_model ? { state: "succeeded" } : null,
        },
      };
    } else if (path === "life/runtime/manage") {
      requests.push({ path, ...body });
      value = {
        schema_version: 2,
        actor_id: actor,
        request_id: body.client_request_id,
        operation: body.operation,
        result: {
          id: body.value.id,
          version: 1,
          state: "queued",
          operation_ref: "job:fixture",
        },
      };
    } else
      return route.fulfill({ status: 503, json: { code: "not_configured" } });
    return route.fulfill({ json: value });
  });
  await page.goto("/#/roles/life");
  await page.getByRole("tab", { name: "衣柜与相册", exact: true }).click();
  await page
    .getByRole("button", { name: "图像供应商与工作流设置", exact: true })
    .click();
  const panel = page.getByRole("region", { name: "ComfyUI 图像管理" });
  await expect(panel.getByLabel("选择工作流")).toContainText(
    "澄汐-分类测试版.json",
  );
  return {
    panel,
    requests,
    errors,
    profiles,
    offline: () => {
      connected = false;
    },
  };
}

test("ComfyUI workflow discovery, node correction, role defaults and compile preview", async ({
  page,
}, info) => {
  const state = await fixture(page);
  const { panel } = state;
  await panel
    .getByLabel("访问凭据", { exact: true })
    .fill("synthetic-browser-secret");
  await panel.getByRole("button", { name: "保存连接并读取工作流" }).click();
  await expect(
    panel.getByText("连接配置已保存，已读取实际工作流目录。"),
  ).toBeVisible();
  await expect(panel.getByLabel("访问凭据", { exact: true })).toHaveValue("");
  await panel
    .getByLabel("选择工作流")
    .selectOption("角色/澄汐/澄汐-分类测试版.json");
  await expect(panel.getByLabel("角色形象节点", { exact: true })).toHaveValue(
    JSON.stringify(["2", "AnimaSelector", "character"]),
  );
  await panel
    .getByLabel("穿搭节点", { exact: true })
    .selectOption(JSON.stringify(["4", "AnimaSelector", "outfit"]));
  await panel.getByRole("button", { name: "将工作流绑定到当前角色" }).click();
  await expect(
    panel.getByText("此工作流与节点绑定已保存到当前角色。"),
  ).toBeVisible();
  await panel.getByLabel("固定角色形象").fill("橙色短发、蓝眼睛");
  await panel.getByLabel("默认穿搭", { exact: true }).fill("宽松家居服");
  await panel.getByLabel("默认姿态与动作").fill("窗边阅读");
  await panel.getByLabel("尺寸与方向").selectOption("landscape");
  await panel.getByRole("button", { name: "保存角色默认值" }).click();
  await expect(
    panel.getByText("当前角色的形象与拍摄默认值已保存。"),
  ).toBeVisible();
  await panel.getByRole("button", { name: "编译预览角色拍摄" }).click();
  await expect(panel.getByLabel("工作流编译结果")).toContainText("1024 × 768");
  await expect(panel.getByLabel("工作流编译结果")).toContainText("橙色短发");
  const selection = state.requests.find(
    (item) => item.operation === "workflow.select",
  )!;
  expect(selection.value.bindings.outfit.node).toBe("4");
  const config = state.requests.find(
    (item) => item.operation === "actor.configure",
  )!;
  expect(config.actor_id).toBe("actor:a");
  expect(config.value.defaults.width).toBe(1024);
  expect(config.value.defaults.height).toBe(768);
  expect(state.profiles["actor:b"].workflow_id).toBeNull();
  expect(
    state.requests.filter((item) => item.path === "life/runtime/manage"),
  ).toHaveLength(0);
  expect(state.errors).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("comfy-management.png"),
    fullPage: true,
  });
  await panel.evaluate((element) => element.scrollIntoView({ block: "start" }));
  await page.screenshot({ path: info.outputPath("comfy-connection.png") });
  await panel
    .getByLabel("角色形象节点", { exact: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath("comfy-bindings.png") });
  await panel.getByLabel("工作流编译结果").scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath("comfy-preview.png") });
});

test("model adaptation, structured generation and offline states remain visible", async ({
  page,
}) => {
  const state = await fixture(page);
  const { panel } = state;
  await panel.getByLabel("选图与适配需求").fill("竖幅角色生活照");
  await panel.getByRole("button", { name: "按需求帮我选工作流" }).click();
  await expect(panel.getByText("按竖幅生活照需求选择角色模板")).toBeVisible();
  await panel.getByRole("button", { name: "将工作流绑定到当前角色" }).click();
  await expect(
    panel.getByText("此工作流与节点绑定已保存到当前角色。"),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "生成图像设置", exact: true }).click();
  const studio = page
    .getByRole("heading", { name: "图像创作", exact: true })
    .locator("..");
  await studio.getByLabel("场景", { exact: true }).fill("午后窗边");
  await studio.getByLabel("姿态与动作", { exact: true }).fill("捧着书");
  await studio.getByLabel("镜头与构图", { exact: true }).fill("全身");
  await studio.getByLabel("关联日程或活动").selectOption("activity:read");
  await studio.getByLabel("尺寸与方向").selectOption("wide");
  await studio.getByRole("button", { name: "交换横竖方向" }).click();
  await studio.getByRole("button", { name: "预览本次提示词与节点" }).click();
  await expect(studio.getByLabel("工作流编译结果")).toContainText("768 × 1344");
  await studio.getByRole("button", { name: "提交创作", exact: true }).click();
  const generation = state.requests.find(
    (item) => item.operation === "image.request",
  )!;
  expect(generation.value.activity_id).toBe("activity:read");
  expect(generation.value.intent.pose).toBe("捧着书");
  expect(generation.value.intent.camera).toBe("全身");
  expect(generation.value.parameters).toMatchObject({
    width: 768,
    height: 1344,
  });
  await page.keyboard.press("Escape");
  await page
    .getByRole("button", { name: "图像供应商与工作流设置", exact: true })
    .click();
  await panel.getByLabel("选图与适配需求").fill("未配置模型");
  await panel.getByRole("button", { name: "模型辅助适配所选工作流" }).click();
  await expect(panel.getByRole("alert")).toContainText(
    "dependency_unavailable",
  );
  state.offline();
  await panel.getByRole("button", { name: "检查实际连接" }).click();
  await expect(panel).toContainText("连接状态：无法连接");
  expect(state.errors).toEqual([]);
});

test("changing actors clears credentials, workflow and pending preview", async ({
  page,
}) => {
  const state = await fixture(page);
  await state.panel
    .getByLabel("访问凭据", { exact: true })
    .fill("synthetic-unsaved-token");
  await state.panel.getByLabel("固定角色形象").fill("澄汐固定形象");
  await page.keyboard.press("Escape");
  await page.getByLabel("选择角色").selectOption("actor:b");
  await page.getByRole("tab", { name: "衣柜与相册", exact: true }).click();
  await page
    .getByRole("button", { name: "图像供应商与工作流设置", exact: true })
    .click();
  const panel = page.getByRole("region", { name: "ComfyUI 图像管理" });
  await expect(panel.getByLabel("固定角色形象")).toHaveValue("");
  await expect(panel.getByLabel("访问凭据", { exact: true })).toHaveValue("");
  await expect(panel.getByLabel("选择工作流")).toHaveValue("");
  await expect(panel.getByLabel("工作流编译结果")).toHaveCount(0);
  expect(state.errors).toEqual([]);
});
