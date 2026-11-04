import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import { answer, command, nativeSession } from "./native-fixture";

const requestIdModule = `data:text/javascript;base64,${Buffer.from(
  stripTypeScriptTypes(
    readFileSync(new URL("../src/app/requestId.ts", import.meta.url), "utf8"),
    { mode: "transform" },
  ),
).toString("base64")}`;
function browserSource(path: string) {
  return stripTypeScriptTypes(
    readFileSync(new URL(path, import.meta.url), "utf8"),
    { mode: "transform" },
  ).replaceAll('"../app/requestId"', JSON.stringify(requestIdModule));
}
const source = browserSource("../src/api/client.ts");
const intentsSource = browserSource("../src/api/intents.ts");
// TEST-NET is not a trustworthy loopback origin. Routes keep all traffic local.
const lanOrigin = "http://192.0.2.1:4173";
const uuidV4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

test.beforeEach(async ({ page }) => {
  await page.route("**/__transport", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: "<!doctype html><html lang='zh-CN'><title>隔离传输用例</title><body></body></html>",
    }),
  );
  await page.goto("/__transport");
});

test("native media URLs carry the generation without changing external URLs", async ({ page }) => {
  const result = await page.evaluate(async (source) => {
    const { establishDataGeneration, nativeResourceUrl } = await import(
      URL.createObjectURL(new Blob([source], { type: "text/javascript" }))
    );
    establishDataGeneration("fixture-generation");
    return {
      internal: nativeResourceUrl("/api/v1/media/books/files/book/epub?v=2"),
      external: nativeResourceUrl("https://example.test/image.png"),
    };
  }, source);
  expect(result.internal).toContain("v=2");
  expect(result.internal).toContain("data_generation=fixture-generation");
  expect(result.external).toBe("https://example.test/image.png");
});

test("LAN HTTP login submits without secure-context randomUUID", async ({
  page,
}) => {
  await page.route(`${lanOrigin}/**`, async (route) => {
    const url = new URL(route.request().url());
    const response = await route.fetch({
      url: `http://127.0.0.1:4173${url.pathname}${url.search}`,
    });
    await route.fulfill({ response });
  });
  await nativeSession(page);
  let authenticated = false;
  const session = () => ({
    authenticated,
    initialized: true,
    setup_token_required: false,
    principal: authenticated
      ? { principal_id: "author", display_name: "测试作者", permissions: ["*"] }
      : null,
    csrf_token: "synthetic-csrf",
    data_generation: "fixture-generation",
  });
  const submissions: Record<string, unknown>[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/v1/session", (route) => answer(route, session()));
  await page.route("**/api/v1/auth/login", (route) => {
    const body = route.request().postDataJSON();
    submissions.push(body);
    authenticated = true;
    return answer(route, command(body, session()));
  });
  await page.goto(`${lanOrigin}/#/login`);
  expect(
    await page.evaluate(() => ({
      secure: window.isSecureContext,
      randomUUID: typeof crypto.randomUUID,
      getRandomValues: typeof crypto.getRandomValues,
    })),
  ).toEqual({
    secure: false,
    randomUUID: "undefined",
    getRandomValues: "function",
  });
  await page.getByLabel("账号", { exact: true }).fill("lan-author");
  await page.getByLabel("密码", { exact: true }).fill("synthetic-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(`${lanOrigin}/#/workbench`);
  expect(submissions).toHaveLength(1);
  expect(submissions[0]).toMatchObject({
    username: "lan-author",
    password: "synthetic-password",
    expected_revision: null,
  });
  expect(submissions[0].client_request_id).toMatch(uuidV4);
  expect(errors).toEqual([]);
});

test("LAN HTTP commands keep the same intent after an uncertain submission", async ({
  page,
}) => {
  await page.goto(`${lanOrigin}/__transport`);
  const result = await page.evaluate(
    async ({ source, intentsSource }) => {
      const { commandInput, apiCommand, establishDataGeneration } = await import(
        URL.createObjectURL(new Blob([source], { type: "text/javascript" }))
      );
      establishDataGeneration("fixture-generation");
      const { commandIntent, finishIntent } = await import(
        URL.createObjectURL(
          new Blob([intentsSource], { type: "text/javascript" }),
        )
      );
      const input = { title: "一次明确约定", state: "planned" };
      const first = commandIntent(
        "session",
        "actor",
        "life/commitments.save",
        input,
        4,
      );
      const generated = commandInput(4);
      const explicit = commandInput(4, "original-explicit-id");
      const originalFetch = window.fetch;
      const submitted: string[] = [];
      let outcome = "";
      let uncertainId = "";
      window.fetch = async (_url, options) => {
        submitted.push(JSON.parse(options.body as string).client_request_id);
        throw new TypeError("connection closed after submission");
      };
      try {
        await apiCommand(
          "life/commitments.save",
          { ...input, ...commandInput(4, first.id) },
          "synthetic-csrf",
          new AbortController().signal,
        );
      } catch (error) {
        outcome = error.executionState;
        uncertainId = error.details.client_request_id;
      } finally {
        window.fetch = originalFetch;
      }
      const retry = commandIntent(
        "session",
        "actor",
        "life/commitments.save",
        { state: "planned", title: "一次明确约定" },
        4,
      );
      finishIntent(first.key);
      const next = commandIntent(
        "session",
        "actor",
        "life/commitments.save",
        input,
        4,
      );
      return {
        secure: window.isSecureContext,
        randomUUID: typeof crypto.randomUUID,
        generated,
        explicit,
        first: first.id,
        retry: retry.id,
        next: next.id,
        outcome,
        uncertainId,
        submitted,
      };
    },
    { source, intentsSource },
  );
  expect(result.secure).toBe(false);
  expect(result.randomUUID).toBe("undefined");
  expect(result.generated.client_request_id).toMatch(uuidV4);
  expect(result.generated.expected_revision).toBe(4);
  expect(result.explicit.client_request_id).toBe("original-explicit-id");
  expect(result.first).toMatch(uuidV4);
  expect(result.retry).toBe(result.first);
  expect(result.uncertainId).toBe(result.first);
  expect(result.outcome).toBe("unknown");
  expect(result.submitted).toEqual([result.first]);
  expect(result.next).toMatch(uuidV4);
  expect(result.next).not.toBe(result.first);
});

test("the shared client revalidates an expired session and never retries an uncertain command", async ({
  page,
}) => {
  const result = await page.evaluate(async (source) => {
    const { apiFetch, apiRead, apiCommandData, establishDataGeneration } = await import(
      URL.createObjectURL(new Blob([source], { type: "text/javascript" }))
    );
    establishDataGeneration("fixture-generation");
    const original = window.fetch;
    let calls = 0;
    let lost = 0;
    window.addEventListener("tianshu:session-lost", () => lost++);
    window.fetch = async () => {
      calls++;
      if (calls === 1)
        return new Response(JSON.stringify({ items: ["allowed"] }), {
          headers: { "content-type": "application/json" },
        });
      if (calls === 2)
        return new Response(
          JSON.stringify({
            error: {
              code: "session_expired",
              message: "登录已过期",
              details: null,
              request_id: "request-2",
            },
          }),
          { status: 401, headers: { "content-type": "application/json" } },
        );
      throw new TypeError("connection closed after submission");
    };
    try {
      const response = await apiFetch("memory/claims");
      const unread = !response.bodyUsed;
      const items = await response.json();
      let expired = "";
      try {
        await apiRead(
          "life/commitments",
          {},
          "csrf",
          new AbortController().signal,
        );
      } catch (cause) {
        expired = cause.code;
      }
      let outcome = "";
      try {
        await apiCommandData(
          "images/generate",
          { client_request_id: "original-action", expected_revision: null },
          "csrf",
          new AbortController().signal,
        );
      } catch (cause) {
        outcome = cause.executionState;
      }
      return { unread, items, expired, outcome, calls, lost };
    } finally {
      window.fetch = original;
    }
  }, source);
  expect(result).toEqual({
    unread: true,
    items: { items: ["allowed"] },
    expired: "session_expired",
    outcome: "unknown",
    calls: 3,
    lost: 1,
  });
});

test("an unknown receipt retains its action identity and null usage", async ({
  page,
}) => {
  const result = await page.evaluate(async (source) => {
    const { apiCommand, establishDataGeneration } = await import(
      URL.createObjectURL(new Blob([source], { type: "text/javascript" }))
    );
    establishDataGeneration("fixture-generation");
    window.fetch = async () =>
      new Response(
        JSON.stringify({
          client_request_id: "intent",
          revision: null,
          action_id: "action",
          attempt_id: "attempt",
          receipt: {
            receipt_id: "receipt",
            action_id: "action",
            attempt_id: "attempt",
            execution: "unknown",
            effect: "unknown",
            delivery: "not_requested",
            artifacts: [],
            coverage: null,
            result: {},
            error_code: null,
          },
          data: { usage: null },
        }),
        { headers: { "content-type": "application/json" } },
      );
    return apiCommand(
      "speech/preview",
      { client_request_id: "intent", expected_revision: null },
      "csrf",
      new AbortController().signal,
    );
  }, source);
  expect(result.action_id).toBe("action");
  expect(result.attempt_id).toBe("attempt");
  expect(result.receipt.execution).toBe("unknown");
  expect(result.data.usage).toBeNull();
});

test("write cancellation and a truncated successful body retain the original intent", async ({
  page,
}) => {
  const result = await page.evaluate(async (source) => {
    const { apiCommand, establishDataGeneration } = await import(
      URL.createObjectURL(new Blob([source], { type: "text/javascript" }))
    );
    establishDataGeneration("fixture-generation");
    let calls = 0;
    const errors: { code: string; state: string; id: string }[] = [];
    const controller = new AbortController();
    window.fetch = async () => {
      calls++;
      controller.abort();
      throw new DOMException("Aborted", "AbortError");
    };
    try {
      await apiCommand(
        "images/generate",
        { client_request_id: "original-cancelled-wait", expected_revision: 4 },
        "csrf",
        controller.signal,
      );
    } catch (error) {
      errors.push({
        code: error.code,
        state: error.executionState,
        id: error.details.client_request_id,
      });
    }
    window.fetch = async () => {
      calls++;
      return new Response('{"client_request_id":', {
        headers: { "content-type": "application/json" },
      });
    };
    try {
      await apiCommand(
        "life/commitments.save",
        { client_request_id: "original-truncated", expected_revision: 5 },
        "csrf",
        new AbortController().signal,
      );
    } catch (error) {
      errors.push({
        code: error.code,
        state: error.executionState,
        id: error.details.client_request_id,
      });
    }
    return { calls, errors };
  }, source);
  expect(result).toEqual({
    calls: 2,
    errors: [
      {
        code: "outcome_unknown",
        state: "unknown",
        id: "original-cancelled-wait",
      },
      { code: "outcome_unknown", state: "unknown", id: "original-truncated" },
    ],
  });
});
