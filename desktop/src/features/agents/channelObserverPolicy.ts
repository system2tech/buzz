import type { RelayEvent } from "@/shared/api/types";
import type { ObserverEvent } from "./ui/agentSessionTypes";

const ACTIVITY_KINDS = new Set([
  "turn_started",
  "turn_completed",
  "turn_liveness",
  "session_resolved",
]);
const UPDATE_KINDS = new Set([
  "agent_message_chunk",
  "agent_thought_chunk",
  "tool_call",
  "tool_call_update",
  "plan",
]);

function isActivity(inner: ObserverEvent): boolean {
  if (ACTIVITY_KINDS.has(inner.kind)) return true;
  if (
    inner.kind !== "acp_read" ||
    !inner.payload ||
    typeof inner.payload !== "object"
  )
    return false;
  const payload = inner.payload as {
    id?: unknown;
    method?: string;
    params?: { update?: { sessionUpdate?: string } };
  };
  return (
    payload.id === undefined &&
    payload.method === "session/update" &&
    UPDATE_KINDS.has(payload.params?.update?.sessionUpdate ?? "")
  );
}

/** Channel viewers receive only their channel's activity, never owner controls. */
export function acceptsChannelObserverPayload(
  event: RelayEvent,
  events: readonly ObserverEvent[],
  joinedChannels: ReadonlySet<string>,
): boolean {
  const scopes = event.tags.filter((tag) => tag[0] === "observer_channel");
  if (
    scopes.length !== 1 ||
    scopes[0].length !== 2 ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      scopes[0][1],
    )
  )
    return false;
  const channel = scopes[0][1];
  return (
    joinedChannels.has(channel) &&
    events.length > 0 &&
    events.every((inner) => inner.channelId === channel && isActivity(inner))
  );
}

let viewerJoinedChannels: ReadonlySet<string> = new Set();
let viewerOwnedAgents: ReadonlySet<string> = new Set();

/** Update the active identity's current channel membership and ownership. */
export function setObserverViewerAccess(
  channels: ReadonlySet<string>,
  ownedAgents: ReadonlySet<string> = new Set(),
) {
  viewerJoinedChannels = channels;
  viewerOwnedAgents = ownedAgents;
}

/** Reset community-scoped observer authorization with the observer store. */
export function resetObserverViewerAccess() {
  viewerJoinedChannels = new Set();
  viewerOwnedAgents = new Set();
}

/** Validate the scope before either live or archived frames enter the stores. */
export function acceptsObserverViewerPayload(
  event: RelayEvent,
  events: readonly ObserverEvent[],
  agent: string,
): boolean {
  if (event.tags.some((tag) => tag[0] === "observer_channel")) {
    return acceptsChannelObserverPayload(event, events, viewerJoinedChannels);
  }
  return viewerOwnedAgents.has(agent);
}

// Observer event kind for a batch envelope wrapping multiple events. The ACP
// harness publishes one frame per second; everything that accumulated between
// ticks arrives as `{ kind: "batch", payload: { events: [...] } }` with every
// inner event carrying its own seq/timestamp. Inner events are processed
// exactly as unbatched ones; the envelope itself is never stored.
const OBSERVER_BATCH_KIND = "batch";

// Expand a decrypted observer event into its inner events when it is a batch
// envelope; a non-batch event passes through as a single-element array. A
// malformed envelope (no events array) degrades to the envelope itself so a
// harness bug cannot silently blank the session viewer.
export function unwrapObserverBatch(parsed: ObserverEvent): ObserverEvent[] {
  if (parsed.kind !== OBSERVER_BATCH_KIND) {
    return [parsed];
  }
  const payload = parsed.payload as { events?: unknown } | null;
  const events = Array.isArray(payload?.events)
    ? (payload.events as ObserverEvent[])
    : null;
  return events && events.length > 0 ? events : [parsed];
}
