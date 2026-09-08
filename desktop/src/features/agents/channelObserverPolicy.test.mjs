import assert from "node:assert/strict";
import { test } from "node:test";
import {
  acceptsChannelObserverPayload,
  acceptsObserverViewerPayload,
  setObserverViewerAccess,
  resetObserverViewerAccess,
} from "./channelObserverPolicy.ts";
const frame = {
  tags: [["observer_channel", "11111111-1111-1111-1111-111111111111"]],
};
const channels = new Set(["11111111-1111-1111-1111-111111111111"]);
const activity = {
  kind: "turn_started",
  channelId: "11111111-1111-1111-1111-111111111111",
};
test("channel activity requires current membership and exact scope", () => {
  assert.equal(
    acceptsChannelObserverPayload(frame, [activity], channels),
    true,
  );
  assert.equal(
    acceptsChannelObserverPayload(frame, [activity], new Set()),
    false,
  );
  assert.equal(
    acceptsChannelObserverPayload(
      frame,
      [{ ...activity, channelId: "private" }],
      channels,
    ),
    false,
  );
  assert.equal(
    acceptsChannelObserverPayload(
      frame,
      [activity, { ...activity, channelId: null }],
      channels,
    ),
    false,
  );
  assert.equal(
    acceptsChannelObserverPayload(
      { tags: [...frame.tags, ...frame.tags] },
      [activity],
      channels,
    ),
    false,
  );
});
test("channel stream rejects management, config, nested batches and raw ACP commands", () => {
  for (const kind of [
    "control_result",
    "session_config_captured",
    "managed_agent_runtime_lifecycle",
    "batch",
    "acp_write",
  ]) {
    assert.equal(
      acceptsChannelObserverPayload(frame, [{ ...activity, kind }], channels),
      false,
    );
  }
  assert.equal(
    acceptsChannelObserverPayload(
      frame,
      [{ ...activity, kind: "acp_read", payload: { method: "agent/create" } }],
      channels,
    ),
    false,
  );
  assert.equal(
    acceptsChannelObserverPayload(
      frame,
      [
        {
          ...activity,
          kind: "acp_read",
          payload: {
            method: "session/update",
            params: { update: { sessionUpdate: "tool_call" } },
          },
        },
      ],
      channels,
    ),
    true,
  );
});

test("observing does not grant owner authority and membership removal blocks future frames", () => {
  setObserverViewerAccess(channels, new Set(["owned"]));
  assert.equal(
    acceptsObserverViewerPayload({ tags: [] }, [activity], "foreign"),
    false,
  );
  assert.equal(
    acceptsObserverViewerPayload({ tags: [] }, [activity], "owned"),
    true,
  );
  assert.equal(
    acceptsObserverViewerPayload(frame, [activity], "foreign"),
    true,
  );
  setObserverViewerAccess(new Set(), new Set(["owned"]));
  assert.equal(
    acceptsObserverViewerPayload(frame, [activity], "foreign"),
    false,
  );
  resetObserverViewerAccess();
});

test("startup/reset rejects unscoped frames until ownership resolves", () => {
  resetObserverViewerAccess();
  assert.equal(
    acceptsObserverViewerPayload({ tags: [] }, [activity], "owned"),
    false,
  );
});
