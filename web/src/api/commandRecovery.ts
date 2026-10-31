import { apiRead, type CommandTarget } from "./client";
import type { CommandResult, Receipt } from "./contracts";

export type OriginalCommand = {
  client_request_id: string;
  operation?: string;
  action_id?: string | null;
  attempt_id?: string | null;
  capability_id?: string;
  target?: CommandTarget;
};
export type ActionView = {
  action: Record<string, unknown>;
  attempt: Record<string, unknown>;
  receipt: Receipt | null;
  result_revision: number;
  result_observed_at: string | null;
};
const capabilityDomains = new Set([
  "capabilities",
  "plugins",
  "content",
  "images",
  "speech",
  "reactions",
  "media",
  "books",
  "music",
  "comics",
  "devices",
  "infrastructure",
  "weather",
  "resources",
  "channels",
  "projects",
]);
const capabilityAliases: Record<string, string> = {
  "channels/connections/save": "channels.configure",
  "channels/connections/probe": "channels.probe",
  "channels/actions/invoke": "channels.api",
  "content/video.observe": "video.observe",
  "devices/control": "device.control",
};
const nativeOperations: Record<string, string> = {
  "companion/roles/create": "companion.roles.save",
  "companion/growth/revoke": "companion.growth.revoked",
  "companion/growth/restore": "companion.growth.active",
  "life/routine.save": "life.routines.save",
  "life/contact-preferences.save": "life.contact_preferences.save",
  "life/activities.pause": "life.activities.paused",
  "life/activities.resume": "life.activities.running",
  "life/activities.cancel": "life.activities.cancelled",
  "life/reading/pause": "life.reading.paused",
  "life/reading/resume": "life.reading.reading",
  "life/reading/close": "life.reading.closed",
};

export function taskHref(value: OriginalCommand) {
  const query = new URLSearchParams();
  if (value.action_id) query.set("action", value.action_id);
  else if (value.attempt_id) query.set("attempt", value.attempt_id);
  if (value.client_request_id)
    query.set("client_request_id", value.client_request_id);
  if (value.operation) query.set("operation", value.operation);
  if (value.capability_id) query.set("capability_id", value.capability_id);
  for (const [key, selector] of Object.entries(value.target ?? {}))
    query.set(key, selector ?? "");
  return `#/system/tasks?${query}`;
}

/** Reads only the owner's original identity; never replays a command. */
export async function recoverCommand(
  value: OriginalCommand,
  csrf: string,
  signal: AbortSignal,
): Promise<{
  found: boolean;
  command: CommandResult<unknown> | null;
  action: ActionView | null;
}> {
  const target = value.target ?? {
    actor_id: null,
    person_id: null,
    conversation_id: null,
  };
  if (value.action_id) {
    const action = await apiRead<ActionView>(
      "capabilities/get",
      { ...target, action_id: value.action_id },
      csrf,
      signal,
    );
    return { found: true, action, command: null };
  }
  const operation = value.operation?.split("?")[0];
  if (!operation) return { found: false, command: null, action: null };
  if (capabilityDomains.has(operation.split("/")[0])) {
    const capability =
      value.capability_id ??
      capabilityAliases[operation] ??
      operation.replaceAll("/", ".");
    const action = await apiRead<ActionView | null>(
      "capabilities/find",
      {
        ...target,
        capability_id: capability,
        client_request_id: value.client_request_id,
      },
      csrf,
      signal,
    );
    return { found: action !== null, action, command: null };
  }
  const answer = await apiRead<{
    found: boolean;
    command: CommandResult<unknown> | null;
  }>(
    "commands/get",
    {
      ...target,
      operation:
        nativeOperations[operation] ??
        (/^models\/routes\/[^/]+\/probe$/.test(operation)
          ? "models.probe"
          : /^models\/providers\/[^/]+\/models$/.test(operation)
            ? "models.providers.models"
            : /^models\/providers\/[^/]+\/delete$/.test(operation)
              ? "models.providers.delete"
              : /^models\/routes\/[^/]+\/delete$/.test(operation)
                ? "models.routes.delete"
                : operation.replaceAll("/", ".")),
      client_request_id: value.client_request_id,
    },
    csrf,
    signal,
  );
  return { ...answer, action: null };
}
