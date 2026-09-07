import type { RelayEvent } from "@/shared/api/types";
import { KIND_AGENT_WORKSPACE } from "@/shared/constants/kinds";

export type MockFilter = {
  "#a"?: string[];
  "#buzz-channel"?: string[];
  "#d"?: string[];
  "#e"?: string[];
  "#h"?: string[];
  "#p"?: string[];
  authors?: string[];
  ids?: string[];
  kinds?: number[];
  limit?: number;
  since?: number;
  until?: number;
};

declare global {
  interface Window {
    /** Relay-authorized lifecycle snapshots seeded before the mock app mounts. */
    __BUZZ_E2E_AGENT_WORKSPACE_EVENTS__?: RelayEvent[];
  }
}

/** Match channel batches faithfully instead of promoting them to global live streams. */
export function mockSubscriptionMatchesChannel(
  subscription: { channelId: string; channelIds?: string[] },
  channelId: string,
): boolean {
  return subscription.channelIds
    ? subscription.channelIds.includes(channelId)
    : subscription.channelId === channelId || subscription.channelId === "*";
}

/** Channel-scoped workspace snapshots, including startup fixtures and live updates. */
export function filterMockAgentWorkspaceEvents(
  filter: MockFilter,
  loadMessages: (channelId: string) => RelayEvent[],
): RelayEvent[] | null {
  if (!filter.kinds?.includes(KIND_AGENT_WORKSPACE)) return null;
  const allowed = new Set(filter["#h"] ?? []);
  return [
    ...(window.__BUZZ_E2E_AGENT_WORKSPACE_EVENTS__ ?? []),
    ...[...allowed].flatMap(loadMessages),
  ]
    .filter(
      (event) =>
        event.kind === KIND_AGENT_WORKSPACE &&
        event.tags.some(
          ([name, value]) => name === "h" && allowed.has(value),
        ) &&
        (!filter.authors || filter.authors.includes(event.pubkey)) &&
        (filter.since === undefined || event.created_at >= filter.since) &&
        (filter.until === undefined || event.created_at <= filter.until),
    )
    .sort((a, b) => b.created_at - a.created_at || b.id.localeCompare(a.id))
    .slice(0, filter.limit ?? 10_000);
}
