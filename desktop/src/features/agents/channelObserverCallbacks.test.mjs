import assert from "node:assert/strict";
import { test } from "node:test";
import {
  _testProcessLiveObserverEvents,
  resetAgentObserverStore,
  subscribeAgentManagementRequests,
  getAgentObserverSnapshot,
} from "./observerRelayStore.ts";

test("read-only live dispatch displays activity without executing management requests", () => {
  resetAgentObserverStore();
  const agent = "a".repeat(64);
  const event = {
    seq: 1,
    timestamp: "2026-01-01T00:00:01Z",
    kind: "acp_read",
    channelId: "11111111-1111-1111-1111-111111111111",
    sessionId: "session",
    turnId: "turn",
    agentIndex: 0,
    payload: {
      type: "agent_management_request",
      action: "create",
      requestId: "request-1",
      request: {
        channelId: "11111111-1111-1111-1111-111111111111",
        displayName: "worker",
        systemPrompt: "work",
      },
    },
  };
  let calls = 0;
  const unsubscribe = subscribeAgentManagementRequests(() => calls++);
  _testProcessLiveObserverEvents(agent, [event], true);
  assert.equal(calls, 0);
  assert.equal(getAgentObserverSnapshot(agent).events.length, 1);
  _testProcessLiveObserverEvents(agent, [
    { ...event, seq: 2, timestamp: "2026-01-01T00:00:02Z" },
  ]);
  assert.equal(
    calls,
    1,
    "owner dispatch proves the payload is an actionable management request",
  );
  unsubscribe();
  resetAgentObserverStore();
});
