import type { ComponentProps } from "react";
import { DetailCard } from "../../components/DetailCard";
import type { MediaController } from "./useMediaController";

/** Music dialogs share the page's floating notifications, scoped to this interaction. */
export function MediaDetailCard({
  media,
  requestSlots = [],
  onOpenChange,
  ...props
}: ComponentProps<typeof DetailCard> & {
  media: MediaController;
  requestSlots?: string[];
}) {
  return (
    <DetailCard
      {...props}
      onOpenChange={(open) => {
        media.clearFeedback();
        if (!open) requestSlots.forEach(media.cancel);
        onOpenChange?.(open);
      }}
    />
  );
}
