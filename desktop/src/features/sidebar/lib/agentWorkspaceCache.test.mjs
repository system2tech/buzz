import assert from "node:assert/strict";
import { test } from "node:test";
import {
  readAgentWorkspaceCache,
  writeAgentWorkspaceCache,
} from "./agentWorkspaceCache.ts";
import {
  agentWorkspaceStatus,
  mergeAgentWorkspaceRecords,
} from "./agentWorkspaces.ts";

test("offline restart restores relationships as Unknown, scoped to relay and viewer", () => {
  const original = globalThis.window;
  const values = new Map();
  globalThis.window = {
    localStorage: {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, value),
    },
  };
  try {
    const record = {
      channelId: "11111111-1111-1111-1111-111111111111",
      managerChannelId: "11111111-1111-1111-1111-111111111111",
      managerPubkey: "a".repeat(64),
      agentPubkey: "a".repeat(64),
      location: "local",
      state: "awake",
      createdAt: 100,
      validUntil: 300,
      eventId: "1".repeat(64),
    };
    writeAgentWorkspaceCache(
      "relay-one:viewer",
      new Map([[record.channelId, record]]),
    );
    const restored = readAgentWorkspaceCache("relay-one:viewer");
    assert.equal(restored.size, 1);
    assert.equal(
      agentWorkspaceStatus(restored.get(record.channelId), 150, true),
      "unknown",
    );
    assert.equal(readAgentWorkspaceCache("relay-two:viewer").size, 0);
    assert.equal(readAgentWorkspaceCache("relay-one:other-viewer").size, 0);
    const confirmed = mergeAgentWorkspaceRecords(restored, [record]);
    assert.equal(
      agentWorkspaceStatus(confirmed.get(record.channelId), 150, true),
      "awake",
    );
    values.set([...values.keys()][0], "invalid JSON");
    assert.equal(readAgentWorkspaceCache("relay-one:viewer").size, 0);
  } finally {
    globalThis.window = original;
  }
});
