import { createServer, type ViteDevServer } from "vite";
import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

/**
 * 人格版本的浏览器验收。
 *
 * 正向主链是**真实 Core**：`tests/backend/personas_fixture.py` 在本 worktree 的忽略运行时里导出固定
 * 提交 0a775631，经 uvicorn 在真实 loopback TLS 上服务，4820 的真实控制台只通过登记连接读它；合成
 * peer（4823）与指向它的故障控制台（4824）只承担额外故障注入，不代替主链。4821 未配置、4822 未授权
 * 是两个真实部署差异。
 *
 * 这里检查的是页面**说出来的话**：读不到、没授权、位置过期与「确实没有记录」必须是四种不同的状态；
 * 切角色/切类别/登出之后，旧 scope 的结果既不会留在屏幕上，也不会回填。
 */

const ADMIN = "synthetic-admin";
const ADMIN_PASSWORD = "synthetic-local-password-014";
const TOKEN = "synthetic-ts025-persona-admin-credential";
const PEER = "127.0.0.1:4823";
const CORE = "http://127.0.0.1:4820";
const UNCONFIGURED = "http://127.0.0.1:4821";
const FORBIDDEN = "http://127.0.0.1:4822";
const FAULTY = "http://127.0.0.1:4824";
const DEV_PORT = 4831;

async function login(page: Page) {
  await page.getByLabel("管理员账号").fill(ADMIN);
  await page.getByLabel("密码", { exact: true }).fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  // Being logged in is not authority: every test states the deployment state it actually got.
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
}

/**
 * Be logged in, whichever state this browser is in.
 *
 * Entering the page again in the same browser keeps the session cookie, so a test that comes back
 * to the page logs in only when the page really is showing its login form - and it waits for the
 * panel to say which of the two it is, which the development server takes a moment to render.
 */
async function ensureLogin(page: Page) {
  const form = page.getByLabel("管理员账号");
  const out = page.getByRole("button", { name: "退出登录" });
  await form.or(out).first().waitFor({ state: "visible" });
  if (await form.isVisible()) await login(page);
  else await expect(out).toBeVisible();
}

function rail(page: Page, label: string) {
  return page.locator(".persona-state .rail-label").filter({ hasText: label });
}

function subject(page: Page, id: string) {
  return page.locator(".persona-subject").filter({ hasText: id });
}

function rows(page: Page) {
  return page.locator(".persona-rows > li");
}

/** The history pager, distinct from the directory pager that shares the button words. */
function historyPager(page: Page) {
  return page.locator(".persona-history .persona-pager");
}

/** The console's own read, used only to learn facts the page must then display. */
async function api(page: Page, path: string, body: object): Promise<unknown> {
  // Absolute, from the page's own origin: the request context's base URL is the main console, and a
  // session issued by another one is not a session here.
  const origin = new URL(page.url()).origin;
  const session = await page.request.get(`${origin}/api/v1/session`);
  const { csrf } = (await session.json()) as { csrf: string };
  const answer = await page.request.post(`${origin}/api/v1/personas/${path}`, {
    headers: { "X-CSRF-Token": csrf, Origin: origin },
    data: body,
  });
  expect(answer.ok(), `${path} ${answer.status()}`).toBeTruthy();
  return await answer.json();
}

/**
 * Set the synthetic peer's named scenario, and fail loudly when it was not set.
 *
 * The peer speaks real TLS, so its control path needs the fixture's own certificate to be accepted
 * for this one request. A scenario that silently did not take effect would make the fault tests
 * assert nothing at all, so the answer is checked here rather than assumed.
 */
async function scenario(
  request: APIRequestContext,
  name: "ready" | "unreadable" | "one-forbidden",
) {
  const answer = await request.post(`https://${PEER}/control/scenario`, {
    data: { scenario: name },
    ignoreHTTPSErrors: true,
  });
  expect(answer.ok(), `control ${name} ${answer.status()}`).toBeTruthy();
  expect((await answer.json()) as { scenario: string }).toEqual({
    scenario: name,
  });
}

/** A late answer the page must not act on: a refusal, a success, or a broken connection. */
type LateAnswer =
  { body: object } | { status: number; code: string } | "failed";

type Held = {
  /** How many requests this read has caught so far. */
  caught: () => number;
  /** Let every caught request answer, or fail, exactly once. */
  release: (answer: LateAnswer) => void;
};

/**
 * Hold one of the page's own reads open until the test lets it go.
 *
 * A reply that arrives *after* the operator switched character, switched kind or logged out is the
 * only way to see whether a stale answer - a stale failure above all - can still move the page. The
 * route keeps the request in this process, so the page keeps running while it is unanswered. A read
 * the page cancels is watched through `requestfailed` instead: answering a request the page already
 * abandoned is accepted here and simply ignored by the browser, so this route cannot report it.
 */
async function holdRead(page: Page, path: string): Promise<Held> {
  let answer: LateAnswer | null = null;
  const waiters: (() => void)[] = [];
  let caught = 0;
  await page.route(`**/api/v1/personas/${path}`, async (route) => {
    caught += 1;
    if (!answer) await new Promise<void>((resolve) => waiters.push(resolve));
    const chosen = answer ?? "failed";
    if (chosen === "failed") await route.abort("failed");
    else if ("body" in chosen)
      await route.fulfill({ status: 200, json: chosen.body });
    else
      await route.fulfill({
        status: chosen.status,
        json: { code: chosen.code },
      });
  });
  return {
    caught: () => caught,
    release: (late) => {
      answer = late;
      for (const wake of waiters.splice(0)) wake();
    },
  };
}

/** What actually overflows, so a failure names the element instead of only the page width. */
async function overflow(page: Page) {
  return page.evaluate(() => {
    const inner = window.innerWidth;
    const name = (element: HTMLElement) =>
      `${element.tagName.toLowerCase()}.${String(element.className).split(" ")[0]}`;
    const roots: string[] = [];
    for (const element of document.querySelectorAll<HTMLElement>("body *")) {
      const box = element.getBoundingClientRect();
      if (box.right <= inner + 1) continue;
      const parent = element.parentElement?.getBoundingClientRect();
      // The outermost overflowing element is the one whose parent still fits.
      if (parent && parent.right > inner + 1) continue;
      const style = getComputedStyle(element);
      roots.push(
        `${name(element)} w=${Math.round(box.width)} parent=${Math.round(parent?.width ?? 0)} min=${style.minWidth} ws=${style.whiteSpace}`,
      );
    }
    const scrolling: string[] = [];
    for (const element of document.querySelectorAll<HTMLElement>("body *")) {
      if (
        element.scrollWidth > element.clientWidth + 1 &&
        element.clientWidth > 0
      )
        scrolling.push(
          `${name(element)} client=${element.clientWidth} scroll=${element.scrollWidth}`,
        );
    }
    return {
      scrollWidth: document.documentElement.scrollWidth,
      inner,
      roots: roots.slice(0, 6),
      scrolling: scrolling.slice(0, 6),
    };
  });
}

async function noOverflow(page: Page) {
  const measured = await overflow(page);
  expect(measured.scrollWidth, JSON.stringify(measured)).toBeLessThanOrEqual(
    measured.inner + 1,
  );
}

/** The first directory row is a character the fixture seeds with a full history. */
async function openFirst(page: Page) {
  await page.locator(".persona-subject").first().click();
  await expect(rows(page).first()).toBeVisible();
}

test("真实 Core 主链：目录按 20 个一页读取，回答里没有凭据、地址或本地路径", async ({
  page,
}, testInfo) => {
  const bodies: string[] = [];
  page.on("response", (response) => {
    if (response.url().includes("/api/v1/personas/"))
      void response
        .text()
        .then((text) => bodies.push(text))
        .catch(() => undefined);
  });
  await page.goto("/#/roles/persona");
  await login(page);
  await expect(rail(page, "可读")).toBeVisible();
  await expect(page.locator(".persona-state")).toContainText(
    "已授权角色 22 个 · 每页 20 个",
  );
  await expect(page.locator(".persona-subject")).toHaveCount(20);
  await expect(page.locator(".persona-catalog .persona-pager")).toContainText(
    "第 1 页 · 本页 20 个角色 · 共 22 个",
  );
  // The deployment's character the Core never held is a stated absence, and the character whose
  // version was never declared is readable: the corrected candidate reads `imported: null`.
  await expect(subject(page, "actor:epsilon")).toContainText("已发布");
  await page.getByRole("button", { name: "下一页角色" }).click();
  await expect(page.locator(".persona-subject")).toHaveCount(2);
  await expect(subject(page, "actor:missing")).toContainText(
    "角色服务里没有这个角色",
  );
  await page.screenshot({
    path: testInfo.outputPath("core-directory-page-2.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "上一页角色" }).click();
  await expect(page.locator(".persona-subject")).toHaveCount(20);
  await expect.poll(() => bodies.length).toBeGreaterThan(1);
  for (const body of bodies) {
    for (const secret of [
      TOKEN,
      "Bearer",
      PEER,
      "localhost.pem",
      "characters-local",
      "core.sqlite",
    ])
      expect(body).not.toContain(secret);
  }
});

test("真实 Core 主链：四类历史各自成页，单修订四字段可折叠且换行保留", async ({
  page,
}, testInfo) => {
  await page.goto("/#/roles/persona");
  await login(page);
  await expect(rail(page, "可读")).toBeVisible();
  await openFirst(page);
  // The pointer area names the character's own current pointers and version.
  await expect(page.locator(".persona-pointer")).toContainText("当前发布");
  await expect(
    page.locator('.persona-pointer dd[data-pointer="published"]'),
  ).not.toBeEmpty();
  await expect(
    page.locator('.persona-pointer dd[data-pointer="version"]'),
  ).not.toHaveText("未知");
  await expect(rows(page)).toHaveCount(20);
  await expect(historyPager(page)).toContainText(
    "第 1 页 · 本页 20 条 · 基线版本",
  );
  await historyPager(page).getByRole("button", { name: "下一页" }).click();
  await expect(rows(page)).not.toHaveCount(0);
  await historyPager(page)
    .getByRole("button", { name: "重新打开第一页" })
    .click();
  await expect(rows(page)).toHaveCount(20);
  // Every kind is the producer's own history: each one is read, and none is a copy of another.
  for (const [kind, name] of [
    ["publications", "发布"],
    ["approvals", "批准"],
    ["rollbacks", "回退"],
  ] as const) {
    await page.getByRole("button", { name, exact: true }).click();
    await expect(rows(page).first()).toBeVisible();
    const answer = (await api(page, "history", {
      subject: "actor:alpha",
      kind,
      cursor: null,
    })) as unknown as { count: number };
    await expect(rows(page)).toHaveCount(answer.count);
  }
  await page.getByRole("button", { name: "修订", exact: true }).click();
  await expect(rows(page)).toHaveCount(20);
  await rows(page).first().getByRole("button", { name: "查看这一版" }).click();
  const panel = page.locator(".persona-revision");
  await expect(panel.locator(".persona-field")).toHaveCount(4);
  await expect(panel.locator(".persona-field-body").first()).not.toBeEmpty();
  await expect(panel).toContainText("当前发布");
  await page.screenshot({
    path: testInfo.outputPath("core-history-and-revision.png"),
    fullPage: true,
  });
  // Folding a field keeps the text and puts it back: the body is real text, not a label.
  const persona = panel.locator(".persona-field").first();
  const text = await persona.locator(".persona-field-body").innerText();
  await persona.locator("summary").click();
  await expect(persona.locator(".persona-field-body")).toBeHidden();
  await persona.locator("summary").click();
  await expect(persona.locator(".persona-field-body")).toHaveText(text);
});

test("真实 Core 主链：两版差异逐项列出，与本页读到的同一份事实一致", async ({
  page,
}, testInfo) => {
  await page.goto("/#/roles/persona");
  await login(page);
  await openFirst(page);
  type History = {
    entries: { revision_id: string }[];
    next_cursor: string | null;
  };
  const first = (await api(page, "history", {
    subject: "actor:alpha",
    kind: "revisions",
    cursor: null,
  })) as History;
  // The fields that make the three change words reachable are on the last revision, which is on
  // the history's second page: the comparison is driven across both pages so paging and comparing
  // are proved together.
  const rest = (await api(page, "history", {
    subject: "actor:alpha",
    kind: "revisions",
    cursor: first.next_cursor,
  })) as History;
  const heads = first.entries.slice(0, 2).map((entry) => entry.revision_id);
  const tails = rest.entries.slice(-2).map((entry) => entry.revision_id);
  // The facts come from the console's own answer; the page must render exactly these.
  let chosen: {
    left: string;
    right: string;
    fields: Record<string, unknown>;
  } | null = null;
  for (const right of tails) {
    for (const left of heads) {
      const answer = (await api(page, "compare", {
        subject: "actor:alpha",
        left,
        right,
      })) as unknown as {
        left: { revision_id: string };
        right: { revision_id: string };
        fields: Record<string, { change: string }>;
      };
      const changes = Object.values(answer.fields).map((field) => field.change);
      if (
        changes.includes("modified") &&
        changes.includes("removed") &&
        changes.includes("unchanged")
      ) {
        chosen = { left, right, fields: answer.fields };
        break;
      }
    }
    if (chosen) break;
  }
  expect(
    chosen,
    "the seeded history has a modified/removed/unchanged comparison",
  ).not.toBeNull();
  const label: Record<string, string> = {
    modified: "改动",
    removed: "移除",
    unchanged: "相同",
    added: "新增",
  };
  const names: Record<string, string> = {
    persona: "人格",
    tone: "语气",
    style: "表达",
    address: "称呼",
  };
  // Drive the page through its own buttons: the row whose digest matches the chosen revision.
  const rowFor = (id: string) =>
    rows(page)
      .filter({ hasText: id.slice(0, 8) })
      .first();
  await rowFor(chosen!.left)
    .getByRole("button", { name: "设为对比基线" })
    .click();
  await expect(page.locator(".persona-notice")).toContainText("设为对比基线");
  await expect(rowFor(chosen!.left).locator(".status-rail")).toHaveAttribute(
    "data-selected",
    "true",
  );
  // The baseline survives the page change, and the comparison is read against it.
  await historyPager(page).getByRole("button", { name: "下一页" }).click();
  await expect(historyPager(page)).toContainText("第 2 页");
  await rowFor(chosen!.right)
    .getByRole("button", { name: "与基线比较" })
    .click();
  const comparison = page.locator(".persona-revision");
  for (const [name, field] of Object.entries(
    chosen!.fields as Record<string, { change: string }>,
  )) {
    await expect(comparison).toContainText(
      `${names[name]} · ${label[field.change]}`,
    );
  }
  await page.screenshot({
    path: testInfo.outputPath("core-comparison.png"),
    fullPage: true,
  });
});

test("三态正文：未提供、明确为 null、空字符串在单版里是三句话", async ({
  page,
}, testInfo) => {
  await page.goto(`${FAULTY}/#/roles/persona`);
  await login(page);
  await expect(rail(page, "可读")).toBeVisible();
  // The revision to read is the one the console itself reports as carrying all three states.
  type Body = { present: string[]; content: Record<string, string | null> };
  type Entry = { revision_id: string };
  const history = (await api(page, "history", {
    subject: "actor:beta",
    kind: "revisions",
    cursor: null,
  })) as { entries: Entry[] };
  let wanted = "";
  for (const entry of history.entries) {
    const body = (await api(page, "revision", {
      subject: "actor:beta",
      revision_id: entry.revision_id,
    })) as { revision: Body };
    const present = body.revision.present;
    if (
      present.includes("tone") &&
      body.revision.content.tone === null &&
      present.includes("style") &&
      body.revision.content.style === "" &&
      !present.includes("address")
    ) {
      wanted = entry.revision_id;
      break;
    }
  }
  expect(
    wanted,
    "the fixture seeds one revision with all three states",
  ).not.toBe("");
  await subject(page, "actor:beta").click();
  await expect(rows(page).first()).toBeVisible();
  await rows(page)
    .filter({ hasText: wanted.slice(0, 8) })
    .first()
    .getByRole("button", { name: "查看这一版" })
    .click();
  const panel = page.locator(".persona-revision");
  await expect(panel.locator(".persona-field")).toHaveCount(4);
  await expect(panel.locator(".persona-field-body")).toHaveCount(4);
  await expect(
    panel.locator('.persona-field-body[data-body-state="null"]'),
  ).toHaveText("明确为null");
  await expect(
    panel.locator('.persona-field-body[data-body-state="empty"]'),
  ).toHaveText("空字符串");
  await expect(
    panel.locator('.persona-field-body[data-body-state="absent"]'),
  ).toHaveText("未提供");
  await expect(
    panel.locator('.persona-field-body[data-body-state="text"]'),
  ).not.toBeEmpty();
  await page.screenshot({
    path: testInfo.outputPath("body-three-states.png"),
    fullPage: true,
  });
});

test("三态比较：两侧各自说清楚有没有这个字段", async ({ page }, testInfo) => {
  await page.goto(`${FAULTY}/#/roles/persona`);
  await login(page);
  await subject(page, "actor:beta").click();
  await expect(rows(page).first()).toBeVisible();
  // Learn which two revisions really differ in the three states, then drive the page to them.
  type Field = {
    presence: { left: boolean; right: boolean };
    left: string | null;
    right: string | null;
  };
  const history = (await api(page, "history", {
    subject: "actor:beta",
    kind: "revisions",
    cursor: null,
  })) as { entries: { revision_id: string }[] };
  let pair: { left: string; right: string } | null = null;
  const ids = history.entries.map((entry) => entry.revision_id);
  for (const right of ids) {
    for (const left of ids) {
      if (left === right) continue;
      const answer = (await api(page, "compare", {
        subject: "actor:beta",
        left,
        right,
      })) as { fields: Record<string, Field> };
      const states = Object.values(answer.fields)
        .map((field) => [
          field.presence.left ? String(field.left) : "absent",
          field.presence.right ? String(field.right) : "absent",
        ])
        .flat();
      if (
        states.includes("absent") &&
        states.includes("null") &&
        states.includes("")
      )
        pair = { left, right };
      if (pair) break;
    }
    if (pair) break;
  }
  expect(
    pair,
    "the fixture seeds two revisions with the three states",
  ).not.toBeNull();
  const rowFor = (id: string) =>
    rows(page)
      .filter({ hasText: id.slice(0, 8) })
      .first();
  await rowFor(pair!.left)
    .getByRole("button", { name: "设为对比基线" })
    .click();
  await rowFor(pair!.right).getByRole("button", { name: "与基线比较" }).click();
  await expect(
    page.locator(".persona-change .persona-sides").first(),
  ).toBeVisible();
  await expect(
    page
      .locator('.persona-change .persona-sides [data-body-state="absent"]')
      .first(),
  ).toHaveText("未提供");
  await expect(
    page
      .locator('.persona-change .persona-sides [data-body-state="null"]')
      .first(),
  ).toHaveText("明确为null");
  await expect(
    page
      .locator('.persona-change .persona-sides [data-body-state="empty"]')
      .first(),
  ).toHaveText("空字符串");
  await page.screenshot({
    path: testInfo.outputPath("compare-three-states.png"),
    fullPage: true,
  });
});

test("未配置与未授权是两种明说的状态，不是空目录", async ({ page }) => {
  await page.goto(`${UNCONFIGURED}/#/roles/persona`);
  await login(page);
  await expect(rail(page, "未配置")).toBeVisible();
  await expect(page.locator(".persona-state")).toContainText(
    "personas_not_configured",
  );
  await expect(page.locator(".state-panel")).toContainText(
    "人格页需要部署登记一个角色服务",
  );
  await expect(page.locator(".persona-subject")).toHaveCount(0);
  await page.goto(`${FORBIDDEN}/#/roles/persona`);
  await login(page);
  await expect(rail(page, "未授权")).toBeVisible();
  await expect(page.locator(".persona-state")).toContainText(
    "persona_read_required",
  );
  await expect(page.locator(".state-panel h2")).toHaveText("未授权");
  await expect(page.locator(".persona-subject")).toHaveCount(0);
});

test("一个角色读不到时整页失败，不显示成有缺口的目录", async ({
  page,
  request,
}) => {
  await page.goto(`${FAULTY}/#/roles/persona`);
  await login(page);
  await expect(page.locator(".persona-subject")).toHaveCount(20);
  await scenario(request, "one-forbidden");
  await page.getByRole("button", { name: "重新读取" }).click();
  await expect(rail(page, "读取失败")).toBeVisible();
  await expect(page.locator(".persona-state")).toContainText("forbidden");
  // Not one row less and not an empty directory: the whole read failed, and it says so.
  await expect(page.getByText("目录为空")).toHaveCount(0);
  await scenario(request, "ready");
  await page.getByRole("button", { name: "重新读取" }).click();
  await expect(rail(page, "可读")).toBeVisible();
  await expect(page.locator(".persona-subject")).toHaveCount(20);
});

test("角色服务整体读不到时是读取失败，恢复后重新可读", async ({
  page,
  request,
}) => {
  await page.goto(`${FAULTY}/#/roles/persona`);
  await login(page);
  await expect(page.locator(".persona-subject")).toHaveCount(20);
  await scenario(request, "unreadable");
  await page.getByRole("button", { name: "重新读取" }).click();
  await expect(rail(page, "读取失败")).toBeVisible();
  await expect(page.locator(".persona-state")).toContainText(
    "invalid_upstream",
  );
  // The rows left over from the good read are never passed off as this read's result.
  await expect(page.locator(".persona-state")).toContainText(
    "上一次成功读取的结果",
  );
  await expect(page.getByText("目录为空")).toHaveCount(0);
  await expect(page.getByText("目录读取失败")).toHaveCount(0);
  await scenario(request, "ready");
  await page.getByRole("button", { name: "重新读取" }).click();
  await expect(rail(page, "可读")).toBeVisible();
  await expect(page.locator(".persona-state")).not.toContainText(
    "上一次成功读取的结果",
  );
  await expect(page.locator(".persona-subject")).toHaveCount(20);
});

test("目录一次都读不到时是读取失败，不是空目录，也不会自动重试", async ({
  page,
}) => {
  let refused = 0;
  await page.route("**/api/v1/personas/catalog", async (route) => {
    refused += 1;
    await route.fulfill({
      status: 503,
      json: { code: "dependency_unavailable" },
    });
  });
  await page.goto("/#/roles/persona");
  await login(page);
  await expect(rail(page, "读取失败")).toBeVisible();
  await expect(page.locator(".persona-state")).toContainText(
    "dependency_unavailable",
  );
  await expect(page.locator(".state-panel")).toContainText("目录读取失败");
  await expect(page.locator(".persona-subject")).toHaveCount(0);
  await expect(page.getByText("目录为空")).toHaveCount(0);
  await expect(page.getByText("上一次成功读取的结果")).toHaveCount(0);
  // One refusal is one answer: a failed read is not retried behind the operator's back.
  await page.waitForTimeout(600);
  expect(refused).toBe(1);
});

test("续读位置过期时要求手动重开，不自动翻页", async ({ page }) => {
  await page.goto("/#/roles/persona");
  await login(page);
  await openFirst(page);
  await expect(rows(page)).toHaveCount(20);
  let asked = 0;
  await page.route("**/api/v1/personas/history", async (route) => {
    asked += 1;
    await route.fulfill({
      status: 409,
      json: { code: "version_conflict", message: "人格已经变化" },
    });
  });
  await historyPager(page).getByRole("button", { name: "下一页" }).click();
  await expect(rail(page, "版本已过期")).toBeVisible();
  // The scope states its own failure: this character's history has no result, and the rows of the
  // page before it are gone rather than reinterpreted.
  await expect(page.locator(".persona-history")).toContainText(
    "历史这一次没有读到",
  );
  await expect(rows(page)).toHaveCount(0);
  const reopen = page
    .locator(".persona-detail > .status-rail")
    .getByRole("button", { name: "重新打开第一页" });
  await expect(reopen).toBeVisible();
  await page.waitForTimeout(600);
  expect(asked).toBe(1);
  await page.unroute("**/api/v1/personas/history");
  await reopen.click();
  await expect(rows(page)).toHaveCount(20);
  await expect(
    page.getByText("这里不会自动翻页，请重新打开第一页。"),
  ).toHaveCount(0);
  await expect(
    page
      .locator(".persona-detail .rail-label")
      .filter({ hasText: "版本已过期" }),
  ).toHaveCount(0);
});

test("登录被撤销后不留下旧面板", async ({ page }) => {
  await page.goto("/#/roles/persona");
  await login(page);
  await expect(page.locator(".persona-subject")).toHaveCount(20);
  await page.route("**/api/v1/personas/catalog", async (route) => {
    await route.fulfill({ status: 401, json: { code: "session_expired" } });
  });
  await page.route("**/api/v1/session", async (route) => {
    await route.fulfill({ json: { authenticated: false, csrf: "stub-csrf" } });
  });
  await page.getByRole("button", { name: "重新读取" }).click();
  await expect(page.locator(".persona-state")).toContainText("登录已过期");
  await expect(page.getByLabel("管理员账号")).toBeVisible();
  await expect(page.locator(".persona-subject")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "退出登录" })).toHaveCount(0);
});

test("切角色失败：新角色说得清楚，旧角色的历史不回填", async ({ page }) => {
  await page.goto("/#/roles/persona");
  await login(page);
  await openFirst(page);
  await expect(rows(page)).toHaveCount(20);
  await rows(page).first().getByRole("button", { name: "查看这一版" }).click();
  await expect(page.locator(".persona-revision")).toContainText("当前发布");
  const alpha = await rows(page).first().innerText();
  // The second character's history read fails on the way out.
  await page.route("**/api/v1/personas/history", async (route) => {
    const body = route.request().postDataJSON() as { subject: string };
    if (body.subject === "actor:beta") {
      await route.fulfill({
        status: 503,
        json: { code: "dependency_unavailable" },
      });
      return;
    }
    await route.continue();
  });
  await subject(page, "actor:beta").click();
  // The body and the comparison of the previous character are gone in the same turn as the click.
  await expect(page.locator(".persona-revision")).toHaveCount(0);
  await expect(page.locator(".persona-detail")).not.toContainText(
    alpha.split("\n")[0],
  );
  await expect(page.locator(".persona-history")).toContainText(
    "actor:beta 的修订历史这一次没有读到",
  );
  await expect(rows(page)).toHaveCount(0);
  // The pointer area belongs to the character now chosen, not to the one before it.
  await expect(
    page.locator('.persona-pointer dd[data-pointer="version"]'),
  ).toHaveText(/^\d+$/);
  await page.unroute("**/api/v1/personas/history");
});

test("切类别失败与快速 A→B→A：晚到的旧结果不许冠新名", async ({ page }) => {
  await page.goto("/#/roles/persona");
  await login(page);
  await openFirst(page);
  await expect(rows(page)).toHaveCount(20);
  // A kind whose read fails states its own failure and keeps no row from the kind before it.
  await page.route("**/api/v1/personas/history", async (route) => {
    const body = route.request().postDataJSON() as { kind: string };
    if (body.kind === "rollbacks") {
      await route.fulfill({
        status: 503,
        json: { code: "dependency_unavailable" },
      });
      return;
    }
    await route.continue();
  });
  await page.getByRole("button", { name: "回退", exact: true }).click();
  await expect(page.locator(".persona-history")).toContainText(
    "回退历史这一次没有读到",
  );
  await expect(rows(page)).toHaveCount(0);
  // Back to the revision history, so the characters compared below are compared like for like.
  await page.unroute("**/api/v1/personas/history");
  await page.getByRole("button", { name: "修订", exact: true }).click();
  await expect(rows(page)).toHaveCount(20);
  const alphaFirst = await rows(page).first().innerText();
  // A→B→A with the B read held open: the answer that arrives last belongs to a scope that is gone.
  let held = 0;
  await page.route("**/api/v1/personas/history", async (route) => {
    const body = route.request().postDataJSON() as { subject: string };
    if (body.subject === "actor:beta") {
      held += 1;
      await new Promise((resolve) => setTimeout(resolve, 800));
      await route.fulfill({
        status: 200,
        json: {
          subject: "actor:beta",
          kind: "revisions",
          persona_version: 9,
          consistency: "revision",
          limit: 20,
          count: 1,
          entries: [
            { revision_id: "f".repeat(64), source: "late-beta-answer" },
          ],
          has_more: false,
          next_cursor: null,
        },
      });
      return;
    }
    await route.continue();
  });
  await subject(page, "actor:beta").click();
  await subject(page, "actor:alpha").click();
  await expect(rows(page).first()).toBeVisible();
  await expect(page.locator(".persona-detail")).not.toContainText(
    "late-beta-answer",
  );
  await expect(page.locator(".persona-history")).toHaveAttribute(
    "aria-label",
    "actor:alpha 的历史",
  );
  await page.waitForTimeout(1200);
  expect(held).toBeGreaterThan(0);
  await expect(page.locator(".persona-detail")).not.toContainText(
    "late-beta-answer",
  );
  await expect(rows(page).first()).toContainText(alphaFirst.split("\n")[0]);
  await page.unroute("**/api/v1/personas/history");
});

/**
 * A late failure is not this page's failure.
 *
 * The path is real: a character's revision read is opened, the operator moves to another character
 * whose history reads perfectly well, and only then does the first read answer - with a refusal, a
 * contradicting version, an expired session or a broken connection. None of those may empty,
 * forbid, re-label or log out the page the operator is actually looking at.
 */
test("迟到的旧角色失败不污染已经切到的角色：403、401、409 与断线", async ({
  page,
}) => {
  await page.goto("/#/roles/persona");
  await login(page);
  await openFirst(page);
  await expect(rows(page)).toHaveCount(20);
  const variants: { name: string; answer: LateAnswer }[] = [
    {
      name: "403 persona_read_required",
      answer: { status: 403, code: "persona_read_required" },
    },
    {
      name: "401 session_expired",
      answer: { status: 401, code: "session_expired" },
    },
    {
      name: "409 version_conflict",
      answer: { status: 409, code: "version_conflict" },
    },
    { name: "断线", answer: "failed" },
  ];
  for (const variant of variants) {
    // Alpha's revision read is opened and held, so it can only answer after the switch.
    await subject(page, "actor:alpha").click();
    await expect(rows(page)).toHaveCount(20);
    const held = await holdRead(page, "revision");
    await rows(page)
      .first()
      .getByRole("button", { name: "查看这一版" })
      .click();
    await expect.poll(held.caught, { message: variant.name }).toBe(1);
    await subject(page, "actor:beta").click();
    await expect(page.locator(".persona-history")).toHaveAttribute(
      "aria-label",
      "actor:beta 的历史",
    );
    await expect(rows(page).first()).toBeVisible();
    // Beta's own page, exactly as the console answered it: the count the late reply must not touch.
    const betaRows = await rows(page).count();
    expect(betaRows, variant.name).toBeGreaterThan(0);
    // Now the old read answers. Every word of it belongs to the character that was left.
    held.release(variant.answer);
    await page.waitForTimeout(700);
    await expect(
      page.locator(".persona-history"),
      variant.name,
    ).toHaveAttribute("aria-label", "actor:beta 的历史");
    await expect(rows(page), variant.name).toHaveCount(betaRows);
    await expect(rail(page, "可读"), variant.name).toBeVisible();
    await expect(page.locator(".persona-login"), variant.name).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "退出登录" }),
      variant.name,
    ).toBeVisible();
    for (const word of [
      "未授权",
      "登录已过期",
      "版本已过期",
      "已选版本不可用",
      "读取中断",
      "persona_read_required",
      "session_expired",
      "version_conflict",
    ])
      await expect(
        page.locator(".persona-state"),
        `${variant.name} ${word}`,
      ).not.toContainText(word);
    await page.unroute("**/api/v1/personas/revision");
  }
});

/**
 * The session re-read a 401 triggers is itself an await, and the operator can move on during it.
 *
 * The refusal is this page's own refusal at the moment it arrives; what makes it stale is what
 * happened while the console was re-reading the session. The answer to that re-read may not log out
 * a page that has moved on to another character in the meantime.
 */
test("迟到的会话复查不许注销已经换过的页面", async ({ page }) => {
  await page.goto("/#/roles/persona");
  await login(page);
  await openFirst(page);
  await expect(rows(page)).toHaveCount(20);
  let sessionReads = 0;
  let releaseSession = () => {};
  const heldSession = new Promise<void>((resolve) => {
    releaseSession = resolve;
  });
  await page.route("**/api/v1/session", async (route) => {
    sessionReads += 1;
    await heldSession;
    await route.fulfill({ json: { authenticated: false, csrf: "stub-csrf" } });
  });
  await page.route("**/api/v1/personas/history", async (route) => {
    const body = route.request().postDataJSON() as { subject: string };
    if (body.subject === "actor:beta")
      await route.fulfill({
        status: 401,
        json: { code: "session_expired" },
      });
    else await route.continue();
  });
  await subject(page, "actor:beta").click();
  // Wait until beta's refusal has really been taken and the console is re-reading the session.
  await expect.poll(() => sessionReads).toBeGreaterThan(0);
  await subject(page, "actor:alpha").click();
  await expect(page.locator(".persona-history")).toHaveAttribute(
    "aria-label",
    "actor:alpha 的历史",
  );
  await expect(rows(page)).toHaveCount(20);
  releaseSession();
  await page.waitForTimeout(600);
  await expect(page.locator(".persona-login")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
  await expect(rail(page, "可读")).toBeVisible();
  await expect(page.locator(".persona-history")).toHaveAttribute(
    "aria-label",
    "actor:alpha 的历史",
  );
  await expect(rows(page)).toHaveCount(20);
  await page.unroute("**/api/v1/session");
  await page.unroute("**/api/v1/personas/history");
});

/**
 * A refusal of the read that *is* current still clears the page.
 *
 * The fix for stale answers must not turn into ignoring refusals: when the action is really revoked
 * for the character on screen, or the session is really gone, the page says so and stops showing
 * what it can no longer stand behind.
 */
test("当前读取被撤权仍然清空整页", async ({ page }) => {
  await page.goto("/#/roles/persona");
  await login(page);
  await openFirst(page);
  await expect(rows(page)).toHaveCount(20);
  await rows(page).first().getByRole("button", { name: "查看这一版" }).click();
  await expect(page.locator(".persona-revision")).toContainText("当前发布");
  // The action is revoked now, and the read that is running right now is refused because of it.
  await page.route("**/api/v1/personas/history", async (route) =>
    route.fulfill({ status: 403, json: { code: "persona_read_required" } }),
  );
  await subject(page, "actor:beta").click();
  await expect(page.locator(".persona-state")).toContainText("未授权");
  await expect(page.locator(".persona-state")).toContainText(
    "persona_read_required",
  );
  await expect(page.locator(".persona-subject")).toHaveCount(0);
  await expect(rows(page)).toHaveCount(0);
  await expect(page.locator(".persona-revision")).toHaveCount(0);
  // Being unable to read is not being logged out: the session is untouched and stated as such.
  await expect(page.locator(".persona-login")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
  await page.unroute("**/api/v1/personas/history");
  // Reading again after the revocation is lifted restores exactly one whole page.
  await page.getByRole("button", { name: "重新读取" }).click();
  await expect(rail(page, "可读")).toBeVisible();
  await expect(page.locator(".persona-subject")).toHaveCount(20);
  // And a session that really is gone still ends the page, with the login form as its next state.
  await page.route("**/api/v1/personas/history", async (route) =>
    route.fulfill({ status: 401, json: { code: "session_expired" } }),
  );
  await page.route("**/api/v1/session", async (route) =>
    route.fulfill({ json: { authenticated: false, csrf: "stub-csrf" } }),
  );
  await subject(page, "actor:beta").click();
  await expect(page.getByLabel("管理员账号")).toBeVisible();
  await expect(rows(page)).toHaveCount(0);
  await expect(page.locator(".persona-subject")).toHaveCount(0);
});

/**
 * Leaving is always allowed, including in the middle of a read the operator no longer wants.
 *
 * The exit button is not disabled by reading: clicking it cancels this page's own reads, clears
 * every sensitive state in the same turn, and reports the server's receipt as what it was. A read
 * that was in the air cannot refill the page afterwards.
 */
test("读取进行中也能退出：按钮可用、立即清空、迟到的回答不回来", async ({
  page,
}) => {
  await page.goto("/#/roles/persona");
  await login(page);
  await openFirst(page);
  await expect(rows(page)).toHaveCount(20);
  const held = await holdRead(page, "history");
  const failed: string[] = [];
  page.on("requestfailed", (request) => {
    if (request.url().includes("/api/v1/personas/"))
      failed.push(
        `${request.url().split("/").pop()} ${request.failure()?.errorText}`,
      );
  });
  await subject(page, "actor:beta").click();
  await expect.poll(held.caught).toBe(1);
  // The page is reading right now - and the operator can still walk away from it.
  await expect(page.locator(".persona-page")).toHaveAttribute(
    "aria-busy",
    "true",
  );
  const out = page.getByRole("button", { name: "退出登录" });
  await expect(out).toBeEnabled();
  // The server's receipt is held back, so "cleared at the click" is what is on screen now.
  await page.route("**/api/v1/logout", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 700));
    await route.continue();
  });
  await out.click();
  await expect(page.locator(".persona-subject")).toHaveCount(0);
  await expect(page.getByLabel("管理员账号")).toBeVisible();
  await expect(page.locator(".persona-notice")).toContainText("正在通知服务器");
  await expect(page.locator(".persona-notice")).toContainText(
    "服务器已注销这个会话",
  );
  await page.unroute("**/api/v1/logout");
  // The abandoned read answers now, with a whole page's worth of rows. None of it may come back.
  held.release({
    body: {
      subject: "actor:beta",
      kind: "revisions",
      persona_version: 9,
      consistency: "revision",
      limit: 20,
      count: 1,
      entries: [{ revision_id: "f".repeat(64), source: "late-beta-answer" }],
      has_more: false,
      next_cursor: null,
    },
  });
  await page.waitForTimeout(700);
  await expect(page.getByLabel("管理员账号")).toBeVisible();
  await expect(page.locator(".persona-subject")).toHaveCount(0);
  // Nothing of the abandoned answer is anywhere on the page, and the pane itself is gone.
  await expect(page.locator(".persona-detail")).toHaveCount(0);
  await expect(page.locator(".persona-rows > li")).toHaveCount(0);
  await expect(page.locator(".persona-page")).not.toContainText(
    "late-beta-answer",
  );
  // Cancelled, not merely ignored: the browser recorded this read as aborted at the click.
  expect(failed.join(" "), "the read really was taken back").toContain(
    "net::ERR_ABORTED",
  );
});

test("再次进入页面重新读取，不把上一次的结果当成这一次的", async ({ page }) => {
  await page.goto("/#/roles/persona");
  await login(page);
  await openFirst(page);
  await expect(rows(page)).toHaveCount(20);
  // Leave the page and come back: everything is read again, and no half-page survives the trip.
  await page.goto("/#/system/tasks");
  await expect(page.locator(".persona-page")).toHaveCount(0);
  await page.goto("/#/roles/persona");
  await expect(page.locator(".persona-page")).toBeVisible();
  await ensureLogin(page);
  await expect(rail(page, "可读")).toBeVisible();
  await expect(page.locator(".persona-subject")).toHaveCount(20);
  await expect(page.locator(".persona-revision")).toHaveCount(0);
  await expect(page.locator(".persona-detail")).toContainText("尚未选择版本");
});

test("登出立即清空本页，注销完成与未确认分别呈现", async ({ page }) => {
  await page.goto("/#/roles/persona");
  await login(page);
  await openFirst(page);
  await expect(rows(page)).toHaveCount(20);
  // The server's answer is held back: the page must stop showing anything readable first.
  await page.route("**/api/v1/logout", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 700));
    await route.continue();
  });
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page.locator(".persona-subject")).toHaveCount(0);
  await expect(page.getByLabel("管理员账号")).toBeVisible();
  await expect(page.locator(".persona-notice")).toContainText("正在通知服务器");
  await expect(page.locator(".persona-notice")).toContainText(
    "服务器已注销这个会话",
  );
  await page.unroute("**/api/v1/logout");
  // And when the logout cannot be confirmed, the page says exactly that: the session the server
  // still holds is shown again, never passed off as a finished logout.
  await login(page);
  await expect(page.locator(".persona-subject")).toHaveCount(20);
  await page.route("**/api/v1/logout", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 700));
    await route.fulfill({
      status: 503,
      json: { code: "dependency_unavailable" },
    });
  });
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page.locator(".persona-subject")).toHaveCount(0);
  await expect(page.locator(".persona-state")).toContainText("退出未确认");
  await expect(page.locator(".persona-state")).toContainText(
    "dependency_unavailable",
  );
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
  await expect(page.locator(".persona-subject")).toHaveCount(20);
  await page.unroute("**/api/v1/logout");
  // A logout the console itself refuses is reported with the code the console sent, and the session
  // the server still holds is shown again rather than passed off as a finished logout.
  await page.route("**/api/v1/logout", async (route) =>
    route.fulfill({ status: 403, json: { code: "forbidden" } }),
  );
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page.locator(".persona-subject")).toHaveCount(0);
  await expect(page.locator(".persona-state")).toContainText("退出未确认");
  await expect(page.locator(".persona-state")).toContainText("forbidden");
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
  await expect(page.locator(".persona-subject")).toHaveCount(20);
  await page.unroute("**/api/v1/logout");
});

test("开发 StrictMode 的真实 effect 重放：首次挂载与再次进入都得到整页", async ({
  page,
}, testInfo) => {
  // A real Vite development server, so React runs its development effect replay. The API is
  // proxied to the console exactly the way a development setup proxies it; nothing in the page is
  // switched off for the test.
  const server: ViteDevServer = await createServer({
    root: process.cwd(),
    configFile: process.cwd() + "/vite.config.ts",
    logLevel: "error",
    server: {
      port: DEV_PORT,
      // The same host as the console, so the session cookie this page is handed is the one the
      // proxy forwards: a `localhost` origin would be a different cookie jar.
      host: "127.0.0.1",
      strictPort: true,
      proxy: {
        "/api": {
          target: CORE,
          changeOrigin: true,
          configure(proxy) {
            proxy.on("proxyReq", (outgoing) => {
              outgoing.setHeader("Origin", CORE);
              outgoing.setHeader("Sec-Fetch-Site", "same-origin");
            });
          },
        },
      },
    },
  });
  await server.listen();
  const sessions: string[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/api/v1/session"))
      sessions.push(request.url());
  });
  try {
    await page.goto(`http://127.0.0.1:${DEV_PORT}/#/roles/persona`, {
      waitUntil: "load",
    });
    await ensureLogin(page);
    await expect(rail(page, "可读")).toBeVisible();
    // The development replay really happened: the first mount's session read was issued twice.
    expect(sessions.length).toBeGreaterThan(1);
    await expect(page.locator(".persona-subject")).toHaveCount(20);
    await openFirst(page);
    await expect(rows(page)).not.toHaveCount(0);
    await page.screenshot({
      path: testInfo.outputPath("dev-strictmode.png"),
      fullPage: true,
    });
    // Entering again replays the same effects and still lands on one whole page, not two halves.
    await page.goto(`http://127.0.0.1:${DEV_PORT}/#/roles/persona`, {
      waitUntil: "load",
    });
    await ensureLogin(page);
    await expect(rail(page, "可读")).toBeVisible();
    await expect(page.locator(".persona-page")).toHaveCount(1);
    await expect(page.locator(".persona-state .status-rail")).toHaveCount(1);
    await expect(page.locator(".persona-subject")).toHaveCount(20);
    await page.reload({ waitUntil: "load" });
    await ensureLogin(page);
    await expect(rail(page, "可读")).toBeVisible();
    await expect(page.locator(".persona-state .status-rail")).toHaveCount(1);
    await expect(page.locator(".persona-subject")).toHaveCount(20);
  } finally {
    await server.close();
  }
});

test("冻结布局：左目录 240px、顶部选择器、手机纵排且正文不在 2500px 之后", async ({
  page,
}, testInfo) => {
  await page.goto("/#/roles/persona");
  await login(page);
  await openFirst(page);
  await expect(rows(page)).toHaveCount(20);
  await expect(page.getByRole("heading", { name: "人格版本" })).toBeVisible();
  await expect(page.locator(".persona-page")).toContainText(
    "世界状态尚待后续接入",
  );
  // >=1100px: the directory column is exactly the frozen 240px beside the body.
  const wide = page.viewportSize() ?? { width: 1440, height: 1000 };
  expect(wide.width).toBeGreaterThanOrEqual(1100);
  const column = await page
    .locator(".persona-catalog")
    .evaluate((element) => Math.round(element.getBoundingClientRect().width));
  expect(column).toBe(240);
  // The twenty cards of the wide layout are what the narrow layout has to replace, so their height
  // is measured here rather than assumed.
  const tall = await page
    .locator(".persona-catalog")
    .evaluate((element) => Math.round(element.getBoundingClientRect().height));
  expect(tall).toBeGreaterThan(1500);
  const beside = await page.evaluate(() => {
    const catalog = document
      .querySelector(".persona-catalog")!
      .getBoundingClientRect();
    const detail = document
      .querySelector(".persona-detail")!
      .getBoundingClientRect();
    return { right: Math.round(catalog.right), left: Math.round(detail.left) };
  });
  expect(beside.left).toBeGreaterThanOrEqual(beside.right);
  // 768–1099px: the twenty cards belong to the wide layout only. This range gets the same labelled
  // compact selector the phone layout uses, paging included, so the body follows right after it
  // instead of thousands of pixels of directory.
  for (const width of [1099, 1024, 900, 768]) {
    await page.setViewportSize({ width, height: 768 });
    await expect(page.locator(".persona-subjects")).toBeHidden();
    const picker = page.locator(".persona-picker select");
    await expect(picker).toBeVisible();
    await expect(page.locator(".persona-picker")).toContainText("角色目录");
    const short = await page
      .locator(".persona-catalog")
      .evaluate((element) =>
        Math.round(element.getBoundingClientRect().height),
      );
    expect(
      short,
      `${width}px replaces the twenty cards instead of merely hiding them`,
    ).toBeLessThan(tall / 8);
    const stacked = await page.evaluate(() => {
      const catalog = document
        .querySelector(".persona-catalog")!
        .getBoundingClientRect();
      const detail = document
        .querySelector(".persona-detail")!
        .getBoundingClientRect();
      return {
        above: Math.round(detail.top) >= Math.round(catalog.bottom) - 1,
        reach: Math.round(
          document.querySelector(".persona-history")!.getBoundingClientRect()
            .top + window.scrollY,
        ),
      };
    });
    expect(
      stacked.above,
      `${width}px`.concat(" keeps the selector above the body"),
    ).toBe(true);
    expect(
      stacked.reach,
      `${width}px keeps the body within reach`,
    ).toBeLessThan(2 * 768);
    // The directory still pages twenty characters at a time, through the selector.
    await expect(page.locator(".persona-catalog .persona-pager")).toContainText(
      "共 22 个",
    );
    await noOverflow(page);
    if (width === 1024)
      await page.screenshot({
        path: testInfo.outputPath("layout-1024.png"),
        fullPage: true,
      });
  }
  // The compact selector is a real directory control at this width too: choosing from it re-reads.
  await page.setViewportSize({ width: 1024, height: 768 });
  await expect(rows(page)).toHaveCount(20);
  await page.locator(".persona-picker select").selectOption({ index: 1 });
  await expect(rows(page).first()).toBeVisible();
  await expect(rail(page, "可读")).toBeVisible();
  // <=767px: the same compact selector, every tool stacked, and the body within reach.
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".persona-subjects")).toBeHidden();
  const picker = page.locator(".persona-picker select");
  await expect(picker).toBeVisible();
  await expect(page.locator(".persona-picker")).toContainText("角色目录");
  const short = await page
    .locator(".persona-catalog")
    .evaluate((element) => Math.round(element.getBoundingClientRect().height));
  expect(
    short,
    "the twenty cards are replaced, not merely hidden",
  ).toBeLessThan(tall / 8);
  const reach = await page.evaluate(() => {
    const history = document
      .querySelector(".persona-history")!
      .getBoundingClientRect();
    return Math.round(history.top + window.scrollY);
  });
  expect(reach, "the body is within reach on a phone").toBeLessThan(2 * 844);
  await expect(page.locator(".persona-catalog .persona-pager")).toContainText(
    "共 22 个",
  );
  await page.screenshot({
    path: testInfo.outputPath("layout-390.png"),
    fullPage: true,
  });
  // The compact selector is a real, keyboard-usable directory control.
  await picker.selectOption({ index: 1 });
  await expect(rows(page).first()).toBeVisible();
  await picker.selectOption({ index: 0 });
  await expect(rows(page).first()).toBeVisible();
  // A comparison, when one is open, reads old above new on this width.
  await rows(page)
    .first()
    .getByRole("button", { name: "设为对比基线" })
    .click();
  await rows(page).nth(1).getByRole("button", { name: "与基线比较" }).click();
  await expect(
    page.locator(".persona-change .persona-sides").first(),
  ).toBeVisible();
  const sidesStacked = await page.evaluate(() => {
    const panel = document.querySelector(".persona-change .persona-sides")!;
    const boxes = [...panel.children].map((child) =>
      child.getBoundingClientRect(),
    );
    return boxes.length === 2 && boxes[1].top >= boxes[0].bottom - 1;
  });
  expect(sidesStacked).toBe(true);
  await page.setViewportSize({ width: 320, height: 640 });
  await noOverflow(page);
});

test("浅色与深色、四种宽度都不横向溢出，且没有 axe 违规", async ({
  page,
}, testInfo) => {
  await page.goto("/#/roles/persona");
  await login(page);
  await openFirst(page);
  await expect(rows(page)).toHaveCount(20);
  for (const theme of ["light", "dark"] as const) {
    await page.getByRole("button", { name: "外观设置", exact: true }).click();
    await page
      .getByRole("radio", {
        name: theme === "light" ? "浅色" : "深色",
        exact: true,
      })
      .check();
    await page.keyboard.press("Escape");
    expect(
      (
        await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
          .analyze()
      ).violations,
    ).toEqual([]);
    for (const size of [
      { width: 1440, height: 1000 },
      { width: 1024, height: 768 },
      { width: 390, height: 844 },
      { width: 320, height: 640 },
    ]) {
      await page.setViewportSize(size);
      await noOverflow(page);
      await page.screenshot({
        path: testInfo.outputPath(`personas-${theme}-${size.width}.png`),
        fullPage: true,
      });
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.screenshot({
      path: testInfo.outputPath(`personas-${theme}.png`),
      fullPage: true,
    });
    // 200% text is still readable without a horizontal scrollbar.
    await page.evaluate(() => {
      document.documentElement.style.fontSize = "200%";
    });
    await noOverflow(page);
    await page.evaluate(() => {
      document.documentElement.style.fontSize = "";
    });
  }
  // Reduced motion: the page keeps every fact and drops the movement.
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 390, height: 844 });
  await noOverflow(page);
  await page.screenshot({
    path: testInfo.outputPath("personas-reduced-motion.png"),
    fullPage: true,
  });
});
