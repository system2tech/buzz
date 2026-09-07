//! Manager-authored channel relationships. Lease expiry makes status unknown;
//! it does not expire the relationship or delete conversation history.

use nostr::{Event, PublicKey};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::kind::KIND_AGENT_WORKSPACE;

/// Longest permitted lifecycle lease, in seconds.
pub const MAX_LEASE_SECONDS: u64 = 600;
/// Maximum encoded workspace payload size.
pub const MAX_CONTENT_BYTES: usize = 1024;

/// The machine class running a manager and its workers.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum AgentLocation {
    /// The user's local computer.
    Local,
    /// A remote host.
    Remote,
}

/// Supervisor-confirmed lifecycle state, authoritative only during its lease.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum AgentWorkspaceState {
    /// Startup has begun but readiness is not confirmed.
    Starting,
    /// The supervisor confirmed the worker is running.
    Awake,
    /// The supervisor intentionally hibernated the worker.
    Sleeping,
    /// Startup or the running process failed.
    Failed,
}

/// Version 1 of a channel's manager relationship and lifecycle lease.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AgentWorkspace {
    /// Wire version. Must be 1.
    pub version: u8,
    /// Canonical UUID of the manager's conversation.
    pub manager_channel_id: String,
    /// Lowercase hex public key of the manager (root) or worker (task).
    pub agent_pubkey: String,
    /// Where this manager and its workers run.
    pub location: AgentLocation,
    /// Explicit supervisor-reported state.
    pub state: AgentWorkspaceState,
    /// Unix seconds after which state is unknown, while grouping remains valid.
    pub valid_until: u64,
}

/// Parse a non-nil, lowercase, hyphenated UUID without accepting aliases.
pub fn canonical_channel_id(value: &str) -> Result<Uuid, String> {
    let id = Uuid::parse_str(value).map_err(|_| "channel id must be a UUID")?;
    if id.is_nil() || id.to_string() != value {
        return Err("channel id must be a canonical non-nil UUID".into());
    }
    Ok(id)
}

impl AgentWorkspace {
    /// Validate a record against its signed channel, author, and creation time.
    /// Channel membership and publisher authority are enforced by the relay.
    pub fn validate(
        &self,
        channel_id: &str,
        author: &PublicKey,
        created_at: u64,
    ) -> Result<(), String> {
        if self.version != 1 {
            return Err("unsupported agent workspace version".into());
        }
        canonical_channel_id(channel_id)?;
        canonical_channel_id(&self.manager_channel_id)?;
        let key = PublicKey::from_hex(&self.agent_pubkey)
            .map_err(|_| "agentPubkey must be a 64-character hex public key")?;
        if self.agent_pubkey.len() != 64 || key.to_hex() != self.agent_pubkey {
            return Err("agentPubkey must be a lowercase 64-character hex public key".into());
        }
        if channel_id == self.manager_channel_id && key != *author {
            return Err("manager workspace must name its signing identity".into());
        }
        let lease = self.valid_until.checked_sub(created_at);
        if !matches!(lease, Some(0..=MAX_LEASE_SECONDS)) {
            return Err(format!(
                "workspace lease must last 0..={MAX_LEASE_SECONDS} seconds"
            ));
        }
        Ok(())
    }

    /// Parse and validate a signed envelope, including unambiguous channel tags.
    /// Expired leases remain readable; no NIP-40 expiration tag is permitted.
    pub fn from_event(event: &Event) -> Result<(Uuid, Self), String> {
        if event.kind.as_u16() as u32 != KIND_AGENT_WORKSPACE {
            return Err("wrong kind for agent workspace".into());
        }
        if event.content.len() > MAX_CONTENT_BYTES {
            return Err("agent workspace content is too large".into());
        }
        let mut d = None;
        let mut h = None;
        for tag in event.tags.iter() {
            let parts = tag.as_slice();
            match parts.first().map(String::as_str) {
                Some("d") if parts.len() != 2 || d.replace(parts[1].as_str()).is_some() => {
                    return Err("workspace requires exactly one two-element d tag".into());
                }
                Some("h") if parts.len() != 2 || h.replace(parts[1].as_str()).is_some() => {
                    return Err("workspace requires exactly one two-element h tag".into());
                }
                Some("expiration" | "p" | "e") => {
                    return Err(
                        "workspace cannot expire, mention users, or reference messages".into(),
                    );
                }
                _ => {}
            }
        }
        let channel = d.ok_or("workspace requires a d tag")?;
        if h != Some(channel) {
            return Err("workspace h and d tags must name the same channel".into());
        }
        let record: Self = serde_json::from_str(&event.content)
            .map_err(|e| format!("invalid agent workspace payload: {e}"))?;
        record.validate(channel, &event.pubkey, event.created_at.as_secs())?;
        Ok((canonical_channel_id(channel)?, record))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use nostr::{EventBuilder, Keys, Kind, Tag, Timestamp};

    const CHANNEL: &str = "11111111-1111-4111-8111-111111111111";
    const PARENT: &str = "22222222-2222-4222-8222-222222222222";

    fn payload(keys: &Keys) -> AgentWorkspace {
        AgentWorkspace {
            version: 1,
            manager_channel_id: CHANNEL.into(),
            agent_pubkey: keys.public_key().to_hex(),
            location: AgentLocation::Local,
            state: AgentWorkspaceState::Sleeping,
            valid_until: 1180,
        }
    }

    fn event(keys: &Keys, record: &AgentWorkspace, tags: Vec<Vec<&str>>) -> Event {
        EventBuilder::new(
            Kind::Custom(KIND_AGENT_WORKSPACE as u16),
            serde_json::to_string(record).unwrap(),
        )
        .tags(tags.into_iter().map(|tag| Tag::parse(tag).unwrap()))
        .custom_created_at(Timestamp::from(1000))
        .sign_with_keys(keys)
        .unwrap()
    }

    #[test]
    fn expired_state_keeps_relationship() {
        let keys = Keys::generate();
        let event = event(
            &keys,
            &payload(&keys),
            vec![vec!["d", CHANNEL], vec!["h", CHANNEL]],
        );
        let (channel, record) = AgentWorkspace::from_event(&event).unwrap();
        assert_eq!(channel.to_string(), CHANNEL);
        assert_eq!(record.valid_until, 1180); // deliberately long expired
        assert_eq!(record.state, AgentWorkspaceState::Sleeping);
    }

    #[test]
    fn rejects_ambiguous_scope_and_expiration() {
        let keys = Keys::generate();
        for tags in [
            vec![vec!["d", CHANNEL]],
            vec![vec!["d", CHANNEL], vec!["h", PARENT]],
            vec![vec!["d", CHANNEL], vec!["h", CHANNEL], vec!["h", PARENT]],
            vec![vec!["d", CHANNEL], vec!["d", PARENT], vec!["h", CHANNEL]],
            vec![
                vec!["d", CHANNEL],
                vec!["h", CHANNEL],
                vec!["expiration", "1180"],
            ],
            vec![vec!["d", CHANNEL], vec!["h", CHANNEL], vec!["p", "mention"]],
        ] {
            assert!(AgentWorkspace::from_event(&event(&keys, &payload(&keys), tags)).is_err());
        }
    }

    #[test]
    fn validates_root_identity_and_bounded_lease() {
        let keys = Keys::generate();
        let mut record = payload(&keys);
        record.agent_pubkey = Keys::generate().public_key().to_hex();
        assert!(record.validate(CHANNEL, &keys.public_key(), 1000).is_err());
        record.manager_channel_id = PARENT.into();
        assert!(record.validate(CHANNEL, &keys.public_key(), 1000).is_ok());
        for expiry in [0, 999, 1601, u64::MAX] {
            record.valid_until = expiry;
            assert!(record.validate(CHANNEL, &keys.public_key(), 1000).is_err());
        }
    }

    #[test]
    fn zero_lease_can_publish_unknown_status_without_removing_grouping() {
        let keys = Keys::generate();
        let mut record = payload(&keys);
        record.valid_until = 1000;
        assert!(record.validate(CHANNEL, &keys.public_key(), 1000).is_ok());
    }

    #[test]
    fn rejects_aliases_unknown_fields_and_large_payloads() {
        let keys = Keys::generate();
        let mut record = payload(&keys);
        record.agent_pubkey = record.agent_pubkey.to_uppercase();
        assert!(record.validate(CHANNEL, &keys.public_key(), 1000).is_err());
        for channel in [
            "11111111111141118111111111111111",
            "00000000-0000-0000-0000-000000000000",
        ] {
            assert!(canonical_channel_id(channel).is_err());
        }
        let mut value = serde_json::to_value(payload(&keys)).unwrap();
        value["unexpected"] = serde_json::json!(true);
        assert!(serde_json::from_value::<AgentWorkspace>(value).is_err());
        let mut oversized = event(
            &keys,
            &payload(&keys),
            vec![vec!["d", CHANNEL], vec!["h", CHANNEL]],
        );
        oversized.content = " ".repeat(MAX_CONTENT_BYTES + 1);
        assert!(AgentWorkspace::from_event(&oversized).is_err());
    }
}
