import { useAuth } from "../../app/Auth";
import { GLOBAL_SCOPE, useDomainResource } from "../../api/useDomainResource";
import type { IdentityDirectory } from "./types";
export function useIdentityDirectory() {
  const { session } = useAuth();
  const manageable = !!session?.principal?.permissions.some(
    (permission) => permission === "*" || permission === "identity.manage",
  );
  const view = useDomainResource<IdentityDirectory>(
    "identity/directory",
    GLOBAL_SCOPE,
    manageable,
  );
  return { ...view, manageable };
}
