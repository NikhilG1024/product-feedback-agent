/** Server-only local demo auth policy. Never import this module from browser code. */
export type LocalAuthState = { enabled: boolean; error: string | null; pm: string; reviewer: string };

export function safeLocalTarget(target: string): boolean {
  return target === "http://127.0.0.1:8000" || target === "http://localhost:8000";
}

export function isLoopback(address?: string): boolean {
  return address === "127.0.0.1" || address === "::1" || address === "::ffff:127.0.0.1";
}

export function localAuthState(command: string, target: string, env: Record<string, string | undefined>): LocalAuthState {
  const disabled = { enabled: false, error: null, pm: "", reviewer: "" };
  if (command !== "serve" || env.LOCAL_DEMO_AUTH !== "1") return disabled;
  if (!safeLocalTarget(target)) return { ...disabled, error: "Local demo auth requires the loopback API on port 8000." };
  if (!env.DEMO_PM_TOKEN?.trim() || !env.DEMO_REVIEWER_TOKEN?.trim())
    return { ...disabled, error: "Local demo tokens are missing from the server environment." };
  return { enabled: true, error: null, pm: env.DEMO_PM_TOKEN, reviewer: env.DEMO_REVIEWER_TOKEN };
}

export function proxyCredentialFor(state: LocalAuthState, address: string | undefined, method: string | undefined, url: string | undefined): string | null {
  if (!state.enabled || !isLoopback(address) || !url) return null;
  const path = new URL(url, "http://localhost").pathname;
  if (method === "POST" && /^\/api\/v1\/products\/[^/]+\/reviews$/.test(path)) return state.reviewer;
  if (method === "GET" && /^\/api\/v1\/reviews\/[^/]+\/status$/.test(path)) return state.reviewer;
  if (method === "GET" && /^\/api\/v1\/reviews\/[^/]+\/events$/.test(path)) return state.reviewer;
  return state.pm;
}
