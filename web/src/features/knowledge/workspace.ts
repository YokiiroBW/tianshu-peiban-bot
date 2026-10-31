import { useState } from "react";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { actorScope, useDomainResource } from "../../api/useDomainResource";
import type { ProjectsView, Project } from "./types";

/** Only the UI selection is shared; source/project truth remains server-owned. */
export function useProjectWorkspace() {
  const roles = useRoleWorkspace();
  const [selection, setSelection] = useState({
    actor: "",
    project: "",
    cursor: null as string | null,
  });
  const cursor = selection.actor === roles.actor ? selection.cursor : null;
  const scope = actorScope(roles.actor);
  const view = useDomainResource<ProjectsView>(
    "projects/view",
    { ...scope, cursor, limit: 40 },
    !!roles.actor,
  );
  const selected = selection.actor === roles.actor ? selection.project : "";
  const card =
    view.data?.items.find((row) => row.project_id === selected) ??
    view.data?.items[0];
  const detail = useDomainResource<Project>(
    "projects/registry/get",
    { ...scope, project_id: card?.project_id },
    !!card,
  );
  return {
    actor: roles.actor,
    scope,
    view,
    card,
    project: detail.data,
    detail,
    scene: detail.data
      ? {
          actor_id: roles.actor,
          person_id: null,
          conversation_id: detail.data.conversation_id,
        }
      : scope,
    select: (project: string) =>
      setSelection({ actor: roles.actor, project, cursor }),
    next: (cursor: string | null) =>
      setSelection({ actor: roles.actor, project: "", cursor }),
    cursor,
  };
}
