import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";

const source = stripTypeScriptTypes(
  readFileSync(new URL("../src/api/client.ts", import.meta.url), "utf8"),
  { mode: "transform" },
);

test.beforeEach(async ({ page }) => {
  await page.route("**/__transport", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: "<!doctype html><html lang='zh-CN'><title>隔离传输用例</title><body></body></html>",
    }),
  );
  await page.goto("/__transport");
});

test("the shared client revalidates an expired session and never retries an uncertain command", async ({
  page,
}) => {
  const result = await page.evaluate(async (source) => {
    const { apiFetch, apiRead, apiCommandData } = await import(
      URL.createObjectURL(new Blob([source], { type: "text/javascript" }))
    );
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
    const { apiCommand } = await import(
      URL.createObjectURL(new Blob([source], { type: "text/javascript" }))
    );
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
    const { apiCommand } = await import(
      URL.createObjectURL(new Blob([source], { type: "text/javascript" }))
    );
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
