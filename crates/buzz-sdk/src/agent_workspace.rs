//! Typed builder for manager/task workspace metadata.

use buzz_core::agent_workspace::AgentWorkspace;
use buzz_core::kind::KIND_AGENT_WORKSPACE;
use nostr::{EventBuilder, Kind, PublicKey, Tag, Timestamp};

use crate::SdkError;

/// Build channel-scoped workspace metadata. The caller signs as `manager`.
///
/// An explicit creation timestamp binds lease validation to the actual event
/// timestamp, including when callers queue the builder before signing.
pub fn build_agent_workspace(
    channel_id: &str,
    manager: &PublicKey,
    workspace: &AgentWorkspace,
    created_at: Timestamp,
) -> Result<EventBuilder, SdkError> {
    workspace
        .validate(channel_id, manager, created_at.as_secs())
        .map_err(SdkError::InvalidInput)?;
    let content =
        serde_json::to_string(workspace).map_err(|e| SdkError::InvalidInput(e.to_string()))?;
    let tag =
        |name| Tag::parse([name, channel_id]).map_err(|e| SdkError::InvalidTag(e.to_string()));
    Ok(
        EventBuilder::new(Kind::Custom(KIND_AGENT_WORKSPACE as u16), content)
            .tags([tag("d")?, tag("h")?])
            .custom_created_at(created_at),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use buzz_core::agent_workspace::{AgentLocation, AgentWorkspaceState};
    use nostr::Keys;

    #[test]
    fn signed_builder_round_trips_the_shared_contract() {
        let keys = Keys::generate();
        let channel = "11111111-1111-4111-8111-111111111111";
        let record = AgentWorkspace {
            version: 1,
            manager_channel_id: channel.into(),
            agent_pubkey: keys.public_key().to_hex(),
            location: AgentLocation::Remote,
            state: AgentWorkspaceState::Awake,
            valid_until: 1180,
        };
        let event =
            build_agent_workspace(channel, &keys.public_key(), &record, Timestamp::from(1000))
                .unwrap()
                .sign_with_keys(&keys)
                .unwrap();
        assert_eq!(event.tags.len(), 2);
        assert_eq!(AgentWorkspace::from_event(&event).unwrap().1, record);
    }
}
