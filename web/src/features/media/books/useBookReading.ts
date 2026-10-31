import { apiJson } from "../../../api/client";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "../../../app/Auth";
import { requestId } from "../../../app/requestId";
import type { MediaController } from "../useMediaController";
import type { ReaderState, ReadingView } from "./audioTypes";

export type ReadingPreference = {
  sharing: boolean;
  binding: string;
  profileId: string;
};

export function useBookReading(
  media: MediaController,
  bookId: string,
  chapterId: string,
  hash: string,
  preference: ReadingPreference,
  audioJob?: string,
) {
  const { session } = useAuth();
  const [reader, setReader] = useState<ReaderState | null>(null);
  const [choices, setChoices] = useState<ReadingView["choices"]>([]);
  const [sharing, setSharing] = useState(false);
  const [binding, setBinding] = useState("");
  const [synced, setSynced] = useState(true);
  const current = useRef({ media, csrf: session?.csrf ?? "" });
  current.current = { media, csrf: session?.csrf ?? "" };
  const state = useRef({
    reader: null as ReaderState | null,
    offset: 0,
    seconds: 0,
    sharing: false,
    binding: "",
    pending: false,
  });

  function close(id: string) {
    // keepalive covers page navigation; TTL covers an offline browser or abrupt termination.
    void apiJson("books/reading/close", {
      body: { reader_id: id, client_request_id: requestId(), expected_revision: null },
      csrf: current.current.csrf, keepalive: true,
    }).catch(() => undefined);
  }
  async function save() {
    const data = state.current;
    if (!data.reader || data.pending) return;
    data.pending = true;
    const [conversation, actor] = data.binding.split("|");
    const id = data.reader.reader_id;
    const sequence = ++data.reader.sequence;
    const sentSharing = data.sharing;
    const sentBinding = data.binding;
    try {
      const result = await current.current.media.request<{
        reader: ReaderState;
      }>("book-reading-progress", "books/reading/progress", {
        reader_id: id,
        sequence,
        offset: data.offset,
        seconds: data.seconds,
        sharing: data.sharing,
        conversation,
        actor,
        client_request_id: requestId(),
      });
      if (state.current === data) setSynced(!!result);
    } finally {
      data.pending = false;
      if (
        state.current === data &&
        (sentSharing !== data.sharing || sentBinding !== data.binding)
      )
        void save();
    }
  }
  useEffect(() => {
    let stopped = false;
    setReader(null);
    setSharing(false);
    setSynced(true);
    const data = {
      reader: null as ReaderState | null,
      offset: 0,
      seconds: 0,
      sharing: false,
      binding: "",
      pending: false,
    };
    state.current = data;
    async function open() {
      const view = await current.current.media.request<ReadingView>(
        "book-reading-view",
        "books/reading/view",
        { book_id: bookId },
      );
      if (stopped || !view) return;
      setChoices(view.choices);
      const first = view.choices.find((c) => c.actors.length);
      const available = view.choices.flatMap((c) =>
        c.actors.map((actor) => `${c.id}|${actor}`),
      );
      if (!available.includes(preference.binding)) preference.sharing = false;
      data.binding = available.includes(preference.binding)
        ? preference.binding
        : first
          ? `${first.id}|${first.actors[0]}`
          : "";
      preference.binding = data.binding;
      setBinding(data.binding);
      const result = await current.current.media.request<{
        reader: ReaderState;
      }>("book-reading-open", "books/reading/open", {
        book_id: bookId,
        chapter_id: chapterId,
        content_hash: hash,
        audio_job: audioJob,
        expected_revision: view.reader?.revision ?? 0,
        client_request_id: requestId(),
      });
      if (!result) {
        if (!stopped) setSynced(false);
        return;
      }
      if (stopped) {
        close(result.reader.reader_id);
        return;
      }
      data.reader = { ...result.reader };
      data.offset = result.reader.offset;
      data.seconds = result.reader.seconds;
      setReader(result.reader);
      data.sharing = preference.sharing && !!data.binding && !document.hidden;
      setSharing(data.sharing);
      if (data.sharing) {
        setSynced(false);
        void save();
      }
    }
    void open();
    const timer = setInterval(() => {
      if (!document.hidden) void save();
    }, 10000);
    const hidden = () => {
      if (document.hidden && state.current === data) {
        data.sharing = false;
        preference.sharing = false;
        setSharing(false);
        void save();
      }
    };
    const leave = () => {
      if (data.reader) close(data.reader.reader_id);
    };
    document.addEventListener("visibilitychange", hidden);
    window.addEventListener("pagehide", leave);
    return () => {
      stopped = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", hidden);
      window.removeEventListener("pagehide", leave);
      leave();
    };
  }, [bookId, chapterId, hash, audioJob]);

  return {
    reader,
    choices,
    sharing,
    binding,
    synced,
    move: (offset: number, seconds: number) => {
      state.current.offset = offset;
      state.current.seconds = seconds;
    },
    save,
    share: (enabled: boolean) => {
      preference.sharing = enabled;
      state.current.sharing = enabled;
      setSharing(enabled);
      void save();
    },
    bind: (value: string) => {
      preference.binding = value;
      state.current.binding = value;
      setBinding(value);
      if (state.current.sharing) void save();
    },
  };
}
