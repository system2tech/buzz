import { expect, test, type Page } from "@playwright/test";
import { installMockBridge } from "../helpers/bridge";
import { waitForAnimations } from "../helpers/animations";
import type { RelayEvent } from "../../src/shared/api/types";

const LOCAL = "94a444a4-c0a3-5966-ab05-530c6ddc2301";
const REMOTE = "9dae0116-799b-5071-a0a8-fdd30a91a35d";
const TASK = "1c7e1c02-87bb-5e88-b2da-5a7a9432d0c9";
const REMOTE_TASK = "9a1657ac-f7aa-5db0-b632-d8bbeb6dfb50";
const HIDDEN = "b5e2f8a1-3c44-5912-9e67-4a8d1f2b3c4e";
const MANAGER_KEY = "a".repeat(64);

async function waitForMockLiveSubscription(page: Page, channelName: string) {
  await expect
    .poll(() =>
      page.evaluate(
        (name) =>
          window.__BUZZ_E2E_HAS_MOCK_LIVE_SUBSCRIPTION__?.({
            channelName: name,
            kind: 40002,
          }) ?? false,
        channelName,
      ),
    )
    .toBe(true);
}

async function seedWorkspaces(page: Page) {
  const createdAt = Math.floor(Date.now() / 1000) - 100;
  const records = [
    {
      channelId: LOCAL,
      managerChannelId: LOCAL,
      location: "local",
      state: "awake",
    },
    {
      channelId: REMOTE,
      managerChannelId: REMOTE,
      location: "remote",
      state: "awake",
    },
    {
      channelId: TASK,
      managerChannelId: LOCAL,
      location: "local",
      state: "sleeping",
    },
    {
      channelId: REMOTE_TASK,
      managerChannelId: REMOTE,
      location: "remote",
      state: "starting",
    },
    {
      channelId: HIDDEN,
      managerChannelId: LOCAL,
      location: "local",
      state: "sleeping",
    },
  ];
  const events: RelayEvent[] = records.map(
    ({ channelId, ...record }, index) => ({
      kind: 30180,
      id: String(index + 1).padStart(64, "0"),
      pubkey: MANAGER_KEY,
      created_at: createdAt,
      tags: [
        ["h", channelId],
        ["d", channelId],
      ],
      sig: "f".repeat(128),
      content: JSON.stringify({
        ...record,
        version: 1,
        validUntil: createdAt + 600,
        agentPubkey:
          channelId === record.managerChannelId ? MANAGER_KEY : "b".repeat(64),
      }),
    }),
  );
  await page.addInitScript((seed) => {
    window.__BUZZ_E2E_AGENT_WORKSPACE_EVENTS__ = seed;
  }, events);
  await installMockBridge(page);
}

async function renameForPreview(page: Page) {
  await page.evaluate(
    async (names) => {
      for (const [channelId, name] of names) {
        await window.__BUZZ_E2E_INVOKE_MOCK_COMMAND__?.("update_channel", {
          input: { channelId, name },
        });
      }
      await window.__BUZZ_E2E_INVALIDATE_CHANNELS__?.();
    },
    [
      [LOCAL, "khoi-local"],
      [REMOTE, "khoi-remote"],
      [TASK, "task-research"],
      [REMOTE_TASK, "task-review"],
    ],
  );
}

async function updateTask(
  page: Page,
  state: string,
  revision: number,
  expired = false,
) {
  await page.evaluate(
    ({ managerId, taskId, pubkey, state, revision, expired }) => {
      const createdAt = Math.floor(Date.now() / 1000) - 50 + revision;
      window.__BUZZ_E2E_EMIT_MOCK_MESSAGE__?.({
        channelName: "task-research",
        kind: 30180,
        pubkey,
        createdAt,
        id: revision.toString(16).padStart(64, "0"),
        extraTags: [["d", taskId]],
        content: JSON.stringify({
          version: 1,
          managerChannelId: managerId,
          agentPubkey: "b".repeat(64),
          location: "local",
          state,
          validUntil: createdAt + (expired ? 0 : 300),
        }),
      });
    },
    {
      managerId: LOCAL,
      taskId: TASK,
      pubkey: MANAGER_KEY,
      state,
      revision,
      expired,
    },
  );
}

test("manager tasks preserve channel behavior through sleep, wake, collapse and reload", async ({
  page,
}) => {
  await seedWorkspaces(page);
  await page.goto("/");
  await expect(page.getByTestId(`agent-workspace-${LOCAL}`)).toBeVisible();
  await renameForPreview(page);
  await expect(page.getByTestId("channel-task-research")).toBeVisible();
  await expect(page.getByTestId("channel-task-research")).toHaveCount(1);
  await expect(page.getByTestId("channel-design")).toHaveCount(0);
  const status = page.getByTestId(`agent-workspace-status-${TASK}`);
  await expect(status).toHaveText("Sleeping");
  await expect(
    page.getByTestId(`agent-workspace-status-${REMOTE_TASK}`),
  ).toHaveText("Starting");
  await expect(
    page
      .getByTestId("channel-task-research")
      .locator("[data-sidebar-row-label]"),
  ).toHaveClass(/text-sidebar-foreground\/75/);
  await waitForAnimations(page);
  await page
    .getByTestId("agent-workspaces")
    .screenshot({ path: "test-results/agent-workspaces-expanded.png" });

  await page.getByTestId("channel-khoi-local").click();
  await expect(page.getByTestId("chat-title")).toHaveText("khoi-local");
  await page.getByTestId(`agent-workspace-toggle-${LOCAL}`).click();
  await expect(page.getByTestId("channel-task-research")).toHaveCount(0);
  await expect(page.getByTestId("chat-title")).toHaveText("khoi-local");
  await page.reload();
  await expect(
    page.getByTestId(`agent-workspace-toggle-${LOCAL}`),
  ).toHaveAttribute("aria-expanded", "false");
  await renameForPreview(page);
  await page.getByTestId(`agent-workspace-toggle-${LOCAL}`).click();
  await page.getByTestId("channel-task-research").click();
  await expect(page.getByTestId("chat-title")).toHaveText("task-research");
  await waitForMockLiveSubscription(page, "task-research");
  await page.getByTestId("channel-khoi-local").click();

  await updateTask(page, "starting", 10);
  await expect(status).toHaveText("Starting");
  await updateTask(page, "awake", 11);
  await expect(status).toHaveCount(0);
  await updateTask(page, "failed", 12);
  await expect(status).toHaveText("Failed");
  await updateTask(page, "sleeping", 13, true);
  await expect(status).toHaveText("Unknown");
  await updateTask(page, "sleeping", 14);
  await expect(status).toHaveText("Sleeping");
  await expect(
    page
      .getByTestId(`agent-workspace-${LOCAL}`)
      .getByTestId("channel-task-research"),
  ).toBeVisible();

  await page.getByTestId("channel-task-research").click({ button: "right" });
  await expect(
    page.getByRole("menuitem", { name: "Star channel", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("menuitem", { name: "Copy", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page.evaluate(() =>
    window.__BUZZ_E2E_EMIT_MOCK_MESSAGE__?.({
      channelName: "task-research",
      content: "Research is ready for your review.",
      pubkey: "b".repeat(64),
      kind: 40002,
      mentionPubkeys: ["deadbeef".repeat(8)],
    }),
  );
  await expect(
    page
      .getByTestId("channel-task-research")
      .locator("[data-sidebar-row-label]"),
  ).not.toHaveClass(/text-sidebar-foreground\/75/);
  await page.getByTestId(`agent-workspace-toggle-${LOCAL}`).click();
  await expect(page.getByTestId(`manager-unread-${LOCAL}`)).toBeVisible();
  await waitForAnimations(page);
  await page
    .getByTestId("agent-workspaces")
    .screenshot({ path: "test-results/agent-workspaces-collapsed-unread.png" });
});

test("quick create wires an independent local manager from the sidebar", async ({
  page,
}) => {
  await installMockBridge(page);
  await page.goto("/");

  await page.getByTestId("create-local-agent").click();
  const dialog = page.getByRole("dialog", { name: "Create local agent" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByTestId("local-agent-access-warning")).toContainText(
    "Anyone can use this agent to access your computer",
  );

  await dialog
    .getByRole("textbox", { name: "Name", exact: true })
    .fill("Morning coordinator");
  await dialog
    .getByLabel("Role")
    .fill("Collect manager updates and write the daily digest.");
  await dialog
    .getByRole("textbox", { name: "Channel name", exact: true })
    .fill("#morning-coordinator");
  await dialog.getByRole("button", { name: "Create and start" }).click();
  await expect(dialog).toBeHidden();

  const createInput = await page.evaluate(() => {
    const call = [...(window.__BUZZ_E2E_COMMAND_PAYLOADS__ ?? [])]
      .reverse()
      .find((entry) => entry.command === "create_managed_agent");
    return (call?.payload as { input?: Record<string, unknown> } | null)?.input;
  });
  expect(createInput).toMatchObject({
    name: "Morning coordinator",
    backend: { type: "local" },
    respondTo: "anyone",
    spawnAfterCreate: true,
    startOnAppLaunch: true,
    localAgentSetup: {
      channelName: "morning-coordinator",
    },
  });
  expect(createInput?.systemPrompt).toContain(
    "Collect manager updates and write the daily digest.",
  );
  expect(createInput?.systemPrompt).toContain("#morning-coordinator");
  expect(
    (createInput?.localAgentSetup as { expectedRelayUrl?: string })
      .expectedRelayUrl,
  ).toBe(createInput?.relayUrl);
  expect(
    (createInput?.localAgentSetup as { expectedSignerPubkey?: string })
      .expectedSignerPubkey,
  ).toMatch(/^[0-9a-f]{64}$/);
});
