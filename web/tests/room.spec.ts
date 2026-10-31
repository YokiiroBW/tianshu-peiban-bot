import { test, expect } from "@playwright/test";
import {
  approach,
  automaticRoom,
  daylight,
  localHour,
  resolveRoom,
} from "../src/features/room/environment";

test("room curve stays continuous and manual overrides survive clock changes", () => {
  expect(localHour(new Date("2026-09-14T00:00:00Z"))).toBe(8);
  expect(daylight(14)).toBeGreaterThan(daylight(18.5));
  for (const boundary of [0, 6, 19, 24])
    expect(
      Math.abs(daylight(boundary - 0.0001) - daylight(boundary + 0.0001)),
    ).toBeLessThan(0.001);
  const held = { window: 0, desk: 0.35, blackout: 1 };
  for (const hour of [0, 8, 14, 23])
    expect(resolveRoom(hour, held)).toMatchObject(held);
  expect(automaticRoom(23).desk).toBeGreaterThan(automaticRoom(14).desk);
  const halfSteps = approach(approach(1, 0, 0.5, 1.5), 0, 0.5, 1.5);
  expect(halfSteps).toBeCloseTo(approach(1, 0, 1, 1.5));
  expect(halfSteps).toBeGreaterThan(0);
});
