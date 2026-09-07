import { CircleHelp, LoaderCircle, Moon, TriangleAlert } from "lucide-react";
import type { AgentWorkspaceStatus } from "../lib/agentWorkspaces";
import { cn } from "@/shared/lib/cn";

/** Explicit lifecycle labels keep intentional sleep distinct from lost contact. */
export function AgentWorkspaceBadge({
  status,
  channelId,
  isActive,
}: {
  status: AgentWorkspaceStatus;
  channelId: string;
  isActive: boolean;
}) {
  if (status === "awake") return null;
  const Icon =
    status === "sleeping"
      ? Moon
      : status === "starting"
        ? LoaderCircle
        : status === "failed"
          ? TriangleAlert
          : CircleHelp;
  const label =
    status === "sleeping"
      ? "Sleeping"
      : status === "starting"
        ? "Starting"
        : status === "failed"
          ? "Failed"
          : "Unknown";
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 text-2xs",
        isActive
          ? "text-sidebar-active-foreground"
          : status === "failed"
            ? "text-destructive"
            : "text-sidebar-foreground/75",
      )}
      data-testid={`agent-workspace-status-${channelId}`}
      data-status={status}
      title={
        status === "unknown"
          ? "No current status report. The manager may be unreachable."
          : label
      }
    >
      <Icon
        className={cn(
          "size-3",
          status === "starting" && "motion-safe:animate-spin",
        )}
        aria-hidden="true"
      />
      {label}
    </span>
  );
}
