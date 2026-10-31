import { integrationPost } from "../../../app/integrationApi";
import type {
  CredentialChange,
  CredentialView,
  SkillAccess,
  SkillsResponse,
} from "./types";

export async function readSkills(access: SkillAccess, signal: AbortSignal) {
  const response = await integrationPost<SkillsResponse>(
    "skills/read",
    { actor_id: access.actor, resource: "list" },
    access.csrf,
    signal,
  );
  return response.result;
}

export async function manageSkills(
  access: SkillAccess,
  operation: string,
  value: object,
  expected_revision: number,
  client_request_id: string,
  signal: AbortSignal,
) {
  const response = await integrationPost<SkillsResponse>(
    "skills/manage",
    { actor_id: access.actor, operation, value, expected_revision, client_request_id },
    access.csrf,
    signal,
  );
  return response.result;
}

export function credentialStatus(
  access: SkillAccess,
  target: { skill_id: string } | { source_id: string },
  signal: AbortSignal,
) {
  return integrationPost<CredentialView>(
    "skills/credentials",
    { actor_id: access.actor, ...target },
    access.csrf,
    signal,
  );
}

export async function configureSkills(
  access: SkillAccess,
  operation: "skill.update" | "source.configure",
  value: object,
  credential: CredentialChange,
  catalog_revision: number | null,
  expected_revision: number,
  client_request_id: string,
  signal: AbortSignal,
) {
  const response = await integrationPost<SkillsResponse>(
    "skills/configure",
    {
      actor_id: access.actor,
      operation,
      value,
      credential,
      catalog_revision,
      expected_revision,
      client_request_id,
    },
    access.csrf,
    signal,
  );
  return response.result;
}
