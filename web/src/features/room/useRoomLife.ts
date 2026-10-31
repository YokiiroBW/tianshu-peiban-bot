import { useEffect, useState } from "react";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { useAuth } from "../../app/Auth";
import {
  useDomainResource,
  useDomainCommand,
} from "../../api/useDomainResource";
import { API_ROOT } from "../../api/client";
import type { MediaRef } from "../../components/MediaPreview";

type Activity = {
  id: string;
  title: string;
  kind: string;
  state: string;
  checkpoint_step: number;
  checkpoint_position: number;
  checkpoint_unit: string;
  waiting_reason: string | null;
};
type Outfit = { id: string; description: string; reference: MediaRef | null };
type State = {
  revision: number;
  timezone: string;
  activity: Activity | null;
  outfit: Outfit | null;
  missing: string[];
};

export function useRoomLife() {
  const workspace = useRoleWorkspace();
  const { session } = useAuth();
  const selector = {
    actor_id: workspace.actor,
    ...(workspace.conversation
      ? {
          conversation_id: workspace.conversation.conversation_id,
          ...(workspace.conversation.person_id
            ? { person_id: workspace.conversation.person_id }
            : {}),
        }
      : {}),
  };
  const state = useDomainResource<State>(
    "life/state",
    selector,
    !!workspace.actor,
    15000,
  );
  const room = useDomainResource<{
    revision: number;
    controls: Record<string, number>;
  }>("life/room", selector, !!workspace.actor);
  const access = useDomainResource<{ can_manage: boolean }>(
    "life/access",
    selector,
    !!workspace.actor,
  );
  const roomCommand = useDomainCommand("room:" + workspace.actor);
  async function saveRoom(controls: Record<string, number>, revision: number) {
    const result = await roomCommand.run(
      "life/room.save",
      { ...selector, controls },
      revision,
    );
    if (result) {
      await room.refresh();
      await state.refresh();
    }
    return result;
  }
  const [garment, setGarment] = useState<HTMLImageElement | null>(null);
  const [garmentError, setGarmentError] = useState("");
  const reference = state.data?.outfit?.reference;
  useEffect(() => {
    setGarment(null);
    setGarmentError("");
    if (!reference) return;
    const image = new Image();
    let disposed = false;
    image.onload = () => {
      if (!disposed) setGarment(image);
    };
    image.onerror = () => {
      if (!disposed) setGarmentError("当前穿搭参考原件暂时无法读取。");
    };
    image.src = `${API_ROOT}capabilities/media/${encodeURIComponent(reference.media_id)}?${new URLSearchParams(selector)}`;
    return () => {
      disposed = true;
      image.onload = null;
      image.onerror = null;
      image.src = "";
    };
  }, [
    reference?.media_id,
    workspace.actor,
    workspace.conversation?.conversation_id,
    session?.csrf,
  ]);
  const activity = state.data?.activity ?? null;
  const pose: "reading" | "resting" | "idle" | "away" = !state.data
    ? "away"
    : ["sleep", "rest"].includes(activity?.kind ?? "")
      ? "resting"
      : ["reading", "writing", "study"].includes(activity?.kind ?? "")
        ? "reading"
        : ["away", "outdoor"].includes(activity?.kind ?? "")
          ? "away"
          : "idle";
  return {
    room: room.data,
    roomCommand,
    saveRoom,
    canManage: access.data?.can_manage === true,
    access: { actor: workspace.actor, csrf: session?.csrf ?? "" },
    actors: workspace.roles.map((role) => ({
      actor_id: role.id,
      label: role.name,
    })),
    actor: workspace.actor,
    choose: workspace.select,
    current: state.data
      ? { ...state.data, activity: activity?.title ?? null }
      : null,
    activity,
    outfit: state.data?.outfit ?? null,
    pose,
    garment,
    error: state.error || room.error || access.error || garmentError,
    loading: state.loading,
    refresh: () => {
      void state.refresh();
      void room.refresh();
    },
  };
}
