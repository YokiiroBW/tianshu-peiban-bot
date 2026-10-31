import { useEffect, useState } from "react";
import { useAuth } from "../../app/Auth";
import { integrationPost, readFailure } from "../../app/integrationApi";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { useLifeResource } from "../life/useLifeRuntime";
import { readContent } from "../life/runtimeApi";
export function useRoomLife() {
  const { session } = useAuth();
  const workspace = useRoleWorkspace();
  const csrf = session?.authenticated ? session.csrf : "";
  const [actors, setActors] = useState<{ actor_id: string; label?: string }[]>(
      [],
    ),
    [error, setError] = useState(""),
    [garment, setGarment] = useState<HTMLImageElement | null>(null),
    [garmentError, setGarmentError] = useState("");
  useEffect(() => {
    setActors([]);
    setError("");
    if (!csrf) return;
    const control = new AbortController();
    void integrationPost<{ items: typeof actors }>(
      "life/actors",
      { limit: 50, after_actor_id: null },
      csrf,
      control.signal,
    )
      .then((answer) => {
        if (control.signal.aborted) return;
        setActors(answer.items);
      })
      .catch((cause) => {
        if (!control.signal.aborted) setError(readFailure(cause));
      });
    return () => control.abort();
  }, [csrf]);
  const actor = actors.some((row) => row.actor_id === workspace.actor)
    ? workspace.actor
    : "";
  const access = { actor, csrf },
    state = useLifeResource(access, "state"),
    activities = useLifeResource(access, "activities"),
    outfits = useLifeResource(access, "outfits");
  const current = state.items[0],
    outfit = outfits.items.find((x) => x.id === current?.outfit_ref),
    activity = activities.items.find((x) => x.id === current?.activity_id);
  const reference =
    outfit?.reference && typeof outfit.reference === "object"
      ? outfit.reference
      : null;
  useEffect(() => {
    setGarment(null);
    setGarmentError("");
    if (!reference) return;
    const control = new AbortController();
    void readContent(
      access,
      reference,
      {
        unit: reference.coverage.unit,
        start: reference.coverage.start,
        end: reference.coverage.end,
      },
      control.signal,
    )
      .then((answer) => {
        if (control.signal.aborted) return;
        const representation = answer.representations.find(
          (x) => x.kind === "image",
        );
        if (!representation) return;
        const image = new Image();
        image.onload = () => {
          if (!control.signal.aborted) setGarment(image);
        };
        image.src = `data:${representation.media_type};base64,${representation.data_base64}`;
      })
      .catch((cause) => {
        if (!control.signal.aborted)
          setGarmentError(`当前穿搭原件：${readFailure(cause)}`);
      });
    return () => control.abort();
  }, [
    actor,
    csrf,
    reference?.object_id,
    reference?.version,
    reference?.sha256,
  ]);
  useEffect(() => {
    if (!actor) return;
    const timer = window.setInterval(() => {
      if (!document.hidden) {
        state.refresh();
        activities.refresh();
        outfits.refresh();
      }
    }, 15000);
    return () => clearInterval(timer);
  }, [actor, csrf]);
  const name = current?.activity ?? activity?.title ?? "";
  const pose: "reading" | "resting" | "idle" | "away" = !current
    ? "away"
    : /sleep|rest|睡|休息/i.test(name)
      ? "resting"
      : /reading|writing|study|读|写|学习/i.test(name)
        ? "reading"
        : /away|outdoor|外出|出门/i.test(name)
          ? "away"
          : "idle";
  return {
    access,
    actors,
    actor,
    choose: (id: string) => {
      workspace.select(id);
    },
    current,
    activity,
    outfit,
    pose,
    garment,
    error:
      error || state.error || activities.error || outfits.error || garmentError,
    loading: state.loading,
    refresh: () => {
      state.refresh();
      activities.refresh();
      outfits.refresh();
    },
  };
}
