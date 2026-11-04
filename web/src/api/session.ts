import { apiJson, establishDataGeneration } from "./client";

export type SessionView = {
  authenticated: boolean;
  initialized: boolean;
  principal: {
    principal_id: string;
    display_name: string;
    permissions: string[];
  } | null;
  csrf_token: string | null;
  setup_token_required: boolean;
  data_generation: string;
};

/** Display conveniences are derived here; the server remains the session authority. */
export type AccountSession = SessionView & {
  csrf: string;
  username: string;
  onboarding: {
    state: "create_admin" | "ready" | "sign_in";
    credential_source: "account";
    setup_token_required: boolean;
  };
};

export function presentSession(view: SessionView): AccountSession {
  if (typeof view?.authenticated !== "boolean" || typeof view.initialized !== "boolean" || (view.csrf_token !== null && typeof view.csrf_token !== "string") || typeof view.data_generation !== "string" || !view.data_generation)
    throw new Error("会话数据不完整，请重新连接。");
  establishDataGeneration(view.data_generation);
  return {
    ...view,
    csrf: view.csrf_token ?? "",
    username: view.principal?.display_name ?? "",
    onboarding: {
      state: !view.initialized ? "create_admin" : view.authenticated ? "ready" : "sign_in",
      credential_source: "account",
      setup_token_required: view.setup_token_required,
    },
  };
}

export async function readSession(signal?: AbortSignal) {
  return presentSession(await apiJson<SessionView>("session", { signal }));
}
