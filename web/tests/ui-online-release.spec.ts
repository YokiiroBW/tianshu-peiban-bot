import { test, expect } from "@playwright/test";
import { answer, nativeSession } from "./native-fixture";

test("publicAssetUrl resolves correctly", async ({ page }) => {
  await page.evaluate(() => {
    const { publicAssetUrl } = (window as any);
    if (publicAssetUrl('assets/img.png', '/_ui/releases/ui-123/') !== '/_ui/releases/ui-123/assets/img.png') throw new Error('Failed');
  });
});

test("version monitor notifies on mismatch", async ({ page }) => {
  await nativeSession(page);
  await page.route('/_ui/version', (route) => answer(route, {
    schema_version: 'tianshu.ui.status.v1',
    served_build_id: 'ui-external-new',
    compatible: true,
  }));
  await page.goto('/#/workbench');
  await expect(page.getByText('检测到新版本 UI')).toBeVisible();
});
