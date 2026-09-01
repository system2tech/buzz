/**
 * S2 — bound the transcript to recent turns.
 *
 * The observer store keeps up to `MAX_OBSERVER_EVENTS` (3000) events per agent
 * and **rebuilds the whole transcript from the retained window on every append**
 * (`observerRelayStore.ts`, `appendAgentEvents`). That is fine for a short
 * session and quadratic-feeling on a long one: a busy agent whose window is full
 * re-processes thousands of events per frame, and because the rebuild produces a
 * fresh array the list remounts each time. Reported 2026-09-01 as recent messages
 * "flashing and flickering, then disappearing" on a session long enough to fill
 * the window.
 *
 * Capping the events fed to the transcript builder fixes both halves: the rebuild
 * is bounded, so it stops being expensive, and the list stops thrashing.
 *
 * **Session-scoped events are always kept**, whatever their age. The
 * system-prompt card is emitted on `session/new` with no turn id, and it is the
 * one row people look for when they want to know what an agent was told — losing
 * it to a recency window would be a worse bug than the one being fixed.
 *
 * This caps the TRANSCRIPT only. The raw JSON-RPC rail still shows the full
 * retained window, which is where you go when you need the untruncated stream.
 */

/** How many recent turns the transcript renders. */
export const TRANSCRIPT_TURN_WINDOW = 12;

type TurnScoped = {
  turnId?: string | null;
  sessionId?: string | null;
};

/**
 * The turn an event belongs to, or `null` for session-scoped events.
 *
 * Falls back to `sessionId` so that an agent which never sets `turnId` gets one
 * bucket per session rather than every event counting as its own turn — which
 * would make the window a 12-EVENT window and hide almost everything.
 */
function turnKeyOf(event: TurnScoped): string | null {
  return event.turnId ?? event.sessionId ?? null;
}

/**
 * Keep every session-scoped event plus everything from the last `maxTurns`
 * turns, in the original order.
 *
 * Returns the input array itself when nothing needs dropping, so React's
 * reference equality still short-circuits re-renders on the common path.
 */
export function limitToRecentTurns<T extends TurnScoped>(
  events: readonly T[],
  maxTurns: number = TRANSCRIPT_TURN_WINDOW,
): readonly T[] {
  if (maxTurns <= 0 || events.length === 0) return events;

  // Walk backwards so "recent" is decided by where a turn LAST appears: an
  // interleaved stream (two channels' turns overlapping) should keep the turns
  // that are still active, not the ones that merely started late.
  const keep = new Set<string>();
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const key = turnKeyOf(events[i]);
    if (key === null) continue;
    if (!keep.has(key)) {
      if (keep.size >= maxTurns) break;
      keep.add(key);
    }
  }

  const kept = events.filter((event) => {
    const key = turnKeyOf(event);
    return key === null || keep.has(key);
  });
  return kept.length === events.length ? events : kept;
}
