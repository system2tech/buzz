use std::collections::HashMap;

use nostr::EventId;

use crate::client::{normalize_write_response, BuzzClient};
use crate::error::CliError;
use crate::validate::validate_hex64;

fn matching_reaction_event_id(events: &[serde_json::Value], emoji: &str) -> Option<String> {
    events
        .iter()
        .find(|event| event.get("content").and_then(|content| content.as_str()) == Some(emoji))
        .and_then(|event| event.get("id").and_then(|id| id.as_str()))
        .map(str::to_string)
}

pub async fn cmd_add_reaction(
    client: &BuzzClient,
    event_id: &str,
    emoji: &str,
    emoji_url: Option<&str>,
) -> Result<(), CliError> {
    validate_hex64(event_id)?;
    let target_eid =
        EventId::parse(event_id).map_err(|e| CliError::Usage(format!("invalid event ID: {e}")))?;

    let builder = if let Some(url) = emoji_url {
        buzz_sdk::build_custom_emoji_reaction(target_eid, emoji, url)
            .map_err(|e| CliError::Other(format!("build_custom_emoji_reaction failed: {e}")))?
    } else {
        buzz_sdk::build_reaction(target_eid, emoji)
            .map_err(|e| CliError::Other(format!("build_reaction failed: {e}")))?
    };

    let event = client.sign_event(builder)?;

    let resp = client.submit_event(event).await?;
    println!("{}", normalize_write_response(&resp));
    Ok(())
}

pub async fn cmd_remove_reaction(
    client: &BuzzClient,
    event_id: &str,
    emoji: &str,
) -> Result<(), CliError> {
    validate_hex64(event_id)?;
    let keys = client.keys();

    // Find our reaction event by querying kind:7 reactions on this event from us
    let my_pk = keys.public_key().to_hex();
    let filter = serde_json::json!({
        "kinds": [7],
        "#e": [event_id],
        "authors": [my_pk]
    });
    let raw = client.query(&filter).await?;
    let events: serde_json::Value = serde_json::from_str(&raw)
        .map_err(|e| CliError::Other(format!("failed to parse reactions query: {e}")))?;
    let arr = events
        .as_array()
        .ok_or_else(|| CliError::Other("reactions query response is not an array".into()))?;

    // Find the reaction event matching the emoji
    let Some(reaction_event_id) = matching_reaction_event_id(arr, emoji) else {
        // Removal is a desired-state operation. A previous request may have
        // succeeded while its response was lost, or the user may have removed
        // the reaction themselves. Either way the requested state already holds.
        println!(
            "{}",
            serde_json::json!({
                "accepted": true,
                "message": "reaction already absent",
            })
        );
        return Ok(());
    };

    let reaction_eid = EventId::parse(&reaction_event_id)
        .map_err(|e| CliError::Other(format!("invalid reaction event ID: {e}")))?;

    let builder = buzz_sdk::build_remove_reaction(reaction_eid)
        .map_err(|e| CliError::Other(format!("build_remove_reaction failed: {e}")))?;

    let event = client.sign_event(builder)?;

    let resp = client.submit_event(event).await?;
    println!("{}", normalize_write_response(&resp));
    Ok(())
}

pub async fn cmd_get_reactions(client: &BuzzClient, event_id: &str) -> Result<(), CliError> {
    validate_hex64(event_id)?;
    let filter = serde_json::json!({
        "kinds": [7],
        "#e": [event_id]
    });
    let resp = client.query(&filter).await?;
    let events: Vec<serde_json::Value> = serde_json::from_str(&resp).unwrap_or_default();

    let mut groups: HashMap<String, Vec<String>> = HashMap::new();
    for e in &events {
        let emoji = e
            .get("content")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .unwrap_or("+")
            .to_string();
        let pubkey = e
            .get("pubkey")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        groups.entry(emoji).or_default().push(pubkey);
    }

    let mut reactions: Vec<serde_json::Value> = groups
        .into_iter()
        .map(|(emoji, pubkeys)| {
            serde_json::json!({
                "emoji": emoji,
                "count": pubkeys.len(),
                "pubkeys": pubkeys,
            })
        })
        .collect();
    reactions.sort_by(|a, b| {
        a.get("emoji")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .cmp(b.get("emoji").and_then(|v| v.as_str()).unwrap_or(""))
    });

    let output = serde_json::json!({ "reactions": reactions });
    println!("{}", serde_json::to_string(&output).unwrap_or_default());
    Ok(())
}

pub async fn dispatch(cmd: crate::ReactionsCmd, client: &BuzzClient) -> Result<(), CliError> {
    use crate::ReactionsCmd;
    match cmd {
        ReactionsCmd::Add {
            event,
            emoji,
            emoji_url,
        } => cmd_add_reaction(client, &event, &emoji, emoji_url.as_deref()).await,
        ReactionsCmd::Remove { event, emoji } => cmd_remove_reaction(client, &event, &emoji).await,
        ReactionsCmd::Get { event } => cmd_get_reactions(client, &event).await,
    }
}

#[cfg(test)]
mod tests {
    use super::matching_reaction_event_id;
    use serde_json::json;

    #[test]
    fn matching_reaction_returns_only_the_requested_emoji() {
        let events = vec![
            json!({"id": "heart-id", "content": "❤️"}),
            json!({"id": "eyes-id", "content": "👀"}),
        ];
        assert_eq!(
            matching_reaction_event_id(&events, "👀").as_deref(),
            Some("eyes-id")
        );
        assert_eq!(matching_reaction_event_id(&events, "💬"), None);
    }

    #[test]
    fn malformed_reaction_events_do_not_look_present() {
        let events = vec![json!({"content": "👀"}), json!({"id": 7, "content": "👀"})];
        assert_eq!(matching_reaction_event_id(&events, "👀"), None);
    }
}
