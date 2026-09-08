//! Opt-in channel observation, separate from owner controls and configuration.
use std::collections::BTreeSet;
use std::time::Duration;

use nostr::PublicKey;
use serde_json::{json, Value};

use crate::{observer::ObserverEvent, relay::RestClient};

// Never silently share with only an arbitrary prefix of a channel roster.
const MAX_RECIPIENTS: usize = 64;

pub(crate) struct ChannelMembers {
    rest: RestClient,
}

impl ChannelMembers {
    pub(crate) fn new(rest: RestClient) -> Self {
        Self { rest }
    }

    pub(crate) async fn recipients(
        &mut self,
        channel: &str,
        agent: &str,
        owner: &str,
    ) -> Vec<PublicKey> {
        let filter = json!({"kinds": [39002], "#d": [channel], "limit": 1});
        let result = tokio::time::timeout(
            Duration::from_secs(2),
            self.rest.query_raw_bounded(&[filter], 1024 * 1024),
        )
        .await;
        match result {
            Ok(Ok(events)) => match parse_members(&events, channel, agent, owner) {
                Some(members) => members,
                None => {
                    tracing::warn!(channel, "channel observer roster unavailable or exceeds 64 recipients; sharing skipped");
                    Vec::new()
                }
            },
            _ => {
                tracing::warn!(
                    channel,
                    "channel observer membership lookup failed; sharing skipped"
                );
                Vec::new()
            }
        }
    }
}

fn parse_members(
    events: &Value,
    channel: &str,
    agent: &str,
    owner: &str,
) -> Option<Vec<PublicKey>> {
    let events = events.as_array()?;
    if events.len() != 1 {
        return None;
    }
    let event = &events[0];
    if event.get("kind")?.as_u64()? != 39002 {
        return None;
    }
    let tags = event.get("tags")?.as_array()?;
    let scope: Vec<_> = tags.iter().filter(|t| t[0] == "d").collect();
    if scope.len() != 1 || scope[0][1].as_str()? != channel {
        return None;
    }
    let mut members = BTreeSet::new();
    for tag in tags.iter().filter(|t| t[0] == "p") {
        let key = PublicKey::from_hex(tag[1].as_str()?).ok()?;
        members.insert(key);
    }
    if !members.iter().any(|key| key.to_hex() == agent) {
        return None;
    }
    members.retain(|key| key.to_hex() != agent && key.to_hex() != owner);
    if members.len() > MAX_RECIPIENTS {
        return None;
    }
    Some(members.into_iter().collect())
}

fn allowed(event: &ObserverEvent) -> bool {
    match event.kind.as_str() {
        "turn_started" | "turn_completed" | "turn_liveness" | "session_resolved" => true,
        "acp_read" => {
            event.payload.get("method").and_then(Value::as_str) == Some("session/update")
                && event.payload.get("id").is_none()
                && matches!(
                    event
                        .payload
                        .pointer("/params/update/sessionUpdate")
                        .and_then(Value::as_str),
                    Some(
                        "agent_message_chunk"
                            | "agent_thought_chunk"
                            | "tool_call"
                            | "tool_call_update"
                            | "plan"
                    )
                )
        }
        _ => false,
    }
}

/// Filter batches without ever trusting their envelope to scope inner content.
pub(crate) fn shareable_frame(frame: &ObserverEvent) -> Option<ObserverEvent> {
    let channel = frame.channel_id.as_deref()?;
    uuid::Uuid::parse_str(channel).ok()?;
    if frame.kind != crate::OBSERVER_BATCH_KIND {
        return allowed(frame).then(|| frame.clone());
    }
    let inner = frame.payload.get("events")?.as_array()?;
    let mut events = Vec::new();
    for value in inner {
        let event: ObserverEvent = serde_json::from_value(value.clone()).ok()?;
        if event.channel_id.as_deref() != Some(channel) {
            // Malformed/mixed batches fail wholly closed.
            return None;
        }
        if allowed(&event) {
            events.push(event);
        }
    }
    if events.is_empty() {
        return None;
    }
    Some(crate::seal_batch(events))
}

#[cfg(test)]
mod tests {
    use super::*;
    const CHANNEL: &str = "12345678-1234-1234-1234-123456789012";
    fn event(kind: &str) -> ObserverEvent {
        ObserverEvent {
            seq: 1,
            timestamp: "now".into(),
            kind: kind.into(),
            agent_index: None,
            channel_id: Some(CHANNEL.into()),
            session_id: None,
            turn_id: None,
            started_at: None,
            payload: json!({}),
        }
    }
    #[test]
    fn private_and_mixed_batch_content_cannot_be_shared() {
        assert!(shareable_frame(&event("session_config_captured")).is_none());
        assert!(shareable_frame(&event("acp_write")).is_none());
        let live = event("turn_started");
        let private = event("control_result");
        let shared = shareable_frame(&crate::batch_envelope(&[live.clone(), private])).unwrap();
        assert_eq!(shared.kind, "turn_started");
        let mut foreign = live.clone();
        foreign.channel_id = Some(uuid::Uuid::new_v4().to_string());
        assert!(shareable_frame(&crate::batch_envelope(&[foreign, live])).is_none());
    }
    #[test]
    fn only_activity_notifications_pass() {
        let mut ev = event("acp_read");
        ev.payload =
            json!({"method":"session/update", "params":{"update":{"sessionUpdate":"tool_call"}}});
        assert!(shareable_frame(&ev).is_some());
        ev.payload["params"]["update"]["sessionUpdate"] = json!("config_option_update");
        assert!(shareable_frame(&ev).is_none());
        ev.payload = json!({"result":{"secret":"private"}});
        assert!(shareable_frame(&ev).is_none());
    }
    #[test]
    fn roster_requires_current_agent_membership_and_exact_scope() {
        let agent = nostr::Keys::generate().public_key().to_hex();
        let human = nostr::Keys::generate().public_key().to_hex();
        let mut roster =
            json!([{"kind":39002,"tags":[["d",CHANNEL],["p",agent],["p",human],["p",human]]}]);
        let recipients = parse_members(&roster, CHANNEL, &agent, "").unwrap();
        assert_eq!(recipients.len(), 1);
        assert_eq!(recipients[0].to_hex(), human);
        assert!(parse_members(&roster, CHANNEL, &human, &agent)
            .unwrap()
            .is_empty());
        roster[0]["tags"][0][1] = json!("other");
        assert!(parse_members(&roster, CHANNEL, &agent, "").is_none());
    }
    #[tokio::test]
    async fn each_shared_copy_is_encrypted_only_to_its_recipient() {
        let agent = nostr::Keys::generate();
        let alice = nostr::Keys::generate();
        let bob = nostr::Keys::generate();
        let (publisher, mut receiver) = crate::relay::RelayEventPublisher::test_pair();
        for recipient in [&alice, &bob] {
            crate::publish_relay_observer_event(
                &publisher,
                &agent,
                &agent.public_key().to_hex(),
                &recipient.public_key().to_hex(),
                &recipient.public_key(),
                event("turn_started"),
                true,
            )
            .await;
            let frame = receiver.recv().await.unwrap();
            assert!(frame
                .tags
                .iter()
                .any(|tag| tag.as_slice() == ["observer_channel", CHANNEL]));
            assert!(!frame
                .tags
                .iter()
                .any(|tag| tag.as_slice().first().is_some_and(|key| key == "h")));
            let plain: ObserverEvent = crate::decrypt_observer_payload(recipient, &frame).unwrap();
            assert_eq!(plain.channel_id.as_deref(), Some(CHANNEL));
            let stranger = if recipient.public_key() == alice.public_key() {
                &bob
            } else {
                &alice
            };
            assert!(crate::decrypt_observer_payload::<Value>(stranger, &frame).is_err());
        }
    }

    #[test]
    fn large_roster_is_rejected_wholly_instead_of_partial_sharing() {
        let agent = nostr::Keys::generate().public_key().to_hex();
        let mut tags = vec![json!(["d", CHANNEL]), json!(["p", agent])];
        for _ in 0..=MAX_RECIPIENTS {
            tags.push(json!(["p", nostr::Keys::generate().public_key().to_hex()]));
        }
        assert!(parse_members(&json!([{"kind":39002,"tags":tags}]), CHANNEL, &agent, "").is_none());
    }
    async fn roster_server(body: Value) -> (RestClient, tokio::task::JoinHandle<()>) {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let task = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut request = vec![0; 8192];
            let _ = stream.read(&mut request).await.unwrap();
            let body = body.to_string();
            stream.write_all(format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body).as_bytes()).await.unwrap();
        });
        (
            RestClient {
                http: reqwest::Client::new(),
                base_url: format!("http://{addr}"),
                keys: nostr::Keys::generate(),
                auth_tag_json: None,
            },
            task,
        )
    }

    #[tokio::test]
    async fn bounded_query_rejects_oversized_response() {
        let (rest, server) = roster_server(json!({"body":"x".repeat(4096)})).await;
        assert!(rest.query_raw_bounded(&[json!({})], 128).await.is_err());
        server.await.unwrap();
    }

    #[tokio::test]
    async fn five_member_fanout_is_paced_without_multiplying_source_delay() {
        let agent = nostr::Keys::generate();
        let owner = nostr::Keys::generate();
        let viewers: Vec<_> = (0..5).map(|_| nostr::Keys::generate()).collect();
        let mut tags = vec![
            json!(["d", CHANNEL]),
            json!(["p", agent.public_key().to_hex()]),
        ];
        for viewer in &viewers {
            tags.push(json!(["p", viewer.public_key().to_hex()]));
        }
        let (rest, server) = roster_server(json!([{"kind":39002,"tags":tags}])).await;
        let handle = crate::observer::ObserverHandle::in_process();
        let rx = handle.subscribe();
        drop(handle);
        let (publisher, mut published) = crate::relay::RelayEventPublisher::test_pair();
        let task = tokio::spawn(crate::run_relay_observer_publisher(
            vec![event("turn_started")],
            rx,
            publisher,
            agent.clone(),
            agent.public_key().to_hex(),
            owner.public_key(),
            Some(rest),
        ));
        let mut times = Vec::new();
        while let Some(frame) = published.recv().await {
            times.push(tokio::time::Instant::now());
            if times.len() > 1 {
                let target = frame
                    .tags
                    .iter()
                    .find(|tag| tag.as_slice()[0] == "p")
                    .unwrap()
                    .as_slice()[1]
                    .clone();
                let viewer = viewers
                    .iter()
                    .find(|key| key.public_key().to_hex() == target)
                    .unwrap();
                assert!(crate::decrypt_observer_payload::<Value>(viewer, &frame).is_ok());
            }
        }
        task.await.unwrap();
        server.await.unwrap();
        assert_eq!(times.len(), 6);
        assert!(
            times[5].duration_since(times[0]) >= Duration::from_millis(190),
            "copies must not burst"
        );
        assert!(
            times[5].duration_since(times[0]) < Duration::from_secs(1),
            "five viewers must share one source cadence"
        );
    }
}
