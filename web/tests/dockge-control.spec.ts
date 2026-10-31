import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import type {
  ControlStack,
  ControlView,
  Operation,
  Plan,
} from "../src/features/infrastructure/dockge/types";
import type {
  ContainerDetail,
  ContainerList,
  InfrastructureView,
} from "../src/features/infrastructure/types";

const fullId = "a".repeat(64);
const now = () => new Date().toISOString();
const maintenance = { required: false, owner: null, reason_codes: [] };
const allowed = Object.fromEntries(
  ["start", "stop", "restart", "update", "configure", "restore"].map((key) => [
    key,
    { enabled: true, reason_codes: [] },
  ]),
);

// LAN HTTP exposes getRandomValues but does not expose randomUUID.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(crypto, "randomUUID", {
      configurable: true,
      value: undefined,
    });
  });
});

async function failNextRequestId(page: Page) {
  await page.evaluate(() => {
    const original = crypto.getRandomValues.bind(crypto);
    Object.defineProperty(crypto, "getRandomValues", {
      configurable: true,
      value: () => {
        Object.defineProperty(crypto, "getRandomValues", {
          configurable: true,
          value: original,
        });
        throw new Error("synthetic request preparation failure");
      },
    });
  });
}

// These projections test browser race/unknown/layout paths. The real HTTP test
// below separately exercises the platform ledger, session and synthetic executor.
async function installProjection(
  page: Page,
  options: {
    unknown?: boolean;
    blockers?: boolean;
    protected?: boolean;
    partial?: boolean;
    dense?: boolean;
    executionActive?: boolean | null;
    expiresIn?: number;
    unreadableStack?: boolean;
  } = {},
) {
  const node = {
    id: "fixture-nas",
    label: "合成 NAS",
    kind: "synology",
    enabled: true,
    connection_revision: 1,
    connection_state: "current",
    observed_at: now(),
    last_attempt_at: now(),
    refresh: null,
    capabilities: ["docker.inventory"],
    last_test: null,
  };
  const observation = {
    status: "ok",
    freshness: "current" as const,
    observed_at: now(),
    last_attempt_at: now(),
    age_seconds: 0,
    reason_codes: [],
    capabilities: [],
    completeness: null,
  };
  const container = {
    id: fullId,
    name: "synthetic-control-app",
    state: "running",
    health: "none",
    availability: "ok",
    image_reference: "synthetic:v1",
    compose: { project: "synthetic-example", service: "app" },
    stats: null,
  };
  const readonlyView: InfrastructureView = {
    configured: true,
    revision: 1,
    max_nodes: 16,
    catalog: null,
    nodes: [node],
  };
  const inventory: ContainerList = {
    node,
    containers: [container],
    total: 1,
    inventory_total: 1,
    page: 0,
    page_size: 100,
    has_more: false,
    applications: [],
    inventory: observation,
    metrics: observation,
  };
  const detail: ContainerDetail = {
    node,
    container: {
      ...container,
      image_id: null,
      created_at: now(),
      started_at: now(),
      network_mode: "bridge",
      ports: [],
      mounts: Array.from({ length: 40 }, (_, i) => ({
        kind: "bind",
        source: `/srv/synthetic/data-${i}`,
        target: `/data/${i}`,
        read_only: false,
      })),
    },
    stats: null,
    inventory: observation,
    metrics: observation,
  };
  const state = {
    revision: "sha256:initial",
    recovered: false,
    submits: [] as Record<string, unknown>[],
    recoveries: 0,
    recoveredIds: [] as string[],
    detailState: "failed" as "queued" | "running" | "failed",
    detailRefreshes: 0,
  };
  const protection = options.protected
    ? {
        required: true,
        owner: "resident-maintenance",
        reason_codes: ["infrastructure_maintenance_required"],
      }
    : maintenance;
  const operations = new Map<string, Operation>();
  const plans = new Map<string, Plan>();
  const stack = (): ControlStack => ({
    stack_id: "stack:synthetic-example",
    display_name: "synthetic-example",
    project_name: "synthetic-example",
    revision: state.revision,
    ownership: "platform",
    exists: true,
    runtime_state: "running",
    health_state: "none",
    containers: [fullId],
    maintenance: protection,
    allowed_actions: allowed,
  });
  const operation = (
    body: Record<string, unknown>,
    kind: string,
    result: Operation["result"],
  ): Operation => ({
    operation_id: `infra-control:${body.client_request_id}`,
    client_request_id: String(body.client_request_id),
    kind,
    node_id: node.id,
    connection_revision: 1,
    stack_id: (body.stack_id as string) ?? stack().stack_id,
    container_id: (body.container_id as string) ?? null,
    action: String(body.action ?? kind),
    state: "observed",
    stage: "finished",
    acceptance: kind === "plan" ? "not_sent" : "submitted",
    created_at: now(),
    updated_at: now(),
    finished_at: now(),
    settled: true,
    reason_codes: [],
    can_refresh: false,
    result,
  });
  const runtime = {
    command_state: "succeeded",
    execution_active: false,
    runtime_state: "running",
    health_state: "none",
    containers: [{ container_id: fullId, state: "running", health: "none" }],
    version_id: "version-new",
    node_operation_id: "synthetic-node-operation",
  };
  for (let i = 0; i < 24; i++) {
    const item = operation(
      { client_request_id: `history-${i}`, action: "stack.restart" },
      "action",
      runtime,
    );
    operations.set(item.client_request_id, item);
  }
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(
      "/api/v1/",
      "",
    );
    const body =
      route.request().method() === "POST" ? route.request().postDataJSON() : {};
    const reply = (json: unknown, status = 200) =>
      route.fulfill({ json, status });
    if (path === "session")
      return reply({
        authenticated: true,
        csrf: "synthetic-dockge-csrf",
        username: "synthetic-admin",
      });
    if (path === "infrastructure/view") return reply(readonlyView);
    if (path === "infrastructure/containers") return reply(inventory);
    if (path === "infrastructure/container") return reply(detail);
    if (
      path === "infrastructure/logs" ||
      path === "infrastructure/logs/refresh"
    )
      return reply({
        state: "observed",
        observed_at: now(),
        reason_codes: [],
        data: {
          stdout: "合成容器日志\n".repeat(200),
          stderr: "",
          truncated: true,
        },
      });
    if (path === "infrastructure/control/view") {
      const stacks = options.dense
        ? Array.from({ length: 65 }, (_, index) => ({
            ...stack(),
            stack_id: `stack:dense-${index}`,
            display_name: `synthetic-app-${index}-${"long-name-".repeat(10)}`,
          }))
        : [stack()];
      if (options.unreadableStack)
        stacks.push({
          ...stack(),
          stack_id: "stack:unreadable",
          display_name: "synthetic-protected",
          project_name: "synthetic-protected",
          maintenance: {
            required: true,
            owner: "resident-maintenance",
            reason_codes: ["infrastructure_maintenance_required"],
          },
        });
      const filtered = stacks.filter(
        (item) => !body.query || item.display_name.includes(body.query),
      );
      const start = (body.page ?? 0) * 50;
      const value: ControlView = {
        configured: true,
        node_id: node.id,
        connection_revision: 1,
        capabilities: {
          available: true,
          reason_codes: [],
          create: { enabled: true, reason_codes: [] },
        },
        stacks: filtered.slice(start, start + 50),
        containers: [
          {
            container_id: fullId,
            stack_id: stack().stack_id,
            maintenance: protection,
            allowed_actions: allowed,
          },
        ],
        total: filtered.length,
        page: body.page ?? 0,
        page_size: 50,
        has_more: start + 50 < filtered.length,
        observation,
        refresh: null,
      };
      return reply(value);
    }
    if (path === "infrastructure/control/detail") {
      if (body.container_id)
        return reply({
          node_id: node.id,
          connection_revision: 1,
          container: {
            container_id: fullId,
            stack_id: stack().stack_id,
            maintenance: protection,
            allowed_actions: allowed,
          },
          observation,
          refresh: null,
        });
      if (body.stack_id === "stack:unreadable") {
        if (body.refresh) state.detailRefreshes++;
        const failed = state.detailState === "failed";
        return reply({
          node_id: node.id,
          connection_revision: 1,
          stack: null,
          compose_content: null,
          versions: [],
          observation: {
            ...observation,
            status: failed ? "unavailable" : "unknown",
            freshness: "missing",
          },
          refresh: {
            ...operation({ ...body, client_request_id: "detail-read" }, "detail", null),
            state: state.detailState,
            settled: failed,
            reason_codes: failed ? ["compose_reference_outside_stack"] : [],
          },
        });
      }
      return reply({
        node_id: node.id,
        connection_revision: 1,
        stack: { ...stack(), stack_id: body.stack_id },
        compose_content:
          "services:\n  app:\n    image: synthetic:v1\n    environment:\n      TOKEN: __opaque_secret_reference__\n",
        versions: [
          {
            version_id: "version-original",
            label: "原配置",
            created_at: now(),
            current: false,
            restorable: true,
            reason_codes: [],
          },
        ],
        observation,
        refresh: null,
      });
    }
    if (path === "infrastructure/control/plan") {
      state.submits.push(body);
      const plan: Plan = {
        plan_id: `plan:${body.client_request_id}`,
        node_id: node.id,
        connection_revision: 1,
        stack_id: body.stack_id ?? stack().stack_id,
        project_name: body.project_name ?? stack().project_name,
        action: body.action,
        expected_revision: body.expected_revision ?? "",
        expires_at: new Date(
          Date.now() + (options.expiresIn ?? 600000),
        ).toISOString(),
        executable: !options.blockers,
        changes: ["app 镜像 synthetic:v1 → synthetic:v2"],
        diff: "- image: synthetic:v1\n+ image: synthetic:v2",
        images: ["synthetic:v2"],
        ports: ["127.0.0.1:18080 → 8080/tcp"],
        mounts: Array.from(
          { length: options.dense ? 40 : 1 },
          (_, i) => `命名卷 synthetic-data-${i} → /data/${i}`,
        ),
        warnings:
          body.action === "restore"
            ? ["configuration_restore_not_data_rollback"]
            : body.action === "update"
              ? ["image_update_not_data_rollback"]
              : [],
        blockers: options.blockers
          ? ["published_port_in_use", "protected_bind_root"]
          : [],
        maintenance,
      };
      plans.set(plan.plan_id, plan);
      const item = operation(body, "plan", plan);
      operations.set(item.client_request_id, item);
      return reply({ operation: item });
    }
    if (
      path === "infrastructure/control/action" ||
      path === "infrastructure/control/execute"
    ) {
      state.submits.push(body);
      const kind = path.endsWith("execute") ? "execute" : "action";
      const item = operation(
        { ...body, action: body.action ?? plans.get(body.plan_id)?.action },
        kind,
        runtime,
      );
      if (options.unknown && !state.recovered)
        Object.assign(item, {
          state: "unknown",
          stage: "reconciling",
          acceptance: "unknown",
          settled: true,
          can_refresh: true,
          result: null,
        });
      if (options.partial && kind === "execute")
        Object.assign(item, {
          state: "partial",
          reason_codes: ["pull_failed"],
          can_refresh:
            options.executionActive !== undefined &&
            options.executionActive !== false,
          result: {
            ...runtime,
            execution_active:
              options.executionActive === undefined
                ? false
                : options.executionActive,
            runtime_state: "partial",
            health_state: "unhealthy",
          },
        });
      operations.set(item.client_request_id, item);
      if (options.unknown && !state.recovered)
        return route.abort("connectionreset");
      return reply({ operation: item });
    }
    if (path === "infrastructure/control/operation") {
      const item =
        operations.get(body.client_request_id) ??
        [...operations.values()].find(
          (op) => op.operation_id === body.operation_id,
        );
      if (!item)
        return reply({ code: "infrastructure_operation_not_found" }, 404);
      if (body.refresh && (item.state === "unknown" || item.can_refresh)) {
        state.recoveries++;
        state.recoveredIds.push(item.client_request_id);
        state.recovered = true;
        Object.assign(item, {
          state: "observed",
          stage: "finished",
          acceptance: "submitted",
          settled: true,
          can_refresh: false,
          result: runtime,
        });
      }
      return reply({ operation: item });
    }
    if (path === "infrastructure/control/operations") {
      const offset = Number(body.cursor ?? 0);
      const all = [...operations.values()];
      return reply({
        items: all.slice(offset, offset + 10),
        page: {
          returned: Math.min(10, all.length - offset),
          has_more: offset + 10 < all.length,
          next_cursor: offset + 10 < all.length ? String(offset + 10) : null,
        },
      });
    }
    return reply({ configured: false });
  });
  return state;
}

async function openStacks(page: Page) {
  await page.goto("/#/system/containers");
  await page.getByRole("button", { name: "Compose 栈", exact: true }).click();
  await expect(
    page.getByRole("complementary", { name: "Compose 栈详情", exact: true }),
  ).toBeVisible();
}

test("projection: full container identity, double click, unknown response and reload recover only the original operation", async ({
  page,
}) => {
  const state = await installProjection(page, { unknown: true });
  await page.goto("/#/system/containers");
  const restart = page.getByRole("button", { name: "重启容器", exact: true });
  await expect(restart).toBeEnabled();
  await restart.evaluate((button: HTMLButtonElement) => {
    button.click();
    button.click();
  });
  await expect(
    page.getByRole("heading", { name: "结果未知", exact: true }),
  ).toBeVisible();
  await expect(restart).toBeDisabled();
  expect(state.submits).toHaveLength(1);
  expect(state.submits[0].container_id).toBe(fullId);
  const original = state.submits[0].client_request_id;
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "结果未知", exact: true }),
  ).toBeVisible();
  await expect(restart).toBeDisabled();
  await page.getByRole("button", { name: "查询原操作", exact: true }).click();
  await expect(page.getByLabel("操作实际结果")).toContainText(
    "实际运行 · 运行中",
  );
  await expect(page.getByLabel("操作实际结果")).toContainText(
    "健康检查 · 未配置检查",
  );
  await expect(restart).toBeEnabled();
  expect(state.recoveries).toBe(1);
  expect(state.recoveredIds).toEqual([original]);
  expect(state.submits).toHaveLength(1);
  expect(state.submits[0].client_request_id).toBe(original);
});

test("projection: request preparation failures leave action, plan and execute available to retry", async ({
  page,
}) => {
  const errors: Error[] = [];
  page.on("pageerror", (error) => errors.push(error));
  const state = await installProjection(page);
  await page.goto("/#/system/containers");
  const restart = page.getByRole("button", { name: "重启容器", exact: true });
  await expect(restart).toBeEnabled();
  await failNextRequestId(page);
  await restart.click();
  await expect(page.getByRole("alert")).toContainText(
    "操作请求尚未发出，请重试。",
  );
  await expect(restart).toBeEnabled();
  expect(state.submits).toHaveLength(0);
  await restart.click();
  await expect(page.getByLabel("操作实际结果")).toContainText(
    "实际运行 · 运行中",
  );

  await page.getByRole("button", { name: "Compose 栈", exact: true }).click();
  await page.getByRole("button", { name: "配置部署", exact: true }).click();
  const preview = page.getByRole("button", {
    name: "预览修改部署",
    exact: true,
  });
  await failNextRequestId(page);
  await preview.click();
  await expect(page.getByRole("alert")).toContainText(
    "操作请求尚未发出，请重试。",
  );
  await expect(preview).toBeEnabled();
  expect(state.submits).toHaveLength(1);
  await preview.click();
  const execute = page.getByRole("button", { name: "执行此计划", exact: true });
  await expect(execute).toBeEnabled();
  await failNextRequestId(page);
  await execute.click();
  await expect(page.getByRole("alert")).toContainText(
    "操作请求尚未发出，请重试。",
  );
  await expect(execute).toBeEnabled();
  expect(state.submits).toHaveLength(2);
  await execute.evaluate((button: HTMLButtonElement) => {
    button.click();
    button.click();
  });
  await expect(page.getByLabel("操作实际结果")).toContainText(
    "实际运行 · 运行中",
  );
  await expect(
    page.getByRole("button", { name: "此计划已提交", exact: true }),
  ).toBeDisabled();
  expect(state.submits).toHaveLength(3);
  expect(errors).toEqual([]);
});

test("projection: null stack readbacks show pending and failed states without leaking another target", async ({
  page,
}) => {
  const state = await installProjection(page, { unreadableStack: true });
  await openStacks(page);
  const inspector = page.getByRole("complementary", {
    name: "Compose 栈详情",
    exact: true,
  });
  await expect(
    inspector.getByRole("button", { name: "启动应用", exact: true }),
  ).toBeEnabled();
  state.detailState = "queued";
  await page
    .getByRole("button", { name: "查看应用 synthetic-protected", exact: true })
    .click();
  await expect(inspector).toContainText("等待后台读取配置与运行状态");
  await expect(inspector).toContainText("专用维护 · resident-maintenance");
  await expect(
    inspector.getByRole("heading", { name: "synthetic-example", exact: true }),
  ).toHaveCount(0);
  await expect(
    inspector.getByRole("button", { name: "启动应用", exact: true }),
  ).toHaveCount(0);
  state.detailState = "running";
  await inspector.getByRole("button", { name: "刷新应用详情" }).click();
  await expect.poll(() => state.detailRefreshes).toBe(1);
  await expect(inspector).toContainText("等待后台读取配置与运行状态");
  state.detailState = "failed";
  await inspector.getByRole("button", { name: "刷新应用详情" }).click();
  await expect(inspector.getByRole("alert")).toContainText(
    "应用详情无法读取，请刷新重试。",
  );
  await expect(inspector).toContainText("Compose 配置引用了栈目录外的文件");
  await expect(inspector).not.toContainText("等待后台读取配置与运行状态");
  await expect(
    inspector.getByRole("heading", {
      name: "synthetic-protected",
      exact: true,
    }),
  ).toBeVisible();
  await inspector
    .getByRole("button", { name: "配置部署", exact: true })
    .click();
  await expect(
    inspector.getByRole("button", { name: "预览修改部署", exact: true }),
  ).toHaveCount(0);
  expect(state.submits).toHaveLength(0);

  let release: () => void = () => {};
  const waiting = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(
    "**/api/v1/infrastructure/control/detail",
    async (route) => {
      if (route.request().postDataJSON().stack_id === "stack:synthetic-example")
        await waiting;
      await route.fallback();
    },
  );
  await page
    .getByRole("button", { name: "查看应用 synthetic-example", exact: true })
    .click();
  await expect(inspector).toContainText("等待后台读取配置与运行状态");
  await expect(inspector).not.toContainText("resident-maintenance");
  await expect(inspector).not.toContainText("synthetic-protected");
  await expect(inspector).not.toContainText("栈目录外的文件");
  await expect(
    inspector.getByRole("button", { name: "启动应用", exact: true }),
  ).toHaveCount(0);
  release();
  await expect(
    inspector.getByRole("button", { name: "启动应用", exact: true }),
  ).toBeEnabled();
  await expect(inspector).not.toContainText("resident-maintenance");
  await expect(inspector.getByRole("alert")).toHaveCount(0);
});

test("projection: concrete config preview invalidates on draft edits and external version changes", async ({
  page,
}) => {
  const state = await installProjection(page);
  await openStacks(page);
  await page.getByRole("button", { name: "配置部署", exact: true }).click();
  const content = page.getByRole("textbox", {
    name: "Compose 配置",
    exact: true,
  });
  await content.fill("services:\n  app:\n    image: synthetic:v2\n");
  await page.getByRole("button", { name: "预览修改部署", exact: true }).click();
  const preview = page.getByRole("region", { name: "部署计划预览" });
  await expect(preview).toContainText("127.0.0.1:18080");
  await expect(preview).toContainText("synthetic-data-0");
  const execute = page.getByRole("button", { name: "执行此计划", exact: true });
  await expect(execute).toBeEnabled();
  await content.fill("services:\n  app:\n    image: synthetic:v3\n");
  await expect(execute).toBeDisabled();
  expect(state.submits).toHaveLength(1);
  state.revision = "sha256:external-dockge-edit";
  await expect(
    page.getByText("当前配置版本已变化，草稿仍绑定原版本", { exact: false }),
  ).toBeVisible({ timeout: 8000 });
  await expect(content).toHaveValue(
    "services:\n  app:\n    image: synthetic:v3\n",
  );
  await page.getByRole("button", { name: "载入当前配置", exact: true }).click();
  await expect(content).toHaveValue(/synthetic:v1/);
  await page.getByRole("button", { name: "预览修改部署", exact: true }).click();
  await expect(execute).toBeEnabled();
  await execute.evaluate((button: HTMLButtonElement) => {
    button.click();
    button.click();
  });
  await expect(
    page.getByRole("button", { name: "此计划已提交", exact: true }),
  ).toBeDisabled();
  expect(state.submits.filter((item) => "plan_id" in item)).toHaveLength(1);
  expect(state.submits[1].expected_revision).toBe(state.revision);
});

test("projection: port conflict blocks deployment; maintenance prevents generic stop and configure", async ({
  page,
}) => {
  const state = await installProjection(page, { blockers: true });
  await openStacks(page);
  await page.getByRole("button", { name: "配置部署", exact: true }).click();
  await page.getByRole("button", { name: "预览修改部署", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "发布端口已被其他服务占用",
  );
  await expect(page.getByRole("alert")).toContainText(
    "挂载涉及受保护服务的数据目录",
  );
  await expect(page.getByRole("alert")).not.toContainText(
    "published_port_in_use",
  );
  await expect(
    page.getByRole("button", { name: "执行此计划", exact: true }),
  ).toBeDisabled();
  expect(state.submits).toHaveLength(1);
  await page.unroute("**/api/v1/**");
  await installProjection(page, { protected: true });
  await page.reload();
  await expect(
    page.getByText("专用维护 · resident-maintenance", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "停止容器", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Compose 栈", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "停止应用", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "配置部署", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "预览修改部署", exact: true }),
  ).toBeDisabled();
});

test("projection: update, config restore, partial runtime and health remain distinct", async ({
  page,
}) => {
  const state = await installProjection(page, { partial: true });
  await openStacks(page);
  await page.getByRole("button", { name: "镜像更新", exact: true }).click();
  await page.getByRole("button", { name: "预览更新镜像", exact: true }).click();
  await page.getByRole("button", { name: "执行此计划", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "部分完成", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("操作实际结果")).toContainText(
    "实际运行 · 部分运行",
  );
  await expect(page.getByLabel("操作实际结果")).toContainText(
    "健康检查 · 检查异常",
  );
  await expect(
    page.getByText("镜像拉取失败，查看实际运行读回", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "配置版本", exact: true }).click();
  await page
    .getByRole("button", { name: "选择此版本预览恢复", exact: true })
    .click();
  await page
    .getByRole("button", { name: "预览恢复配置版本", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "部署计划预览" }),
  ).toBeVisible();
  expect(state.submits.at(-1)?.restore_version_id).toBe("version-original");
  await expect(
    page.getByRole("region", { name: "部署计划预览" }),
  ).toContainText("恢复配置和运行版本，不回滚已发生的数据写入");
});

test("projection: plan expiry disables execute before the next inventory poll", async ({
  page,
}) => {
  await installProjection(page, { expiresIn: 1000 });
  await openStacks(page);
  await page.getByRole("button", { name: "配置部署", exact: true }).click();
  await page.getByRole("button", { name: "预览修改部署", exact: true }).click();
  const execute = page.getByRole("button", { name: "执行此计划", exact: true });
  await expect(execute).toBeEnabled();
  await expect(execute).toBeDisabled({ timeout: 2500 });
  await expect(
    page.getByRole("region", { name: "部署计划预览" }),
  ).toContainText("计划已经过期");
});

test("projection: bounded stacks, config preview and operation pagination stay usable at desktop and narrow widths", async ({
  page,
}, testInfo) => {
  await installProjection(page, { dense: true });
  await openStacks(page);
  const widths = testInfo.project.name === "mobile" ? [390] : [1440, 1057];
  for (const width of widths) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    const content = page.getByRole("group", {
      name: "应用清单内容",
      exact: true,
    });
    expect(
      await content.evaluate(
        (element) =>
          element.scrollHeight > element.clientHeight &&
          getComputedStyle(element).overflowY === "auto",
      ),
    ).toBe(true);
    await page.getByRole("button", { name: "配置部署", exact: true }).click();
    await page
      .getByRole("button", { name: "预览修改部署", exact: true })
      .click();
    await expect(
      page.getByRole("region", { name: "部署计划预览" }),
    ).toBeVisible();
    const detail = page.getByRole("group", {
      name: "应用详情内容",
      exact: true,
    });
    expect(
      await detail.evaluate(
        (element) =>
          element.scrollHeight > element.clientHeight &&
          getComputedStyle(element).overflowY === "auto",
      ),
    ).toBe(true);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    expect(
      (await new AxeBuilder({ page }).include(".infra-workspace").analyze())
        .violations,
    ).toEqual([]);
    await page
      .getByRole("heading", {
        name: "具体变更预览 · synthetic-example",
        exact: true,
      })
      .scrollIntoViewIfNeeded();
    await page.screenshot({
      path: testInfo.outputPath(`dockge-preview-${width}.png`),
      fullPage: true,
    });
    await page.getByRole("button", { name: "操作记录", exact: true }).click();
    const history = page.getByRole("region", { name: "操作记录", exact: true });
    await expect(history.getByRole("button", { name: /重启应用/ })).toHaveCount(
      10,
    );
    await history
      .getByRole("button", { name: "下一页记录", exact: true })
      .click();
    await expect(history).toContainText("第 2 页");
    await page.evaluate(() =>
      document.documentElement.setAttribute("data-theme", "dark"),
    );
    expect(
      (await new AxeBuilder({ page }).include(".infra-workspace").analyze())
        .violations,
    ).toEqual([]);
    await page.screenshot({
      path: testInfo.outputPath(`dockge-history-dark-${width}.png`),
      fullPage: true,
    });
  }
});

test("projection: create previews a concrete deployment and executes only its plan identity", async ({
  page,
}) => {
  const state = await installProjection(page);
  await openStacks(page);
  await page.getByRole("button", { name: "创建应用", exact: true }).click();
  const preview = page.getByRole("button", {
    name: "预览创建部署",
    exact: true,
  });
  await expect(preview).toBeDisabled();
  await page
    .getByRole("textbox", { name: "应用项目名", exact: true })
    .fill("synthetic-new-app");
  await page
    .getByRole("textbox", { name: "Compose 配置", exact: true })
    .fill("services:\n  app:\n    image: synthetic:v2\n");
  await preview.click();
  await expect(
    page.getByRole("region", { name: "部署计划预览" }),
  ).toContainText("synthetic-new-app");
  await page.getByRole("button", { name: "执行此计划", exact: true }).click();
  await expect(page.getByLabel("操作实际结果")).toContainText(
    "实际运行 · 运行中",
  );
  expect(state.submits).toHaveLength(2);
  expect(state.submits[0].project_name).toBe("synthetic-new-app");
  expect(state.submits[0]).not.toHaveProperty("stack_id");
  expect(state.submits[0]).not.toHaveProperty("expected_revision");
  expect(Object.keys(state.submits[1]).sort()).toEqual([
    "client_request_id",
    "expected_connection_revision",
    "node_id",
    "plan_id",
  ]);
});

for (const active of [true, null]) {
  test(`projection: partial result with execution_active=${active} only recovers the original command`, async ({
    page,
  }) => {
    const state = await installProjection(page, {
      partial: true,
      executionActive: active,
    });
    await openStacks(page);
    await page.getByRole("button", { name: "镜像更新", exact: true }).click();
    await page
      .getByRole("button", { name: "预览更新镜像", exact: true })
      .click();
    await page.getByRole("button", { name: "执行此计划", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "部分完成", exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "运行详情", exact: true }).click();
    const stop = page.getByRole("button", { name: "停止应用", exact: true });
    await expect(stop).toBeDisabled();
    await page.getByRole("button", { name: "查询原操作", exact: true }).click();
    await expect(stop).toBeEnabled();
    expect(state.submits).toHaveLength(2);
    expect(state.recoveries).toBe(1);
  });
}

test("real HTTP: container and stack actions, configure, saved version restore and protected maintenance", async ({
  page,
}, testInfo) => {
  test.setTimeout(120000);
  const pageErrors: Error[] = [];
  page.on("pageerror", (error) => pageErrors.push(error));
  await page.goto("/#/system/containers");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Docker 容器", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => ({
      randomUUID: typeof crypto.randomUUID,
      getRandomValues: typeof crypto.getRandomValues,
    })),
  ).toEqual({ randomUUID: "undefined", getRandomValues: "function" });
  await page.getByLabel("搜索容器", { exact: true }).fill("fixture-web");
  await page
    .getByRole("button", { name: "查看容器 fixture-web", exact: true })
    .click();

  const calls: { endpoint: string; clientId: string }[] = [];
  const queriedIds: string[] = [];
  const droppedIds: string[] = [];
  page.on("request", (request) => {
    const endpoint = request
      .url()
      .match(/\/infrastructure\/control\/(\w+)$/)?.[1];
    if (!endpoint || request.method() !== "POST") return;
    const body = request.postDataJSON();
    if (endpoint === "operation" && body.client_request_id)
      queriedIds.push(body.client_request_id);
    if (["action", "plan", "execute"].includes(endpoint))
      calls.push({ endpoint, clientId: body.client_request_id });
  });
  async function clickControl(
    button: string,
    endpoint: "action" | "plan" | "execute",
    loseResponse = false,
  ) {
    if (loseResponse) {
      // Forward the unchanged request to the real platform, then lose only its
      // response. Recovery must read the durable ledger using the original ID.
      await page.route(
        `**/api/v1/infrastructure/control/${endpoint}`,
        async (route) => {
          const response = await route.fetch();
          expect(response.ok()).toBe(true);
          droppedIds.push(route.request().postDataJSON().client_request_id);
          await route.abort("connectionreset");
        },
        { times: 1 },
      );
    }
    const request = page.waitForRequest((value) =>
      value.url().endsWith(`/infrastructure/control/${endpoint}`),
    );
    await page
      .getByRole("button", { name: button, exact: true })
      .evaluate((element: HTMLButtonElement) => {
        element.click();
        element.click();
      });
    const sent = (await request).postDataJSON();
    expect(sent.client_request_id).toMatch(
      /^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/,
    );
    return sent;
  }
  async function submit(
    button: string,
    endpoint: "action" | "execute",
    expected: string,
    loseResponse = false,
  ) {
    const sent = await clickControl(button, endpoint, loseResponse);
    const receipt = page
      .getByRole("region", { name: "当前操作", exact: true })
      .filter({ hasText: sent.client_request_id });
    await expect(receipt).toContainText(expected, { timeout: 20000 });
    await expect(receipt).toContainText("命令结果 · 命令已结束");
    return sent;
  }
  const restarted = await submit(
    "重启容器",
    "action",
    "实际运行 · 运行中",
    true,
  );
  expect(restarted.container_id).toBe("1".padStart(64, "0"));
  await page.getByRole("button", { name: "Compose 栈", exact: true }).click();
  await page
    .getByRole("button", { name: "查看应用 fixture-web", exact: true })
    .click();
  await submit("停止应用", "action", "实际运行 · 已停止");
  await submit("启动应用", "action", "实际运行 · 运行中");
  await submit("重启应用", "action", "实际运行 · 运行中");
  await page.getByRole("button", { name: "配置部署", exact: true }).click();
  const editor = page.getByRole("textbox", {
    name: "Compose 配置",
    exact: true,
  });
  await expect(editor).toHaveValue(/tianshu.secret:fixture/);
  const original = await editor.inputValue();
  await editor.fill(
    `${original.replace("synthetic:v1", "synthetic:v2")}\n# browser-${testInfo.project.name}\n`,
  );
  await clickControl("预览修改部署", "plan", true);
  const preview = page.getByRole("region", {
    name: "部署计划预览",
    exact: true,
  });
  await expect(preview).toContainText("fixture-data", { timeout: 15000 });
  await expect(preview).toContainText("18080");
  const executed = await submit(
    "执行此计划",
    "execute",
    "实际运行 · 运行中",
    true,
  );
  const session = await (await page.request.get("/api/v1/session")).json();
  const read = await page.request.post(
    "/api/v1/infrastructure/control/operation",
    {
      data: { client_request_id: executed.client_request_id },
      headers: {
        "X-CSRF-Token": session.csrf,
        Origin: "http://127.0.0.1:4837",
      },
    },
  );
  expect(read.status()).toBe(200);
  const saved = (await read.json()).operation;
  expect(saved.state).toBe("observed");
  expect(saved.result.execution_active).toBe(false);
  expect(saved.result.health_state).toBe("none");
  expect(saved.result.version_id).toBeTruthy();
  await page.getByRole("button", { name: "刷新应用详情", exact: true }).click();
  await page.getByRole("button", { name: "配置版本", exact: true }).click();
  const version = page
    .locator(".infra-card")
    .filter({ hasText: saved.result.version_id });
  await expect(version).toBeVisible({ timeout: 15000 });
  await version
    .getByRole("button", { name: "选择此版本预览恢复", exact: true })
    .click();
  await clickControl("预览恢复配置版本", "plan");
  await expect(preview).toBeVisible({ timeout: 15000 });
  const restored = await submit("执行此计划", "execute", "实际运行 · 运行中");
  await page.getByRole("button", { name: "刷新应用详情", exact: true }).click();
  await page.getByRole("button", { name: "配置部署", exact: true }).click();
  await expect(editor).toHaveValue(original, { timeout: 15000 });
  await page.getByRole("button", { name: "操作记录", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "操作记录", exact: true }),
  ).toContainText("恢复配置版本");
  await page.screenshot({
    path: testInfo.outputPath("dockge-real-http-history.png"),
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "查看应用 tianshu-platform", exact: true })
    .click();
  await expect(
    page.getByText("专用维护 · resident", { exact: true }),
  ).toBeVisible({ timeout: 15000 });
  await expect(
    page.getByRole("button", { name: "停止应用", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "配置部署", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "预览修改部署", exact: true }),
  ).toBeDisabled();
  expect(
    (await new AxeBuilder({ page }).include(".infra-workspace").analyze())
      .violations,
  ).toEqual([]);
  expect(calls.map((call) => call.endpoint).sort()).toEqual([
    "action",
    "action",
    "action",
    "action",
    "execute",
    "execute",
    "plan",
    "plan",
  ]);
  expect(new Set(calls.map((call) => call.clientId)).size).toBe(calls.length);
  expect(droppedIds).toHaveLength(3);
  for (const id of droppedIds) {
    expect(calls.filter((call) => call.clientId === id)).toHaveLength(1);
    expect(queriedIds).toContain(id);
  }

  // Open the actual persisted restore operation from the unified task centre.
  // These endpoints are served by the real platform, without projection routes.
  await page.goto("/#/system/tasks");
  const source = page.getByRole("combobox", { name: "来源", exact: true });
  await source.selectOption("platform.infrastructure");
  const restoredTaskId = `infra-control:${restored.client_request_id}`;
  const restoredCard = page.locator("article.tasks-item").filter({
    has: page.locator(`[data-task-toggle="${restoredTaskId}"]`),
  });
  await expect(restoredCard).toContainText("恢复 Compose 栈版本");
  await expect(restoredCard).toContainText("fixture-web");
  await expect(restoredCard).toContainText("容器与 Compose 控制");
  const restoredResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/tasks/detail"),
  );
  await restoredCard
    .getByRole("button", { name: "查看详情", exact: true })
    .click();
  const task = (await (await restoredResponse).json()).task;
  expect(task.task_id).toBe(restoredTaskId);
  expect(task.status).toBe("observed");
  expect(task.timeline.recorded_at).toBeTruthy();
  expect(task.timeline.updated_at).toBeTruthy();
  expect(task.timeline.settled_at).toBeTruthy();
  expect(task.evidence.result.command_state).toBe("succeeded");
  expect(task.evidence.result.execution_active).toBe(false);
  expect(task.evidence.result.containers[0].container_id).toBe(
    "1".padStart(64, "0"),
  );
  const restoredDetail = restoredCard.getByRole("region");
  await expect(restoredDetail).toContainText("时间线");
  await expect(restoredDetail).toContainText("结案时间");
  await expect(restoredDetail).toContainText("执行结果");
  await expect(restoredDetail).toContainText("命令结果：成功");
  await expect(restoredDetail).toContainText("命令仍在运行：否");
  await expect(restoredDetail).toContainText("实际运行：运行中");
  await expect(restoredDetail).toContainText("健康检查：无");
  await expect(restoredDetail).toContainText(task.request.stack_id);
  await expect(restoredDetail).toContainText("1".padStart(64, "0"));
  await expect(restoredDetail).toContainText(
    "原始责任模块 容器与 Compose 控制",
  );
  await expect(
    restoredDetail.getByRole("link", { name: "到责任模块页面" }),
  ).toHaveAttribute("href", "#/system/containers");
  await expect(restoredDetail).not.toContainText("[object Object]");

  // Retained synthetic media history exercises the same producer and renderer,
  // including arrays, unavailable results and nulls, without running a download.
  await source.selectOption("resources.download");
  const mediaCard = page.locator("article.tasks-item").filter({
    has: page.getByRole("heading", {
      name: "合成媒体任务详情（只读夹具）",
      exact: true,
    }),
  });
  const mediaResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/tasks/detail"),
  );
  await mediaCard
    .getByRole("button", { name: "查看详情", exact: true })
    .click();
  const mediaTask = (await (await mediaResponse).json()).task;
  expect(mediaTask.source).toBe("resources.download");
  expect(mediaTask.status).toBe("unknown");
  expect(Object.keys(mediaTask.timeline).sort()).toEqual([
    "recorded_at",
    "updated_at",
  ]);
  expect(mediaTask.evidence.media_servers[1].item_id).toBeNull();
  const mediaDetail = mediaCard.getByRole("region");
  await expect(mediaDetail).toContainText("时间线");
  await expect(mediaDetail).toContainText("媒体服务器结果");
  await expect(mediaDetail).toContainText("synthetic-jellyfin");
  await expect(mediaDetail).toContainText("synthetic-plex");
  await expect(mediaDetail).toContainText("状态：不可用");
  await expect(mediaDetail).toContainText("项目标识：—");
  await expect(mediaDetail).not.toContainText("结案时间");
  await expect(mediaDetail).not.toContainText("[object Object]");
  await expect(mediaDetail).toContainText("原始责任模块 订阅与下载");
  expect(pageErrors).toEqual([]);
  await page.screenshot({
    path: testInfo.outputPath("dockge-task-detail-media.png"),
    fullPage: true,
  });
});
