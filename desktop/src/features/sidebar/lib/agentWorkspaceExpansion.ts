import { normalizeRelayUrl } from "@/shared/lib/normalizeRelayUrl";

/** Persist collapse preferences independently for every relay and viewer. */
export function agentWorkspaceExpansionKey(
  relayUrl: string,
  pubkey: string,
): string {
  return `buzz.agent-workspaces.collapsed:${encodeURIComponent(normalizeRelayUrl(relayUrl))}:${pubkey}`;
}

export function readAgentWorkspaceExpansion(
  key: string,
): Record<string, boolean> {
  try {
    const data: unknown = JSON.parse(window.localStorage.getItem(key) ?? "{}");
    if (!data || typeof data !== "object" || Array.isArray(data)) return {};
    return Object.fromEntries(
      Object.entries(data).filter(([, value]) => typeof value === "boolean"),
    );
  } catch {
    return {};
  }
}

export function writeAgentWorkspaceExpansion(
  key: string,
  state: Record<string, boolean>,
): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(state));
  } catch {
    /* The in-memory disclosure remains usable if storage is unavailable. */
  }
}
