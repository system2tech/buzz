import { relayClient } from "@/shared/api/relayClient";
import type { RelayEvent } from "@/shared/api/types";
import { KIND_AGENT_WORKSPACE } from "@/shared/constants/kinds";
import {
  parseAgentWorkspaceEvent,
  type AgentWorkspaceRecord,
} from "./agentWorkspaces";

export const AGENT_WORKSPACE_BATCH_SIZE = 100;

/** Subscribe before snapshotting to close the load/live race; repair on reconnect. */
export function startAgentWorkspaceSync(
  channelIds: readonly string[],
  onRecords: (records: AgentWorkspaceRecord[]) => void,
): () => void {
  let cancelled = false;
  const allowed = new Set(channelIds);
  const batches = Array.from(
    { length: Math.ceil(channelIds.length / AGENT_WORKSPACE_BATCH_SIZE) },
    (_, index) =>
      channelIds.slice(
        index * AGENT_WORKSPACE_BATCH_SIZE,
        (index + 1) * AGENT_WORKSPACE_BATCH_SIZE,
      ),
  ).map((ids) => ({
    ids,
    dispose: null as null | (() => Promise<void>),
    pending: false,
  }));
  const accept = (events: RelayEvent[]) => {
    if (cancelled) return;
    const records = events.flatMap((event) => {
      const record = parseAgentWorkspaceEvent(event);
      return record && allowed.has(record.channelId) ? [record] : [];
    });
    if (records.length) onRecords(records);
  };
  const refresh = async (batch: (typeof batches)[number]) => {
    if (cancelled || batch.pending) return;
    batch.pending = true;
    try {
      const filter = { kinds: [KIND_AGENT_WORKSPACE], "#h": [...batch.ids] };
      if (!batch.dispose) {
        const dispose = await relayClient.subscribeLive(
          { ...filter, limit: 0 },
          (event) => accept([event]),
        );
        if (cancelled) {
          await dispose();
          return;
        }
        batch.dispose = dispose;
      }
      const events = await relayClient.fetchEvents({
        ...filter,
        limit: 10_000,
      });
      accept(events);
    } catch {
      // Existing relationships survive transient failures; lease expiry makes
      // the status Unknown. The next reconnect/retry repairs missed changes.
    } finally {
      batch.pending = false;
    }
  };
  const refreshAll = () => {
    for (const batch of batches) void refresh(batch);
  };
  const unsubscribeReconnects = relayClient.subscribeToReconnects(refreshAll);
  const retryTimer = window.setInterval(refreshAll, 60_000);
  refreshAll();
  return () => {
    cancelled = true;
    window.clearInterval(retryTimer);
    unsubscribeReconnects();
    for (const batch of batches) void batch.dispose?.().catch(() => {});
  };
}
