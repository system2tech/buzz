import assert from "node:assert/strict";
import { test } from "node:test";
import {
  agentWorkspaceStatus,
  buildAgentWorkspaceGroups,
  mergeAgentWorkspaceRecords,
  parseAgentWorkspaceEvent,
} from "./agentWorkspaces.ts";
import { sidebarChannelBuckets } from "./sidebarChannelBuckets.ts";

const managerId = "11111111-1111-1111-1111-111111111111";
const taskId = "22222222-2222-2222-2222-222222222222";
const otherId = "33333333-3333-3333-3333-333333333333";
const author = "a".repeat(64);
const worker = "b".repeat(64);
const channel = (id, name = id, extra = {}) => ({
  id,
  name,
  channelType: "stream",
  isMember: true,
  ...extra,
});
const event = (id = taskId, extra = {}, data = {}) => ({
  id: "1".repeat(64),
  pubkey: author,
  kind: 30180,
  created_at: 100,
  tags: [
    ["h", id],
    ["d", id],
  ],
  sig: "",
  content: JSON.stringify({
    version: 1,
    managerChannelId: managerId,
    agentPubkey: id === managerId ? author : worker,
    location: "local",
    state: "sleeping",
    validUntil: 200,
    ...data,
  }),
  ...extra,
});

test("workspace parser rejects malformed authority, ambiguous tags and future schemas", () => {
  assert.equal(parseAgentWorkspaceEvent(event()).channelId, taskId);
  for (const invalid of [
    event(taskId, { kind: 30078 }),
    event(taskId, { pubkey: "invalid" }),
    event(taskId, {
      tags: [
        ["h", taskId],
        ["d", otherId],
      ],
    }),
    event(taskId, {
      tags: [
        ["h", taskId],
        ["h", otherId],
        ["d", taskId],
      ],
    }),
    event(taskId, { content: "null" }),
    event(taskId, {}, { version: 2 }),
    event(taskId, {}, { state: "offline" }),
    event(taskId, {}, { validUntil: 99 }),
    event(taskId, {}, { validUntil: 701 }),
    event(managerId, {}, { agentPubkey: worker }),
  ])
    assert.equal(parseAgentWorkspaceEvent(invalid), null);
});

test("lease expiry and disconnection preserve grouping but never imply sleep", () => {
  const record = parseAgentWorkspaceEvent(event());
  assert.equal(agentWorkspaceStatus(record, 199), "sleeping");
  assert.equal(agentWorkspaceStatus(record, 200), "unknown");
  assert.equal(agentWorkspaceStatus(record, 100, false), "unknown");
  const noLease = parseAgentWorkspaceEvent(
    event(taskId, {}, { validUntil: 100 }),
  );
  assert.equal(agentWorkspaceStatus(noLease, 100), "unknown");
});

test("snapshot/live ordering is deterministic and unchanged events keep references", () => {
  const early = parseAgentWorkspaceEvent(event());
  const later = parseAgentWorkspaceEvent(
    event(taskId, { created_at: 101, id: "2".repeat(64) }),
  );
  const tieWinner = { ...later, eventId: "3".repeat(64), state: "awake" };
  const current = mergeAgentWorkspaceRecords(new Map(), [later]);
  assert.equal(mergeAgentWorkspaceRecords(current, [early, later]), current);
  assert.equal(
    mergeAgentWorkspaceRecords(current, [tieWinner]).get(taskId).state,
    "awake",
  );
  assert.equal(
    mergeAgentWorkspaceRecords(new Map(), [tieWinner, early, later]).get(
      taskId,
    ),
    tieWinner,
  );
});

test("grouping uses IDs, matching manager authority, membership and a single visible level", () => {
  const root = parseAgentWorkspaceEvent(event(managerId));
  const task = parseAgentWorkspaceEvent(event());
  const records = new Map([
    [managerId, root],
    [taskId, task],
  ]);
  const channels = [
    channel(managerId, "khoi-local"),
    channel(taskId, "task-research"),
    channel(otherId, "task-research"),
  ];
  const grouped = buildAgentWorkspaceGroups(channels, records);
  assert.deepEqual(
    grouped.groups[0].tasks.map(({ channel }) => channel.id),
    [taskId],
  );
  assert.deepEqual([...grouped.groupedChannelIds], [managerId, taskId]);
  assert.equal(
    buildAgentWorkspaceGroups(channels.slice(1), records).groups.length,
    0,
  );
  assert.equal(
    buildAgentWorkspaceGroups(
      [channel(managerId, "m", { isMember: false }), channels[1]],
      records,
    ).groups.length,
    0,
  );
  for (const mutation of [
    { managerPubkey: worker },
    { location: "remote" },
    { managerChannelId: otherId },
  ]) {
    const wrong = new Map([
      [managerId, root],
      [taskId, { ...task, ...mutation }],
    ]);
    assert.equal(
      buildAgentWorkspaceGroups(channels, wrong).groups[0].tasks.length,
      0,
    );
  }
  const renamed = buildAgentWorkspaceGroups(
    [channels[0], channel(taskId, "renamed")],
    records,
  );
  assert.equal(renamed.groups[0].tasks[0].channel.name, "renamed");
});

test("automatic groups avoid ordinary/custom duplicates while orphans stay visible", () => {
  const channels = [channel(managerId), channel(taskId), channel(otherId)];
  const buckets = sidebarChannelBuckets(
    channels,
    [{ id: "custom" }],
    { [taskId]: "custom", [otherId]: "custom" },
    new Set(),
    new Set([managerId, taskId]),
    () => "alpha",
  );
  assert.deepEqual(buckets.unassigned, []);
  assert.deepEqual(
    buckets.bySection.custom.map(({ id }) => id),
    [otherId],
  );
});
