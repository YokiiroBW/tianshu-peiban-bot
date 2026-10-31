import { test, expect } from "@playwright/test";
import { createHash } from "node:crypto";
import { contentHash } from "../src/features/life/contentHash";

test("LAN upload SHA-256 equals native hashes across UTF-8 and block boundaries", async () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  const samples = [
    "",
    "abc",
    "中文原件\n窗边阅读",
    ...[55, 56, 63, 64, 65, 1000000].map((length) => "a".repeat(length)),
  ];
  try {
    for (const fallback of [false, true]) {
      if (fallback)
        Object.defineProperty(globalThis, "crypto", {
          configurable: true,
          value: { subtle: undefined },
        });
      for (const sample of samples) {
        const bytes = new TextEncoder().encode(sample);
        expect(await contentHash(bytes.buffer)).toBe(
          createHash("sha256").update(sample).digest("hex"),
        );
      }
    }
  } finally {
    if (descriptor) Object.defineProperty(globalThis, "crypto", descriptor);
  }
});
