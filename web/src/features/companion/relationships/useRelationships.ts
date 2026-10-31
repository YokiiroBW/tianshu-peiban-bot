import { useState } from "react";
import { useRoleWorkspace } from "../../../app/RoleWorkspace";
import {
  actorScope,
  useDomainCommand,
  useDomainResource,
} from "../../../api/useDomainResource";
import type { Page } from "../../../api/contracts";
import type { Projection, RelationshipTarget } from "./api";
export type { RelationshipTarget } from "./api";
export function useRelationships(selection?: RelationshipTarget) {
  const workspace = useRoleWorkspace();
  const [chosenRole, setRole] = useState(workspace.actor);
  const [chosenPerson, setPerson] = useState(
    workspace.conversation?.person_id ?? "",
  );
  const [cursor, setCursor] = useState<string | null>(null);
  const roleId = selection?.roleId ?? chosenRole;
  const personId = selection?.personId ?? chosenPerson;
  const people = useDomainResource<
    Page<{ person_id: string; display_name: string }>
  >(
    "identity/people",
    { ...actorScope(roleId), cursor, limit: 100 },
    !!roleId && !selection,
  );
  const target = {
    actor_id: roleId,
    person_id: personId,
    conversation_id: null,
  };
  const view = useDomainResource<Projection>(
    "memory/relationship",
    target,
    !!roleId && !!personId,
    15000,
  );
  const command = useDomainCommand<Projection>(
    `relationship:${roleId}:${personId}`,
    "actor",
  );
  async function save(value: object, revision: number) {
    const result = await command.run(
      "memory/relationship/save",
      { ...target, ...value },
      revision,
    );
    if (result) await view.refresh();
    return result;
  }
  return {
    roleId,
    personId,
    roles: workspace.roles,
    people,
    view,
    command,
    save,
    cursor,
    setCursor,
    chooseRole: (id: string) => {
      setRole(id);
      setPerson("");
      setCursor(null);
    },
    choosePerson: setPerson,
  };
}
