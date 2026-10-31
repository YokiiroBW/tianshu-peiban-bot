// Optional coordinator-run live UI driver. No routing mocks, cookie import or permission changes.
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { resolve, isAbsolute, join } from "node:path";
import { createHash } from "node:crypto";
import { parseArgs } from "node:util";
import { pathToFileURL } from "node:url";
import { chromium } from "@playwright/test";

function check(condition) {
  if (!condition) throw new Error("ui_check_failed");
}
function digest(value) {
  return createHash("sha256").update(value).digest("hex");
}
async function credential(input, name) {
  const keys = [name, `${name}_env`, `${name}_file`].filter(
    (key) => key in input,
  );
  check(keys.length === 1 && !(name === "password" && keys[0] === name));
  const key = keys[0];
  let value;
  if (key.endsWith("_env")) {
    check(/^[A-Z_][A-Z0-9_]{0,127}$/.test(input[key]));
    value = process.env[input[key]];
  } else if (key.endsWith("_file")) {
    check(isAbsolute(input[key]));
    const raw = await readFile(input[key]);
    check(raw.length <= 2048);
    value = raw.toString("utf8").replace(/[\r\n]+$/, "");
  } else value = input[key];
  check(typeof value === "string" && value.length > 0 && value.length <= 256);
  return value;
}

async function post(page, operation, body) {
  return page.evaluate(
    async ({ operation, body }) => {
      const session = await fetch("/api/web/session", { cache: "no-store" });
      if (!session.ok) return null;
      const current = await session.json();
      if (!current.authenticated) return null;
      const result = await fetch(`/api/web/infrastructure/${operation}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": current.csrf,
        },
        body: JSON.stringify(body),
      });
      return result.ok ? result.json() : null;
    },
    { operation, body },
  );
}

// Can also be called by an explicitly authorized browser runner with its already logged-in page.
// The caller keeps the session; this function never exports cookies or reads another account.
export async function inspectAuthenticated(page, input, output, suffix) {
  await page.goto(`${input.origin}/#/home/1`);
  await page
    .getByRole("heading", { name: "Docker 容器", exact: true })
    .waitFor();
  const view = await post(page, "view", {});
  check(view?.configured);
  const node = view.nodes.find((row) => row.id === input.node_id);
  check(node?.enabled);
  const escaped = node.label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  await page
    .getByRole("button", { name: new RegExp(`^${escaped}`) })
    .first()
    .click();
  const listing = await post(page, "containers", { node_id: input.node_id });
  check(listing?.inventory.freshness === "current");
  const list = page.getByRole("region", { name: "容器列表", exact: true });
  await list
    .getByRole("heading", { name: `容器清单 ${listing.total} 个`, exact: true })
    .waitFor();
  check(
    (await list.locator(".infra-container-row").count()) ===
      Math.min(listing.total, 100),
  );
  if (listing.total) {
    await list.locator(".infra-container-row").first().click();
    await page.getByRole("complementary", { name: "容器详情" }).waitFor();
  }
  check(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  );
  await page.screenshot({
    path: join(output, `docker-${suffix}.png`),
    fullPage: true,
  });
  await page.goto(`${input.origin}/#/home/2`);
  await page
    .getByRole("heading", { name: "节点与网络", exact: true })
    .waitFor();
  await page
    .getByRole("button", { name: new RegExp(`^${escaped}`) })
    .first()
    .click();
  const detail = page.getByRole("region", {
    name: `${node.label}节点详情`,
    exact: true,
  });
  await detail
    .getByRole("heading", { name: node.label, exact: true })
    .waitFor();
  await detail
    .getByRole("heading", { name: "磁盘与文件系统", exact: true })
    .waitFor();
  const observed = await post(page, "node", { node_id: input.node_id });
  check(observed?.snapshot.freshness === "current");
  check(observed.snapshot.data.identity.node_id === input.node_id);
  await page.screenshot({
    path: join(output, `nodes-${suffix}.png`),
    fullPage: true,
  });
  return {
    viewport: suffix,
    containers: listing.inventory_total,
    node_sha256: digest(input.node_id),
    observed_at: observed.snapshot.observed_at,
    filesystems: observed.snapshot.data.filesystems.length,
  };
}

async function main() {
  const { values } = parseArgs({
    options: { input: { type: "string" }, output: { type: "string" } },
  });
  check(values.input && values.output);
  const output = resolve(values.output);
  await mkdir(output, { recursive: true, mode: 0o700 });
  let browser;
  const report = { schema_version: 1, status: "failed", views: [] };
  try {
    const raw = await readFile(values.input);
    check(raw.length <= 16384);
    const input = JSON.parse(raw);
    const origin = new URL(input.origin);
    check(
      ["http:", "https:"].includes(origin.protocol) &&
        input.origin === origin.origin &&
        /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(input.node_id),
    );
    const username = await credential(input, "username");
    const password = await credential(input, "password");
    const environment = Object.fromEntries(
      Object.entries(process.env).filter(
        ([key]) => ![input.username_env, input.password_env].includes(key),
      ),
    );
    browser = await chromium.launch({ headless: true, env: environment });
    for (const [suffix, viewport, theme] of [
      ["desktop-light", { width: 1440, height: 1000 }, "light"],
      ["mobile-dark", { width: 390, height: 844 }, "dark"],
    ]) {
      const context = await browser.newContext({
        viewport,
        ignoreHTTPSErrors: false,
        locale: "zh-CN",
        reducedMotion: "reduce",
      });
      try {
        const page = await context.newPage();
        let runtimeErrors = 0;
        page.on("pageerror", () => runtimeErrors++);
        await page.goto(`${input.origin}/#/home/1`);
        await page.getByLabel("管理员账号").fill(username);
        await page.getByLabel("密码", { exact: true }).fill(password);
        await page.getByRole("button", { name: "登录", exact: true }).click();
        await page
          .getByRole("heading", { name: "Docker 容器", exact: true })
          .waitFor();
        await page.evaluate((value) => {
          const current = JSON.parse(
            localStorage.getItem("tianshu.appearance") || "{}",
          );
          localStorage.setItem(
            "tianshu.appearance",
            JSON.stringify({ ...current, theme: value }),
          );
          document.documentElement.dataset.theme = value;
        }, theme);
        report.views.push(
          await inspectAuthenticated(page, input, output, suffix),
        );
        check(runtimeErrors === 0);
        await page
          .getByRole("button", { name: "退出登录", exact: true })
          .click();
      } finally {
        await context.close();
      }
    }
    report.status = "passed";
  } catch {
    report.code = "ui_check_failed";
  } finally {
    await browser?.close();
  }
  await writeFile(
    join(output, "receipt.json"),
    JSON.stringify(report, null, 2) + "\n",
    { mode: 0o600 },
  );
  process.stdout.write(
    JSON.stringify({ status: report.status, views: report.views.length }) +
      "\n",
  );
  process.exitCode = report.status === "passed" ? 0 : 1;
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href
) {
  await main();
}
