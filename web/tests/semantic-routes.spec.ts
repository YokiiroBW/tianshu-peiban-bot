import { test, expect } from "@playwright/test";
import { modules, sectionPaths, resolveRoute, routeHref } from "../src/app/modules";

test("every current page has a semantic route and numeric product paths are absent", () => {
  for (const module of modules) {
    if (module.id === "room") continue;
    for (const [index, path] of sectionPaths[module.id].entries()) {
      if (module.sections[index] === null) continue;
      const route = resolveRoute(routeHref(module.id, index));
      expect(route?.module.id).toBe(module.id);
      expect(route?.section).toBe(index);
      expect(path).not.toMatch(/\/\d+$/);
    }
  }
  expect(resolveRoute("#/companion/1")).toBeNull();
  expect(resolveRoute("#/settings/2")).toBeNull();
});
