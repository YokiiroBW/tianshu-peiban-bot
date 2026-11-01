import { test, expect } from "@playwright/test";
import { answer, apiPath, command, emptyPage, nativeSession } from "./native-fixture";

for (const mode of ["actor", "current"] as const) {
  test(`reaction ${mode} import declares its real range through the public API`, async ({ page }) => {
    await nativeSession(page);
    const imports: Record<string, unknown>[] = [];
    const scopes: URL[] = [];
    const media = {
      media_id: "source:upload:1", sha256: "original-gif", mime_type: "image/gif",
      size_bytes: 6, uri: "private-original",
    };
    await page.route(apiPath("conversation/messages"), (route) => answer(route, emptyPage));
    await page.route(apiPath("reactions/view"), (route) => answer(route, {
      packs: [], assets: [], next_cursor: null,
      index: { profile: null, pending: 0, failed: 0 },
    }));
    await page.route(apiPath("capabilities/upload"), (route) => {
      scopes.push(new URL(route.request().url()));
      const id = route.request().postData()!
        .match(/name="client_request_id"\r\n\r\n([^\r\n]+)/)![1];
      return answer(route, command({ client_request_id: id }, {
        artifact: { artifact_id: "uploaded", media: { ...media, media_id: "artifact:uploaded" },
          source_refs: [{ source_id: "upload", revision: 1 }], coverage: null, metadata: {} },
        media, source_ref: { source_id: "upload", revision: 1 },
      }));
    });
    await page.route(apiPath("reactions/packs.import"), (route) => {
      scopes.push(new URL(route.request().url()));
      const body = route.request().postDataJSON();
      imports.push(body);
      return answer(route, command(body, { id: "pack", revision: 2, asset_count: 1 }, 2));
    });
    await page.goto("/#/roles/chat");
    await expect(page.getByLabel("图片、音视频或文件", { exact: true })).toBeVisible();
    await page.evaluate(() => { window.location.hash = "#/media/reactions"; });
    await page.getByRole("button", { name: "导入图片、动图或ZIP图包", exact: true }).click();
    await expect(page.getByLabel("导入范围", { exact: true })).toHaveValue("actor");
    await expect(page.getByLabel("仅当前角色使用", { exact: true })).toHaveCount(0);
    await page.getByLabel("导入范围", { exact: true }).selectOption(mode);
    await page.getByLabel("图包名称", { exact: true }).fill("Synthetic originals");
    await page.getByLabel("原始图片、GIF或ZIP", { exact: true }).setInputFiles({
      name: "original.gif", mimeType: "image/gif", buffer: Buffer.from("GIF89a"),
    });
    await page.getByRole("button", { name: "上传并导入图包", exact: true }).click();
    await expect.poll(() => imports.length).toBe(1);
    expect(imports[0]).toMatchObject({
      name: "Synthetic originals", import_scope: mode, expected_revision: 0,
      files: [{ filename: "original.gif", media: { media_id: "artifact:uploaded" } }],
    });
    expect(imports[0]).not.toHaveProperty("role_only");
    expect(imports[0]).not.toHaveProperty("actor_ids");
    expect(scopes).toHaveLength(2);
    for (const scope of scopes) {
      expect(scope.searchParams.get("actor_id")).toBe("actor");
      if (mode === "actor") {
        expect(scope.searchParams.has("conversation_id")).toBe(false);
        expect(scope.searchParams.has("person_id")).toBe(false);
      } else {
        expect(scope.searchParams.get("conversation_id")).toBe("web-conversation");
      }
    }
  });
}
