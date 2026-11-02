import { test, expect } from "@playwright/test";
import { answer, apiPath, nativeSession } from "./native-fixture";

test.beforeEach(async ({ page }) => nativeSession(page));

for (const partial of [true, false]) {
  test(`Growth ${partial ? "explains unassessed bodies" : "keeps complete observations quiet"}`, async ({
    page,
  }) => {
    await page.route(apiPath("companion/growth/view"), (route) =>
      answer(route, {
        items: [],
        reflections: [],
        next_cursor: null,
        missing: [],
        can_review: true,
        review: {
          revision: 3,
          last_error: null,
          can_request_review: false,
          pending_inputs: 0,
          partial_inputs: partial ? 2 : 0,
          material_missing: partial
            ? ["discovery_metadata_only_body_not_read", "action_content_not_assessed"]
            : [],
          legacy_missing: [],
          batch: null,
        },
      }),
    );
    await page.goto("/#/roles/growth");
    const range = page.getByLabel("反思材料范围");
    if (partial) {
      await expect(range).toBeVisible();
      await expect(range.getByText(/有 2 条经历按有限材料整理/)).toBeVisible();
      await expect(range.getByText(/不包含正文阅读/)).toBeVisible();
      await expect(range.getByText(/完整内容尚未纳入反思/)).toBeVisible();
      await expect(page.getByText(/原执行恢复仍有/)).toHaveCount(0);
    } else {
      await expect(range).toHaveCount(0);
    }
  });
}
