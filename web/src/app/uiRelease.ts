import { createElement, useEffect, useState } from "react";
import { createPortal } from "react-dom";

declare const __TIANSHU_UI_BUILD_ID__: string | undefined;

export const UI_BUILD_ID: string =
  typeof __TIANSHU_UI_BUILD_ID__ === "string" && __TIANSHU_UI_BUILD_ID__.trim()
    ? __TIANSHU_UI_BUILD_ID__.trim()
    : "dev-local";

if (typeof document !== "undefined" && document.documentElement) {
  document.documentElement.dataset.uiBuildId = UI_BUILD_ID;
}

export function publicAssetUrl(
  relativePath: string,
  baseUrl: string = import.meta.env.BASE_URL || "/",
): string {
  const normalizedBase = baseUrl
    ? baseUrl.endsWith("/")
      ? baseUrl
      : `${baseUrl}/`
    : "/";
  const cleanPath = relativePath.replace(/^\/+/, "");
  return `${normalizedBase}${cleanPath}`;
}

export type UiVersionStatusPayload = {
  schema_version?: string;
  served_build_id?: string | null;
  served_source_sha?: string | null;
  served_mode?: string;
  requested_build_id?: string | null;
  activation_sequence?: number;
  compatible?: boolean;
  degraded?: boolean;
  degraded_reason?: string | null;
};

export function evaluateUiVersionChange(
  payload: unknown,
  currentBuildId: string = UI_BUILD_ID,
): string | null {
  if (!payload || typeof payload !== "object") return null;
  const record = payload as UiVersionStatusPayload;
  if (record.schema_version !== "tianshu.ui.status.v1") return null;
  if (record.compatible === false) return null;
  const served =
    typeof record.served_build_id === "string"
      ? record.served_build_id.trim()
      : "";
  if (!served) return null;
  if (served === currentBuildId) return null;
  if (
    currentBuildId === "dev-local" &&
    (record.served_mode === "builtin" || served.startsWith("builtin-"))
  ) {
    return null;
  }
  return served;
}

export const UI_VERSION_POLL_INTERVAL_MS = 60_000;

export function UiVersionMonitor({
  pollIntervalMs = UI_VERSION_POLL_INTERVAL_MS,
}: {
  pollIntervalMs?: number;
} = {}) {
  const [availableBuildId, setAvailableBuildId] = useState<string | null>(null);
  const [dismissedBuildId, setDismissedBuildId] = useState<string | null>(null);

  useEffect(() => {
    if (typeof document !== "undefined" && document.documentElement) {
      document.documentElement.dataset.uiBuildId = UI_BUILD_ID;
    }
    let alive = true;
    let inFlight = false;

    const checkVersion = async () => {
      if (!alive || inFlight) return;
      if (typeof document !== "undefined" && document.hidden) return;
      if (typeof navigator !== "undefined" && navigator.onLine === false)
        return;
      inFlight = true;
      try {
        const response = await fetch("/_ui/version", {
          method: "GET",
          credentials: "same-origin",
          cache: "no-store",
          headers: { Accept: "application/json" },
        });
        if (!response.ok || !alive) return;
        const data: unknown = await response.json();
        if (!alive) return;
        if (
          data &&
          typeof data === "object" &&
          (data as UiVersionStatusPayload).schema_version ===
            "tianshu.ui.status.v1"
        ) {
          const nextBuildId = evaluateUiVersionChange(data, UI_BUILD_ID);
          setAvailableBuildId(nextBuildId);
        }
      } catch {
        // Silent failure: version check errors must never disrupt the active page.
      } finally {
        inFlight = false;
      }
    };

    const onVisibilityChange = () => {
      if (!document.hidden) void checkVersion();
    };
    const onOnline = () => {
      void checkVersion();
    };
    const onFocus = () => {
      void checkVersion();
    };

    void checkVersion();
    const timer = window.setInterval(() => {
      void checkVersion();
    }, pollIntervalMs);

    document.addEventListener("visibilitychange", onVisibilityChange);
    window.addEventListener("online", onOnline);
    window.addEventListener("focus", onFocus);

    return () => {
      alive = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("online", onOnline);
      window.removeEventListener("focus", onFocus);
    };
  }, [pollIntervalMs]);

  if (!availableBuildId || availableBuildId === dismissedBuildId) {
    return null;
  }

  const host =
    typeof document !== "undefined"
      ? (document.querySelector(".notification-region") ?? document.body)
      : null;
  if (!host) return null;

  return createPortal(
    createElement(
      "aside",
      {
        className: "notification-card ui-version-notice",
        role: "status",
        "aria-live": "polite",
        "aria-label": "界面版本更新提示",
        "data-available-build-id": availableBuildId,
      },
      createElement(
        "strong",
        { className: "command-monitor-message" },
        "界面已发布新版本",
      ),
      createElement(
        "p",
        null,
        `当前页面可继续使用，刷新后将加载新版本（${availableBuildId}）。`,
      ),
      createElement(
        "div",
        { className: "command-monitor-actions" },
        createElement(
          "button",
          {
            type: "button",
            className: "text-link",
            onClick: () => window.location.reload(),
          },
          "刷新加载新版本",
        ),
        createElement(
          "button",
          {
            type: "button",
            className: "text-link",
            onClick: () => setDismissedBuildId(availableBuildId),
          },
          "稍后",
        ),
      ),
    ),
    host,
  );
}
