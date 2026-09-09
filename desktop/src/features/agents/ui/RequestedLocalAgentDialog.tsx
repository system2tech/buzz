import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { toast } from "sonner";

import { channelsQueryKey } from "@/features/channels/hooks";
import { canonicalChannelName } from "@/features/channels/lib/canonicalChannelName";
import { useCommunities } from "@/features/communities/useCommunities";
import {
  managedAgentsQueryKey,
  relayAgentsQueryKey,
  useAvailableAcpRuntimes,
} from "@/features/agents/hooks";
import { buildLocalAgentPrompt } from "@/features/agents/lib/localAgentPrompt";
import { agentAccessWarningText } from "@/features/agents/lib/agentAccessWarning";
import { getDefaultPersonaRuntime } from "@/features/agents/lib/resolvePersonaRuntime";
import {
  consumePendingOpenLocalAgent,
  subscribeOpenLocalAgent,
} from "@/features/agents/openLocalAgentEvent";
import { useAgentAccessOwnerOnlyQuery } from "@/features/agents/useAgentAccessOwnerOnly";
import { useGlobalAgentConfig } from "@/features/agents/useGlobalAgentConfig";
import { createManagedAgent } from "@/shared/api/tauri";
import { useIdentityQuery } from "@/shared/api/hooks";
import { Button } from "@/shared/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/shared/ui/dialog";
import { Input } from "@/shared/ui/input";
import { Textarea } from "@/shared/ui/textarea";

export function RequestedLocalAgentDialog() {
  const queryClient = useQueryClient();
  const { activeCommunity } = useCommunities();
  const identityQuery = useIdentityQuery();
  const { globalConfig } = useGlobalAgentConfig();
  const relayUrl = activeCommunity?.relayUrl;
  const currentPubkey = identityQuery.data?.pubkey;
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [role, setRole] = React.useState("");
  const [channelName, setChannelName] = React.useState("");
  const [isCreating, setIsCreating] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const runtimesQuery = useAvailableAcpRuntimes({ enabled: open });
  const ownerOnlyQuery = useAgentAccessOwnerOnlyQuery({ enabled: open });
  const runtime = React.useMemo(
    () =>
      getDefaultPersonaRuntime(
        runtimesQuery.data ?? [],
        globalConfig.preferred_runtime,
      ),
    [globalConfig.preferred_runtime, runtimesQuery.data],
  );

  const openDialog = React.useEffectEvent(() => {
    setError(null);
    setOpen(true);
  });
  React.useEffect(() => {
    if (consumePendingOpenLocalAgent()) openDialog();
    return subscribeOpenLocalAgent(openDialog);
  }, []);

  function reset() {
    setName("");
    setRole("");
    setChannelName("");
    setError(null);
  }

  function closeDialog() {
    setOpen(false);
    reset();
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!relayUrl || !currentPubkey || !runtime || isCreating) return;
    setIsCreating(true);
    setError(null);
    try {
      const cleanName = name.trim();
      const cleanChannelName = canonicalChannelName(channelName);
      const created = await createManagedAgent({
        name: cleanName,
        relayUrl,
        acpCommand: "buzz-acp",
        agentCommand: runtime.command,
        harnessOverride: true,
        agentArgs: [],
        systemPrompt: buildLocalAgentPrompt({
          name: cleanName,
          role,
          channelName: cleanChannelName,
        }),
        spawnAfterCreate: true,
        startOnAppLaunch: true,
        backend: { type: "local" },
        respondTo: "anyone",
        localAgentSetup: {
          channelName: cleanChannelName,
          expectedRelayUrl: relayUrl,
          expectedSignerPubkey: currentPubkey,
        },
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: channelsQueryKey }),
        queryClient.invalidateQueries({ queryKey: managedAgentsQueryKey }),
        queryClient.invalidateQueries({ queryKey: relayAgentsQueryKey }),
      ]);
      closeDialog();
      if (created.spawnError) {
        toast.error(`${cleanName} was created but could not start`, {
          description: created.spawnError,
        });
      } else if (created.profileSyncError) {
        toast.warning(`${cleanName} is running with a relay warning`, {
          description: created.profileSyncError,
        });
      } else {
        toast.success(`${cleanName} is running`);
      }
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Could not create local agent.",
      );
    } finally {
      setIsCreating(false);
    }
  }

  const canSubmit =
    Boolean(
      relayUrl && currentPubkey && runtime && ownerOnlyQuery.data === false,
    ) &&
    name.trim().length > 0 &&
    role.trim().length > 0 &&
    canonicalChannelName(channelName).length > 0;

  return (
    <Dialog
      onOpenChange={(next) => {
        if (!isCreating) {
          setOpen(next);
          if (!next) reset();
        }
      }}
      open={open}
    >
      <DialogContent className="max-w-lg">
        <form className="space-y-5" onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>Create local agent</DialogTitle>
            <DialogDescription>
              Give it a role and a conversation. Buzz creates its identity,
              admits it to this workspace, and starts it on this computer.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-1.5">
            <label className="text-sm font-medium" htmlFor="local-agent-name">
              Name
            </label>
            <Input
              autoFocus
              disabled={isCreating}
              id="local-agent-name"
              onChange={(event) => setName(event.target.value)}
              placeholder="Daily coordinator"
              value={name}
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-sm font-medium" htmlFor="local-agent-role">
              Role
            </label>
            <Textarea
              className="min-h-28"
              disabled={isCreating}
              id="local-agent-role"
              onChange={(event) => setRole(event.target.value)}
              placeholder="Collect each manager's daily update, resolve gaps, and save the digest."
              value={role}
            />
          </div>

          <div className="space-y-1.5">
            <label
              className="text-sm font-medium"
              htmlFor="local-agent-channel"
            >
              Channel name
            </label>
            <Input
              autoCapitalize="none"
              autoCorrect="off"
              disabled={isCreating}
              id="local-agent-channel"
              onChange={(event) => setChannelName(event.target.value)}
              placeholder="daily-coordinator"
              value={channelName}
            />
          </div>

          {ownerOnlyQuery.isLoading ? (
            <p className="text-sm text-muted-foreground">
              Checking workspace access…
            </p>
          ) : ownerOnlyQuery.data === false ? (
            <div
              className="flex items-start gap-2 rounded-xl border border-warning/30 bg-warning-bg px-3 py-2.5"
              data-testid="local-agent-access-warning"
            >
              <AlertTriangle
                aria-hidden="true"
                className="mt-0.5 h-4 w-4 shrink-0 text-warning"
              />
              <p className="text-xs leading-5 text-warning">
                {agentAccessWarningText("anyone", "local")}
              </p>
            </div>
          ) : (
            <p className="text-sm text-destructive" role="alert">
              Independent local agents are unavailable in this build.
            </p>
          )}

          {runtimesQuery.isLoading ? (
            <p className="text-sm text-muted-foreground">
              Checking local agent runtime…
            </p>
          ) : !runtime ? (
            <p className="text-sm text-destructive" role="alert">
              No local agent runtime is available. Install one in Settings →
              Agents first.
            </p>
          ) : null}
          {error ? (
            <p className="text-sm text-destructive" role="alert">
              {error}
            </p>
          ) : null}

          <DialogFooter>
            <Button
              disabled={isCreating}
              onClick={closeDialog}
              type="button"
              variant="ghost"
            >
              Cancel
            </Button>
            <Button disabled={!canSubmit || isCreating} type="submit">
              {isCreating ? "Creating…" : "Create and start"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
