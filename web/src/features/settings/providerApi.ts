import {
  GLOBAL_SCOPE,
  actorScope,
  useDomainCommand,
  useDomainResource,
} from "../../api/useDomainResource";
import { useAuth } from "../../app/Auth";
import type { ModelCatalog, ModelReceipt } from "./providerTypes";

export type ProbeResult = {
  verification: string;
  receipt?: ModelReceipt | null;
  catalog?: ModelCatalog;
};
export function useModelCatalog(actorId?: string) {
  return useDomainResource<ModelCatalog>(
    "models/catalog",
    actorId ? actorScope(actorId) : GLOBAL_SCOPE,
  );
}
export function useModelCommand(scope: string, actorId?: string) {
  const command = useDomainCommand<ModelCatalog>(
    scope,
    actorId ? "actor" : "global",
  );
  const { session } = useAuth();
  const manageable = !!session?.principal?.permissions.some(
    (permission) => permission === "*" || permission === "models.manage",
  );
  async function save(operation: string, body: object, revision: number) {
    return command.run(
      `models/${operation}`,
      { ...(actorId ? actorScope(actorId) : GLOBAL_SCOPE), ...body },
      revision,
    );
  }
  return { ...command, save, manageable };
}
