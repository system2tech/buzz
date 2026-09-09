import * as React from "react";
import { ChevronDown } from "lucide-react";
import type { ActiveChannelTurnSummary } from "@/features/agents/activeAgentTurnsStore";
import type { Channel } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { useNow } from "@/shared/lib/useNow";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuTrigger,
} from "@/shared/ui/context-menu";
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuItem,
} from "@/shared/ui/sidebar";
import {
  agentWorkspaceStatus,
  type AgentWorkspaceGroup,
  type AgentWorkspaceRecord,
} from "../lib/agentWorkspaces";
import {
  agentWorkspaceExpansionKey,
  readAgentWorkspaceExpansion,
  writeAgentWorkspaceExpansion,
} from "../lib/agentWorkspaceExpansion";
import { ChannelContextMenuItems } from "./ChannelContextMenu";
import { ChannelMenuButton } from "./SidebarSection";
import { SectionQuickAction } from "./CustomChannelSection";

type ChannelActions = Pick<
  React.ComponentProps<typeof ChannelContextMenuItems>,
  | "onMarkChannelRead"
  | "onMarkChannelUnread"
  | "onMuteChannel"
  | "onUnmuteChannel"
  | "onStarChannel"
  | "onUnstarChannel"
  | "onDeleteChannel"
  | "onLeaveChannel"
>;

type RowProps = ChannelActions & {
  selectedChannelId: string | null;
  isActiveChannel: boolean;
  unreadChannelIds: ReadonlySet<string>;
  unreadChannelCounts: ReadonlyMap<string, number>;
  mutedChannelIds?: ReadonlySet<string>;
  starredChannelIds?: ReadonlySet<string>;
  activeWorkingByChannelId: ReadonlyMap<string, ActiveChannelTurnSummary>;
  onSelectChannel: (channelId: string) => void;
  connected: boolean;
};

function AgentWorkspaceRow({
  channel,
  record,
  now,
  disclosure,
  unreadTaskCount,
  ...props
}: RowProps & {
  channel: Channel;
  record: AgentWorkspaceRecord;
  now: number;
  disclosure?: React.ReactNode;
  unreadTaskCount?: number;
}) {
  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>
        <SidebarMenuItem
          className="group/menu-item flex items-center gap-0.5"
          data-workspace-channel-id={channel.id}
        >
          {disclosure}
          <div className="min-w-0 flex-1">
            <ChannelMenuButton
              channel={channel}
              isActive={
                props.isActiveChannel && props.selectedChannelId === channel.id
              }
              hasUnread={props.unreadChannelIds.has(channel.id)}
              unreadCount={props.unreadChannelCounts.get(channel.id) ?? 0}
              activeWorking={props.activeWorkingByChannelId.get(channel.id)}
              isMuted={props.mutedChannelIds?.has(channel.id)}
              onSelectChannel={props.onSelectChannel}
              workspaceStatus={agentWorkspaceStatus(
                record,
                now,
                props.connected,
              )}
              workspaceUnreadTaskCount={unreadTaskCount}
            />
          </div>
        </SidebarMenuItem>
      </ContextMenuTrigger>
      <ContextMenuContent>
        <ChannelContextMenuItems
          channel={channel}
          hasUnread={props.unreadChannelIds.has(channel.id)}
          isMuted={props.mutedChannelIds?.has(channel.id)}
          isStarred={props.starredChannelIds?.has(channel.id)}
          onMarkChannelRead={props.onMarkChannelRead}
          onMarkChannelUnread={props.onMarkChannelUnread}
          onMuteChannel={props.onMuteChannel}
          onUnmuteChannel={props.onUnmuteChannel}
          onStarChannel={props.onStarChannel}
          onUnstarChannel={props.onUnstarChannel}
          onDeleteChannel={props.onDeleteChannel}
          onLeaveChannel={props.onLeaveChannel}
        />
      </ContextMenuContent>
    </ContextMenu>
  );
}

function AgentWorkspaceTree({
  groups,
  storageKey,
  onCreateLocalAgent,
  ...rowProps
}: RowProps & {
  groups: AgentWorkspaceGroup[];
  storageKey: string;
  onCreateLocalAgent: () => void;
}) {
  const [collapsed, setCollapsed] = React.useState(() =>
    readAgentWorkspaceExpansion(storageKey),
  );
  const now = Math.floor(useNow(1000) / 1000);
  const toggle = (channelId: string) => {
    setCollapsed((previous) => {
      const next = { ...previous, [channelId]: !previous[channelId] };
      writeAgentWorkspaceExpansion(storageKey, next);
      return next;
    });
  };
  React.useEffect(() => {
    const handler = (event: StorageEvent) => {
      if (event.key === storageKey)
        setCollapsed(readAgentWorkspaceExpansion(storageKey));
    };
    window.addEventListener("storage", handler);
    return () => window.removeEventListener("storage", handler);
  }, [storageKey]);
  return (
    <SidebarGroup
      className="group/sidebar-section select-none"
      data-testid="agent-workspaces"
    >
      <div className="relative">
        <SidebarGroupLabel>Agent managers</SidebarGroupLabel>
        <div className="absolute right-1 top-1/2 z-10 flex -translate-y-1/2">
          <SectionQuickAction
            label="Create local agent"
            onClick={onCreateLocalAgent}
            testId="create-local-agent"
          />
        </div>
      </div>
      <SidebarGroupContent>
        {groups.map(({ manager, record, tasks }) => {
          const isCollapsed = collapsed[manager.id] ?? false;
          const contentId = `agent-workspace-tasks-${manager.id}`;
          const unreadTaskCount = tasks.filter(({ channel }) =>
            rowProps.unreadChannelIds.has(channel.id),
          ).length;
          return (
            <div key={manager.id} data-testid={`agent-workspace-${manager.id}`}>
              <SidebarMenu>
                <AgentWorkspaceRow
                  {...rowProps}
                  channel={manager}
                  record={record}
                  now={now}
                  unreadTaskCount={isCollapsed ? unreadTaskCount : 0}
                  disclosure={
                    <button
                      type="button"
                      aria-label={`${isCollapsed ? "Expand" : "Collapse"} ${manager.name} tasks`}
                      aria-expanded={!isCollapsed}
                      aria-controls={contentId}
                      title={`${record.location === "local" ? "Local" : "Remote"} manager`}
                      className="flex size-6 shrink-0 items-center justify-center rounded-sm text-sidebar-foreground/70 hover:bg-sidebar-accent focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-sidebar-ring"
                      data-testid={`agent-workspace-toggle-${manager.id}`}
                      onClick={() => toggle(manager.id)}
                    >
                      <ChevronDown
                        className={cn(
                          "size-3.5 transition-transform",
                          isCollapsed && "-rotate-90",
                        )}
                        aria-hidden="true"
                      />
                    </button>
                  }
                />
              </SidebarMenu>
              {!isCollapsed ? (
                <SidebarMenu
                  id={contentId}
                  className="ml-3.5 w-auto border-l border-sidebar-border pl-3"
                >
                  {tasks.map(({ channel, record: taskRecord }) => (
                    <AgentWorkspaceRow
                      key={channel.id}
                      {...rowProps}
                      channel={channel}
                      record={taskRecord}
                      now={now}
                    />
                  ))}
                </SidebarMenu>
              ) : null}
            </div>
          );
        })}
      </SidebarGroupContent>
    </SidebarGroup>
  );
}

/** Render manager conversations with independently collapsible task channels. */
export function AgentWorkspaceSection({
  relayUrl,
  currentPubkey,
  groups,
  onCreateLocalAgent,
  ...props
}: RowProps & {
  relayUrl?: string;
  currentPubkey?: string;
  groups: AgentWorkspaceGroup[];
  onCreateLocalAgent: () => void;
}) {
  if (!relayUrl || !currentPubkey) return null;
  const storageKey = agentWorkspaceExpansionKey(relayUrl, currentPubkey);
  return (
    <AgentWorkspaceTree
      key={storageKey}
      storageKey={storageKey}
      groups={groups}
      onCreateLocalAgent={onCreateLocalAgent}
      {...props}
    />
  );
}
