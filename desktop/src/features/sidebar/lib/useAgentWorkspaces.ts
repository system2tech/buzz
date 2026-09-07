import * as React from "react";
import type { Channel } from "@/shared/api/types";
import { useRelayConnection } from "@/shared/api/useRelayConnection";
import { normalizeRelayUrl } from "@/shared/lib/normalizeRelayUrl";
import {
  buildAgentWorkspaceGroups,
  mergeAgentWorkspaceRecords,
  type AgentWorkspaceRecord,
} from "./agentWorkspaces";
import { startAgentWorkspaceSync } from "./agentWorkspaceSync";
import {
  readAgentWorkspaceCache,
  writeAgentWorkspaceCache,
} from "./agentWorkspaceCache";

const EMPTY_RECORDS: ReadonlyMap<string, AgentWorkspaceRecord> = new Map();

/** Scope lifecycle data to the active identity, relay and joined channel set. */
export function useAgentWorkspaces(
  channels: readonly Channel[],
  pubkey: string | undefined,
  relayUrl: string | undefined,
  membershipReady = true,
) {
  const channelKey = JSON.stringify(
    channels
      .filter(
        (channel) =>
          channel.isMember &&
          channel.channelType === "stream" &&
          !channel.archivedAt,
      )
      .map(({ id }) => id)
      .sort(),
  );
  const scope =
    pubkey && relayUrl ? `${normalizeRelayUrl(relayUrl)}:${pubkey}` : "";
  const [snapshot, setSnapshot] = React.useState(() => ({
    scope,
    records: readAgentWorkspaceCache(scope),
  }));
  const connection = useRelayConnection({ degradedAfterMs: 0 });

  React.useEffect(() => {
    const allowed = new Set<string>(JSON.parse(channelKey));
    setSnapshot((previous) => {
      const existing =
        previous.scope === scope
          ? previous.records
          : readAgentWorkspaceCache(scope);
      if (!membershipReady)
        return previous.scope === scope
          ? previous
          : { scope, records: existing };
      const records = new Map([...existing].filter(([id]) => allowed.has(id)));
      return previous.scope === scope && records.size === previous.records.size
        ? previous
        : { scope, records };
    });
    if (!scope || !membershipReady) return;
    return startAgentWorkspaceSync(JSON.parse(channelKey), (incoming) => {
      setSnapshot((previous) => {
        if (previous.scope !== scope) return previous;
        const records = mergeAgentWorkspaceRecords(previous.records, incoming);
        return records === previous.records ? previous : { scope, records };
      });
    });
  }, [scope, channelKey, membershipReady]);

  React.useEffect(() => {
    if (membershipReady && snapshot.scope === scope)
      writeAgentWorkspaceCache(scope, snapshot.records);
  }, [snapshot, scope, membershipReady]);

  const records = snapshot.scope === scope ? snapshot.records : EMPTY_RECORDS;
  const grouped = React.useMemo(
    () => buildAgentWorkspaceGroups(channels, records),
    [channels, records],
  );
  return { ...grouped, connected: connection === "connected" };
}
