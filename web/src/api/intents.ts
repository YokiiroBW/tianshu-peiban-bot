/** In-memory request identities survive component navigation, never a second business state. */
const intents = new Map<string, string>();

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, canonical(item)]));
  return value;
}

export function commandIntent(session: string, scope: string, path: string, value: object, revision: number | null) {
  const key = JSON.stringify([session, scope, path, canonical(value), revision]);
  let id = intents.get(key);
  if (!id) { id = crypto.randomUUID(); intents.set(key, id); }
  return { key, id };
}

export function finishIntent(key: string) { intents.delete(key); }
window.addEventListener("tianshu:session-ended", () => intents.clear());
