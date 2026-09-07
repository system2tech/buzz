import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://localhost",
});
before(() =>
  Object.assign(globalThis, {
    document: dom.window.document,
    window: dom.window,
    HTMLElement: dom.window.HTMLElement,
    IS_REACT_ACT_ENVIRONMENT: true,
  }),
);
after(() => dom.window.close());
const managerId = "11111111-1111-1111-1111-111111111111";
const taskId = "22222222-2222-2222-2222-222222222222";
const author = "a".repeat(64);
const event = (channelId, state = "sleeping", createdAt = 100) => ({
  id: createdAt.toString(16).padStart(64, "0"),
  pubkey: author,
  kind: 30180,
  created_at: createdAt,
  tags: [
    ["h", channelId],
    ["d", channelId],
  ],
  sig: "",
  content: JSON.stringify({
    version: 1,
    managerChannelId: managerId,
    agentPubkey: channelId === managerId ? author : "b".repeat(64),
    location: "local",
    state,
    validUntil: 500,
  }),
});
const channels = [managerId, taskId].map((id) => ({
  id,
  name: id,
  channelType: "stream",
  isMember: true,
}));

test("hook catches initial/live races, repairs missed status and resets tenant/membership state", async () => {
  const { act, renderHook } = await import("@testing-library/react");
  const { relayClient } = await import("@/shared/api/relayClient");
  const { useAgentWorkspaces } = await import("./useAgentWorkspaces.ts");
  const originals = Object.fromEntries(
    [
      "fetchEvents",
      "subscribeLive",
      "subscribeToReconnects",
      "subscribeToConnectionState",
      "getConnectionState",
    ].map((key) => [key, relayClient[key]]),
  );
  let onReconnect;
  let onLive;
  let deliverSnapshot;
  let snapshot = [event(managerId), event(taskId)];
  let disposals = 0;
  const calls = [];
  relayClient.getConnectionState = () => "connected";
  relayClient.subscribeToConnectionState = (listener) => {
    listener("connected");
    return () => {};
  };
  relayClient.subscribeToReconnects = (listener) => {
    onReconnect = listener;
    return () => {};
  };
  relayClient.subscribeLive = async (filter, listener) => {
    calls.push(["live", filter]);
    onLive = listener;
    return async () => {
      disposals++;
    };
  };
  relayClient.fetchEvents = async (filter) => {
    calls.push(["history", filter]);
    return new Promise((resolve) => {
      deliverSnapshot = () => resolve(snapshot);
    });
  };
  let hook;
  try {
    hook = renderHook(
      (props) => useAgentWorkspaces(props.channels, props.pubkey, props.relay),
      {
        initialProps: {
          channels,
          pubkey: "viewer",
          relay: "wss://one.example",
        },
      },
    );
    await act(async () => {});
    assert.equal(calls[0][0], "live");
    for (const [, filter] of calls) {
      assert.deepEqual(filter["#h"], [managerId, taskId]);
      assert.deepEqual(filter.kinds, [30180]);
    }
    await act(async () => {
      onLive(event(taskId, "awake", 101));
      deliverSnapshot();
    });
    assert.equal(hook.result.current.groups[0].tasks[0].record.state, "awake");
    snapshot = [event(managerId), event(taskId, "failed", 102)];
    await act(async () => {
      onReconnect();
    });
    await act(async () => {
      deliverSnapshot();
    });
    assert.equal(hook.result.current.groups[0].tasks[0].record.state, "failed");
    const knownGroup = hook.result.current.groups[0];
    await act(async () => {
      hook.rerender({
        channels: [
          ...channels,
          { ...channels[1], id: "33333333-3333-3333-3333-333333333333" },
        ],
        pubkey: "viewer",
        relay: "wss://one.example",
      });
    });
    assert.equal(hook.result.current.groups[0].record, knownGroup.record);
    assert.equal(
      hook.result.current.groups[0].tasks[0].record,
      knownGroup.tasks[0].record,
    );
    const staleListener = onLive;
    await act(async () => {
      hook.rerender({
        channels: [channels[1]],
        pubkey: "viewer",
        relay: "wss://one.example",
      });
    });
    assert.equal(hook.result.current.groups.length, 0);
    assert.ok(disposals >= 1);
    await act(async () => {
      staleListener(event(managerId, "awake", 110));
    });
    assert.equal(hook.result.current.groups.length, 0);
    await act(async () => {
      hook.rerender({
        channels,
        pubkey: "other-viewer",
        relay: "wss://two.example",
      });
    });
    assert.equal(hook.result.current.groups.length, 0);
    hook.unmount();
    await act(async () => {
      deliverSnapshot();
    });
  } finally {
    hook?.unmount();
    Object.assign(relayClient, originals);
  }
});

test("sync batches only allowed channel IDs and disposes subscriptions resolved after teardown", async () => {
  const { relayClient } = await import("@/shared/api/relayClient");
  const { startAgentWorkspaceSync } = await import("./agentWorkspaceSync.ts");
  const originalLive = relayClient.subscribeLive;
  const originalReconnect = relayClient.subscribeToReconnects;
  const originalFetch = relayClient.fetchEvents;
  const filters = [];
  const pending = [];
  const received = [];
  let disposed = 0;
  relayClient.subscribeToReconnects = () => () => {};
  relayClient.fetchEvents = async () => [];
  relayClient.subscribeLive = (filter, listener) => {
    filters.push(filter);
    listener(event(managerId));
    return new Promise((resolve) =>
      pending.push(() =>
        resolve(async () => {
          disposed++;
        }),
      ),
    );
  };
  try {
    const ids = Array.from(
      { length: 201 },
      (_, i) =>
        `${String(i + 10).padStart(8, "0")}-0000-0000-0000-000000000000`,
    );
    const stop = startAgentWorkspaceSync(ids, (records) =>
      received.push(...records),
    );
    assert.deepEqual(
      filters.map((filter) => filter["#h"].length),
      [100, 100, 1],
    );
    assert.equal(received.length, 0);
    stop();
    for (const resolve of pending) resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(disposed, 3);
  } finally {
    relayClient.subscribeLive = originalLive;
    relayClient.subscribeToReconnects = originalReconnect;
    relayClient.fetchEvents = originalFetch;
  }
});

test("cold channel loading cannot erase cached relationships before membership resolves", async () => {
  const { act, renderHook } = await import("@testing-library/react");
  const { relayClient } = await import("@/shared/api/relayClient");
  const { useAgentWorkspaces } = await import("./useAgentWorkspaces.ts");
  const { parseAgentWorkspaceEvent } = await import("./agentWorkspaces.ts");
  const { readAgentWorkspaceCache, writeAgentWorkspaceCache } = await import(
    "./agentWorkspaceCache.ts"
  );
  const methods = [
    "fetchEvents",
    "subscribeLive",
    "subscribeToReconnects",
    "subscribeToConnectionState",
    "getConnectionState",
  ];
  const original = Object.fromEntries(
    methods.map((name) => [name, relayClient[name]]),
  );
  const scope = "wss://cold.example:viewer";
  writeAgentWorkspaceCache(
    scope,
    new Map(
      [managerId, taskId].map((id) => [
        id,
        parseAgentWorkspaceEvent(event(id)),
      ]),
    ),
  );
  let subscriptions = 0;
  relayClient.getConnectionState = () => "disconnected";
  relayClient.subscribeToConnectionState = (listener) => {
    listener("disconnected");
    return () => {};
  };
  relayClient.subscribeToReconnects = () => () => {};
  relayClient.fetchEvents = async () => {
    throw new Error("offline");
  };
  relayClient.subscribeLive = async () => {
    subscriptions++;
    throw new Error("offline");
  };
  let hook;
  try {
    hook = renderHook(
      ({ visible, ready }) =>
        useAgentWorkspaces(visible, "viewer", "wss://cold.example", ready),
      { initialProps: { visible: [], ready: false } },
    );
    await act(async () => {});
    assert.equal(hook.result.current.groups.length, 0);
    assert.equal(readAgentWorkspaceCache(scope).size, 2);
    assert.equal(subscriptions, 0);
    await act(async () => hook.rerender({ visible: channels, ready: true }));
    assert.equal(hook.result.current.groups[0].tasks[0].channel.id, taskId);
    assert.equal(hook.result.current.groups[0].tasks[0].record.restored, true);
    assert.equal(readAgentWorkspaceCache(scope).size, 2);
  } finally {
    hook?.unmount();
    Object.assign(relayClient, original);
  }
});
