import type { Channel, RelayEvent } from "@/shared/api/types";
import { KIND_AGENT_WORKSPACE } from "@/shared/constants/kinds";

export type AgentWorkspaceState = "starting" | "awake" | "sleeping" | "failed";
export type AgentWorkspaceStatus = AgentWorkspaceState | "unknown";
export type AgentWorkspaceRecord = {
  channelId: string;
  managerChannelId: string;
  managerPubkey: string;
  agentPubkey: string;
  location: "local" | "remote";
  state: AgentWorkspaceState;
  validUntil: number;
  createdAt: number;
  eventId: string;
  /** Restored relationships remain usable, but their status needs relay confirmation. */
  restored?: boolean;
};
export type AgentWorkspaceGroup = {
  manager: Channel;
  record: AgentWorkspaceRecord;
  tasks: { channel: Channel; record: AgentWorkspaceRecord }[];
};

const HEX_KEY = /^[a-f0-9]{64}$/;
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;

/** Parse relay-authorized state, rejecting ambiguous tags and unknown versions. */
export function parseAgentWorkspaceEvent(
  event: RelayEvent,
): AgentWorkspaceRecord | null {
  if (
    event.kind !== KIND_AGENT_WORKSPACE ||
    !HEX_KEY.test(event.pubkey) ||
    !HEX_KEY.test(event.id) ||
    !Number.isSafeInteger(event.created_at) ||
    event.created_at < 0
  )
    return null;
  const channelTags = event.tags.filter(([name]) => name === "h");
  const addressTags = event.tags.filter(([name]) => name === "d");
  const channelId = channelTags[0]?.[1];
  if (
    channelTags.length !== 1 ||
    addressTags.length !== 1 ||
    !channelId ||
    !UUID.test(channelId) ||
    addressTags[0]?.[1] !== channelId
  )
    return null;
  try {
    const data = JSON.parse(event.content);
    if (
      data?.version !== 1 ||
      typeof data.managerChannelId !== "string" ||
      !UUID.test(data.managerChannelId) ||
      typeof data.agentPubkey !== "string" ||
      !HEX_KEY.test(data.agentPubkey) ||
      !["local", "remote"].includes(data.location) ||
      !["starting", "awake", "sleeping", "failed"].includes(data.state) ||
      !Number.isSafeInteger(data.validUntil) ||
      data.validUntil < event.created_at ||
      data.validUntil > event.created_at + 600 ||
      (channelId === data.managerChannelId && data.agentPubkey !== event.pubkey)
    )
      return null;
    return {
      channelId,
      managerChannelId: data.managerChannelId,
      managerPubkey: event.pubkey,
      agentPubkey: data.agentPubkey,
      location: data.location,
      state: data.state,
      validUntil: data.validUntil,
      createdAt: event.created_at,
      eventId: event.id,
    };
  } catch {
    return null;
  }
}

/** Match the relay's deterministic addressable-event head ordering. */
export function mergeAgentWorkspaceRecords(
  current: ReadonlyMap<string, AgentWorkspaceRecord>,
  incoming: Iterable<AgentWorkspaceRecord>,
): ReadonlyMap<string, AgentWorkspaceRecord> {
  let result: Map<string, AgentWorkspaceRecord> | undefined;
  for (const record of incoming) {
    const previous = (result ?? current).get(record.channelId);
    if (
      previous &&
      (record.createdAt < previous.createdAt ||
        (record.createdAt === previous.createdAt &&
          (record.eventId < previous.eventId ||
            (record.eventId === previous.eventId && !previous.restored))))
    )
      continue;
    result ??= new Map(current);
    result.set(record.channelId, record);
  }
  return result ?? current;
}

/** Expiry changes status, never the durable manager/task relationship. */
export function agentWorkspaceStatus(
  record: AgentWorkspaceRecord,
  nowSeconds: number,
  connected = true,
): AgentWorkspaceStatus {
  return connected && !record.restored && nowSeconds < record.validUntil
    ? record.state
    : "unknown";
}

/** Build only one level, from joined channels and a matching manager root. */
export function buildAgentWorkspaceGroups(
  channels: readonly Channel[],
  records: ReadonlyMap<string, AgentWorkspaceRecord>,
): { groups: AgentWorkspaceGroup[]; groupedChannelIds: ReadonlySet<string> } {
  const visible = new Map(
    channels
      .filter(
        (channel) =>
          channel.isMember &&
          channel.channelType === "stream" &&
          !channel.archivedAt,
      )
      .map((channel) => [channel.id, channel]),
  );
  const byManager = new Map<string, AgentWorkspaceGroup>();
  for (const record of records.values()) {
    const manager = visible.get(record.channelId);
    if (
      manager &&
      record.channelId === record.managerChannelId &&
      record.agentPubkey === record.managerPubkey
    ) {
      byManager.set(manager.id, { manager, record, tasks: [] });
    }
  }
  for (const record of records.values()) {
    const channel = visible.get(record.channelId);
    const group = byManager.get(record.managerChannelId);
    if (
      channel &&
      group &&
      channel.id !== group.manager.id &&
      record.managerPubkey === group.record.managerPubkey &&
      record.location === group.record.location
    ) {
      group.tasks.push({ channel, record });
    }
  }
  // Names and IDs determine order; changing lifecycle state never moves a row.
  const compare = (a: Channel, b: Channel) =>
    a.name.localeCompare(b.name) || a.id.localeCompare(b.id);
  const groups = [...byManager.values()].sort((a, b) =>
    compare(a.manager, b.manager),
  );
  const groupedChannelIds = new Set<string>();
  for (const group of groups) {
    group.tasks.sort((a, b) => compare(a.channel, b.channel));
    groupedChannelIds.add(group.manager.id);
    for (const task of group.tasks) groupedChannelIds.add(task.channel.id);
  }
  return { groups, groupedChannelIds };
}
