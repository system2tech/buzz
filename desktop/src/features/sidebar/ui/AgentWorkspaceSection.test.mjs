import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { JSDOM } from "jsdom";
import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AgentWorkspaceSection } from "./AgentWorkspaceSection.tsx";
import { SidebarProvider } from "@/shared/ui/sidebar";
import { AppShellProvider, useAppShell } from "@/app/AppShellContext";

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://localhost",
});
before(() => {
  Object.assign(globalThis, {
    document: dom.window.document,
    window: dom.window,
    HTMLElement: dom.window.HTMLElement,
    IS_REACT_ACT_ENVIRONMENT: true,
  });
  window.matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  });
});
after(() => dom.window.close());

test("manager disclosure, conversation navigation, sleep styling and user-scoped restoration", async () => {
  const { render, fireEvent } = await import("@testing-library/react");
  const manager = { id: "manager", name: "khoi-local", channelType: "stream" };
  const task = { id: "task", name: "task-research", channelType: "stream" };
  const record = {
    state: "sleeping",
    validUntil: Math.floor(Date.now() / 1000) + 300,
    location: "local",
  };
  const groups = [
    {
      manager,
      record: { ...record, state: "awake" },
      tasks: [{ channel: task, record }],
    },
  ];
  const navigations = [];
  const client = new QueryClient({
    defaultOptions: {
      queries: { enabled: false, retry: false, gcTime: Infinity },
    },
  });
  const base = {
    groups,
    relayUrl: "wss://one.example",
    currentPubkey: "viewer",
    connected: true,
    selectedChannelId: null,
    isActiveChannel: false,
    unreadChannelIds: new Set(),
    unreadChannelCounts: new Map(),
    activeWorkingByChannelId: new Map(),
    onSelectChannel: (id) => navigations.push(id),
  };
  function Wrapper({ children }) {
    const defaults = useAppShell();
    return React.createElement(
      AppShellProvider,
      {
        value: {
          ...defaults,
          hasSidebarUnreadProjections: true,
          topLevelUnreadChannelIds: new Set(),
          unreadThreadChannelIds: new Set(),
        },
      },
      React.createElement(
        QueryClientProvider,
        { client },
        React.createElement(SidebarProvider, null, children),
      ),
    );
  }
  const view = render(React.createElement(AgentWorkspaceSection, base), {
    wrapper: Wrapper,
  });
  try {
    const taskLabel = () =>
      view
        .getByTestId("channel-task-research")
        .querySelector("[data-sidebar-row-label]");
    assert.equal(
      view.getByTestId("agent-workspace-status-task").textContent,
      "Sleeping",
    );
    assert.match(taskLabel().className, /text-sidebar-foreground\/75/);
    fireEvent.click(view.getByTestId("channel-khoi-local"));
    assert.deepEqual(navigations, ["manager"]);
    fireEvent.click(view.getByTestId("channel-task-research"));
    assert.deepEqual(navigations, ["manager", "task"]);
    view.rerender(
      React.createElement(AgentWorkspaceSection, {
        ...base,
        unreadChannelIds: new Set(["task"]),
      }),
    );
    assert.doesNotMatch(taskLabel().className, /text-sidebar-foreground\/75/);
    fireEvent.click(view.getByTestId("agent-workspace-toggle-manager"));
    assert.equal(view.queryByTestId("channel-task-research"), null);
    assert.equal(
      view
        .getByTestId("agent-workspace-toggle-manager")
        .getAttribute("aria-expanded"),
      "false",
    );
    assert.ok(view.getByTestId("manager-unread-manager"));
    assert.deepEqual(navigations, ["manager", "task"]);
    view.rerender(
      React.createElement(AgentWorkspaceSection, {
        ...base,
        currentPubkey: "second-viewer",
      }),
    );
    assert.ok(view.getByTestId("channel-task-research"));
    view.rerender(React.createElement(AgentWorkspaceSection, base));
    assert.equal(view.queryByTestId("channel-task-research"), null);
    fireEvent.click(view.getByTestId("agent-workspace-toggle-manager"));
    view.rerender(
      React.createElement(AgentWorkspaceSection, { ...base, connected: false }),
    );
    assert.equal(
      view.getByTestId("agent-workspace-status-task").textContent,
      "Unknown",
    );
    assert.doesNotMatch(taskLabel().className, /text-sidebar-foreground\/75/);
  } finally {
    view.unmount();
  }
});
