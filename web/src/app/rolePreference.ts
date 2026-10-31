/** This tab's role choice is a convenience; blocked storage must never stop an operation. */
export function readRolePreference(key: string) {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

export function saveRolePreference(key: string, role: string | null) {
  try {
    if (role === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, role);
  } catch {
    // The current view and server session remain usable without a browser cache.
  }
}
