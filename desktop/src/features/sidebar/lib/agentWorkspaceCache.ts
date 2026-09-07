import {
  parseAgentWorkspaceEvent,
  type AgentWorkspaceRecord,
} from "./agentWorkspaces";
import { KIND_AGENT_WORKSPACE } from "@/shared/constants/kinds";

const keyFor = (scope: string) =>
  `buzz.agent-workspaces.relationships:${encodeURIComponent(scope)}`;

/** Restore only relationships; every persisted status starts as unconfirmed. */
export function readAgentWorkspaceCache(
  scope: string,
): ReadonlyMap<string, AgentWorkspaceRecord> {
  const records = new Map<string, AgentWorkspaceRecord>();
  if (!scope) return records;
  try {
    const cached: unknown = JSON.parse(
      window.localStorage.getItem(keyFor(scope)) ?? "[]",
    );
    if (!Array.isArray(cached)) return records;
    for (const value of cached.slice(0, 10_000)) {
      if (!value || typeof value !== "object") continue;
      const record = parseAgentWorkspaceEvent({
        kind: KIND_AGENT_WORKSPACE,
        pubkey: value.managerPubkey,
        id: value.eventId,
        created_at: value.createdAt,
        sig: "",
        tags: [
          ["d", value.channelId],
          ["h", value.channelId],
        ],
        content: JSON.stringify({ ...value, version: 1 }),
      });
      if (record) records.set(record.channelId, { ...record, restored: true });
    }
  } catch {
    /* A corrupt/unavailable cache falls back to the ordinary channel list. */
  }
  return records;
}

/** Cache validated channel relationships, scoped to the exact relay and viewer. */
export function writeAgentWorkspaceCache(
  scope: string,
  records: ReadonlyMap<string, AgentWorkspaceRecord>,
): void {
  if (!scope) return;
  try {
    window.localStorage.setItem(
      keyFor(scope),
      JSON.stringify([...records.values()].slice(0, 10_000)),
    );
  } catch {
    /* Relay data remains authoritative when local persistence is unavailable. */
  }
}
