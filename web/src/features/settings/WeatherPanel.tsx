import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { useDomainResource, actorScope } from "../../api/useDomainResource";
import {
  WeatherConnectionSettings,
  WeatherLocationSettings,
} from "../life/WeatherSettings";
import type { LifeState } from "../life/domainTypes";
import "./external.css";
export function WeatherPanel() {
  const { actor } = useRoleWorkspace();
  const state = useDomainResource<LifeState>(
    "life/state",
    actorScope(actor),
    !!actor,
  );
  return (
    <div className="domain-workspace">
      <WeatherConnectionSettings />
      {state.error && <p role="alert">{state.error}</p>}
      {state.data ? (
        <WeatherLocationSettings state={state.data} refresh={state.refresh} />
      ) : (
        !actor && (
          <p className="muted">在侧栏选择角色后，可保存角色的实际天气位置。</p>
        )
      )}
    </div>
  );
}
