import assert from "node:assert/strict";
import test from "node:test";

import {
  TRANSCRIPT_TURN_WINDOW,
  limitToRecentTurns,
} from "./recentTurnWindow.ts";

const ev = (seq, turnId, sessionId = "sess-1") => ({ seq, turnId, sessionId });

test("returns the same array when nothing needs dropping", () => {
  const events = [ev(1, "t1"), ev(2, "t1"), ev(3, "t2")];
  // Reference equality matters: React's useMemo consumers short-circuit on it,
  // so the common path must not allocate.
  assert.equal(limitToRecentTurns(events, 5), events);
});

test("keeps only the last N turns", () => {
  const events = [
    ev(1, "old"),
    ev(2, "old"),
    ev(3, "mid"),
    ev(4, "new"),
    ev(5, "new"),
  ];
  const kept = limitToRecentTurns(events, 2);
  assert.deepEqual(
    kept.map((e) => e.seq),
    [3, 4, 5],
    "the two most recent turns survive, whole",
  );
});

test("session-scoped events survive the window whatever their age", () => {
  // The system-prompt card is emitted on session/new with no turn id. Losing it
  // to a recency window would be a worse bug than the flicker this cap fixes.
  const events = [
    { seq: 1, turnId: null, sessionId: null },
    ev(2, "old"),
    ev(3, "new"),
  ];
  const kept = limitToRecentTurns(events, 1);
  assert.deepEqual(
    kept.map((e) => e.seq),
    [1, 3],
    "the session-scoped row is kept, the stale turn is not",
  );
});

test("recency is decided by where a turn LAST appears", () => {
  // Interleaved turns: `a` started first but is still active, `b` finished. A cap
  // that ranked by first appearance would drop the turn still producing output.
  const events = [ev(1, "a"), ev(2, "b"), ev(3, "b"), ev(4, "a")];
  const kept = limitToRecentTurns(events, 1);
  assert.deepEqual(
    kept.map((e) => e.seq),
    [1, 4],
    "turn `a` is the recent one because it appears last",
  );
});

test("falls back to sessionId when an agent sets no turnId", () => {
  // Without the fallback every event would be its own turn, making a 12-turn
  // window a 12-EVENT window and hiding almost everything.
  const events = [
    { seq: 1, turnId: undefined, sessionId: "s1" },
    { seq: 2, turnId: undefined, sessionId: "s1" },
    { seq: 3, turnId: undefined, sessionId: "s2" },
  ];
  const kept = limitToRecentTurns(events, 1);
  assert.deepEqual(
    kept.map((e) => e.seq),
    [3],
    "one bucket per session, not per event",
  );
});

test("degenerate inputs are passed through untouched", () => {
  const events = [ev(1, "t1")];
  assert.equal(limitToRecentTurns(events, 0), events);
  assert.equal(limitToRecentTurns([], 5).length, 0);
});

test("the default window is a usable size", () => {
  assert.ok(
    TRANSCRIPT_TURN_WINDOW >= 5,
    "too small a window would hide ordinary back-and-forth",
  );
});
