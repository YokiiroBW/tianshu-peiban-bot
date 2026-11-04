import { useState } from "react";
import { GLOBAL_SCOPE, useDomainResource } from "../../api/useDomainResource";
import type { ProjectsView, Project } from "./types";

/** Only the UI selection is shared; source/project truth remains server-owned. */
export function useProjectWorkspace() {
  const [selection, setSelection] = useState({
    project: "",
    cursor: null as string | null,
  });
  const cursor = selection.cursor;
  const scope = GLOBAL_SCOPE;
  const view = useDomainResource<ProjectsView>(
    "projects/view",
    { ...scope, cursor, limit: 40 },
    true,
  );
  const selected = selection.project;
  const card =
    view.data?.items.find((row) => row.project_id === selected) ??
    view.data?.items[0];
  const detail = useDomainResource<Project>(
    "projects/registry/get",
    { ...scope, project_id: card?.project_id },
    !!card,
  );
  return {
    scope,
    view,
    card,
    project: detail.data,
    detail,
    scene: scope,
    select: (project: string) =>
      setSelection({ project, cursor }),
    next: (cursor: string | null) =>
      setSelection({ project: "", cursor }),
    cursor,
  };
}
