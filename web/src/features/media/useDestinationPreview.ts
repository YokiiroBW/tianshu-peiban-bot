import { useEffect, useRef, useState } from "react";
import type { DestinationMode, DestinationPreview, Source } from "./types";
import type { MediaController } from "./useMediaController";

type Input = {
  subscription_id: string | null;
  label: string;
  source: Source;
  account_id: string | null;
  target_id: string;
  destination_mode: DestinationMode;
  destination_subdirectory: string;
};
type Result = {
  key: string;
  status: "pending" | "ready" | "failed";
  preview?: DestinationPreview;
};

/** Source naming and preservation belong to the media owner, including on edit. */
export function useDestinationPreview(
  input: Input | null,
  media: MediaController,
) {
  const key = input ? JSON.stringify(input) : "";
  const { request, cancel, clearError } = media;
  const generation = useRef(0);
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<Result | null>(null);
  useEffect(() => {
    const mine = ++generation.current;
    cancel("destination-preview");
    clearError("destination-preview");
    if (!key) return;
    setResult({ key, status: "pending" });
    const timer = setTimeout(() => {
      void request<DestinationPreview>(
        "destination-preview",
        "subscriptions/destination-preview",
        JSON.parse(key),
      ).then((preview) => {
        if (mine !== generation.current) return;
        const valid =
          preview &&
          ["source", "custom"].includes(preview.destination_mode) &&
          typeof preview.destination_subdirectory === "string" &&
          (preview.destination_directory === null ||
            typeof preview.destination_directory === "string");
        setResult(
          valid ? { key, status: "ready", preview } : { key, status: "failed" },
        );
      });
    }, 180);
    return () => {
      generation.current += 1;
      clearTimeout(timer);
      cancel("destination-preview");
    };
  }, [key, request, cancel, clearError, attempt]);

  return {
    status: !key ? "idle" : result?.key === key ? result.status : "pending",
    preview: result?.key === key ? result.preview : undefined,
    retry: () => setAttempt((current) => current + 1),
  };
}
