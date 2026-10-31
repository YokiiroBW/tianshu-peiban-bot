import { useState } from "react";
/** Each cursor stays with the exact authorized scene and collection. */
export function useMemoryCursor(scene: object, collection: string) {
  const key = JSON.stringify([scene, collection]);
  const [state, setState] = useState<{
    key: string;
    history: (string | null)[];
  }>({ key, history: [null] });
  const history = state.key === key ? state.history : [null];
  return {
    cursor: history.at(-1) ?? null,
    previous: history.length > 1,
    first: () => setState({ key, history: [null] }),
    back: () => setState({ key, history: history.slice(0, -1) }),
    next: (cursor: string) => setState({ key, history: [...history, cursor] }),
  };
}
