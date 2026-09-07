//! Publish signed manager/task relationships and bounded lifecycle leases.

use buzz_core::agent_workspace::{
    AgentLocation, AgentWorkspace, AgentWorkspaceState, MAX_LEASE_SECONDS,
};
use clap::{Subcommand, ValueEnum};
use nostr::Timestamp;

use crate::client::{normalize_write_response, BuzzClient};
use crate::error::CliError;

#[derive(Clone, ValueEnum)]
pub enum Location {
    Local,
    Remote,
}

#[derive(Clone, ValueEnum)]
pub enum State {
    Starting,
    Awake,
    Sleeping,
    Failed,
}

#[derive(Subcommand)]
pub enum AgentWorkspaceCmd {
    /// Publish a manager root or its task's current relationship and state
    Publish {
        /// Conversation UUID; equal to manager-channel for the manager root
        #[arg(long)]
        channel: String,
        /// Manager home conversation UUID (publish this root first)
        #[arg(long)]
        manager_channel: String,
        /// Manager public key on roots, worker public key on tasks (lowercase hex)
        #[arg(long)]
        agent_pubkey: String,
        #[arg(long, value_enum)]
        location: Location,
        #[arg(long, value_enum)]
        state: State,
        /// State freshness window; renew before expiry (grouping never expires)
        #[arg(long, default_value_t = 180, value_parser = clap::value_parser!(u64).range(0..=MAX_LEASE_SECONDS))]
        lease_seconds: u64,
    },
}

pub async fn dispatch(command: AgentWorkspaceCmd, client: &BuzzClient) -> Result<(), CliError> {
    let AgentWorkspaceCmd::Publish {
        channel,
        manager_channel,
        agent_pubkey,
        location,
        state,
        lease_seconds,
    } = command;
    let now = Timestamp::now();
    let workspace = AgentWorkspace {
        version: 1,
        manager_channel_id: manager_channel,
        agent_pubkey,
        location: match location {
            Location::Local => AgentLocation::Local,
            Location::Remote => AgentLocation::Remote,
        },
        state: match state {
            State::Starting => AgentWorkspaceState::Starting,
            State::Awake => AgentWorkspaceState::Awake,
            State::Sleeping => AgentWorkspaceState::Sleeping,
            State::Failed => AgentWorkspaceState::Failed,
        },
        valid_until: now
            .as_secs()
            .checked_add(lease_seconds)
            .ok_or_else(|| CliError::Usage("workspace lease timestamp overflows".into()))?,
    };
    let builder =
        buzz_sdk::build_agent_workspace(&channel, &client.keys().public_key(), &workspace, now)
            .map_err(|e| CliError::Usage(e.to_string()))?;
    let event = client.sign_event(builder)?;
    let event_id = event.id.to_hex();
    let response = client.submit_event(event).await?;
    let ack: serde_json::Value = serde_json::from_str(&response)
        .map_err(|e| CliError::Other(format!("invalid relay acknowledgement: {e}")))?;
    if ack.get("accepted").and_then(serde_json::Value::as_bool) != Some(true) {
        return Err(CliError::Other(
            "relay did not accept workspace metadata".into(),
        ));
    }
    if ack
        .get("message")
        .and_then(serde_json::Value::as_str)
        .is_some_and(|message| message.starts_with("duplicate"))
    {
        // A retry of the same event is harmless; a newer NIP-33 head is not.
        // Read back only on this ambiguous acknowledgement path.
        let raw = client
            .query(&serde_json::json!({
                "kinds": [buzz_core::kind::KIND_AGENT_WORKSPACE],
                "authors": [client.keys().public_key().to_hex()],
                "#d": [channel], "#h": [channel], "limit": 1,
            }))
            .await?;
        let heads: Vec<serde_json::Value> = serde_json::from_str(&raw)
            .map_err(|e| CliError::Other(format!("invalid workspace readback: {e}")))?;
        if heads
            .first()
            .and_then(|head| head.get("id"))
            .and_then(serde_json::Value::as_str)
            != Some(event_id.as_str())
        {
            return Err(CliError::Conflict(
                "a newer workspace status superseded this publication".into(),
            ));
        }
    }
    println!("{}", normalize_write_response(&response));
    Ok(())
}

#[cfg(test)]
mod tests {
    use clap::Parser;

    #[test]
    fn requires_known_states_locations_and_bounded_leases() {
        let base = [
            "buzz",
            "agent-workspace",
            "publish",
            "--channel",
            "11111111-1111-4111-8111-111111111111",
            "--manager-channel",
            "11111111-1111-4111-8111-111111111111",
            "--agent-pubkey",
            "aa",
            "--location",
            "local",
            "--state",
            "sleeping",
        ];
        assert!(crate::Cli::try_parse_from(base).is_ok());
        for lease in ["601", "-1"] {
            let mut args = base.to_vec();
            args.extend(["--lease-seconds", lease]);
            assert!(crate::Cli::try_parse_from(args).is_err());
        }
        let mut args = base;
        args[12] = "offline";
        assert!(crate::Cli::try_parse_from(args).is_err());
    }
}
