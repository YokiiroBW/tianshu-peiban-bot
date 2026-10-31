import { integrationPost } from "../../app/integrationApi";
import type { LifeAccess } from "./runtimeApi";
import type { ComfyResponse, ComfyCompile, ImageIntent } from "./comfyTypes";

export async function readComfy<T>(
  access: LifeAccess,
  resource: "status" | "workflows" | "workflow" | "actor",
  signal: AbortSignal,
  workflow_id: string | null = null,
) {
  const response = await integrationPost<ComfyResponse<T>>(
    "life/image-backend/read",
    { actor_id: access.actor, resource, workflow_id },
    access.csrf,
    signal,
  );
  return response.result;
}

export async function manageComfy<T>(
  access: LifeAccess,
  operation: string,
  value: object,
  expected_revision: number,
  client_request_id: string,
  signal: AbortSignal,
) {
  const response = await integrationPost<ComfyResponse<T>>(
    "life/image-backend/manage",
    { actor_id: access.actor, operation, value, expected_revision, client_request_id },
    access.csrf,
    signal,
  );
  return response.result;
}

export async function compileComfy(
  access: LifeAccess,
  intent: ImageIntent,
  parameters: { width: number; height: number; steps?: number; seed?: number },
  assist_model: boolean,
  signal: AbortSignal,
) {
  const response = await integrationPost<ComfyResponse<ComfyCompile>>(
    "life/image-backend/compile",
    { actor_id: access.actor, intent, parameters, assist_model },
    access.csrf,
    signal,
  );
  return response.result;
}
