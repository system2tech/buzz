//! Agent observer frame helpers.
//!
//! Observer frames are transient, owner-scoped agent telemetry/control messages.
//! They use a Buzz ephemeral event kind and carry NIP-44 encrypted JSON in the
//! event content so relays can route frames without reading ACP internals.

use nostr::{nips::nip44, Event, Keys, PublicKey};
use serde::{de::DeserializeOwned, Serialize};
use thiserror::Error;
use zeroize::Zeroize;

/// Tag name that identifies the agent pubkey the observer frame belongs to.
pub const OBSERVER_AGENT_TAG: &str = "agent";
/// Channel whose members may read this telemetry; never grants control.
pub const OBSERVER_CHANNEL_TAG: &str = "observer_channel";
/// Tag name that identifies the cleartext frame direction.
pub const OBSERVER_FRAME_TAG: &str = "frame";
/// Frame value for agent-to-owner observer telemetry.
pub const OBSERVER_FRAME_TELEMETRY: &str = "telemetry";
/// Frame value for owner-to-agent observer control commands.
pub const OBSERVER_FRAME_CONTROL: &str = "control";
/// Minimum plausible NIP-44 v2 ciphertext length.
pub const NIP44_MIN_CONTENT_LEN: usize = 132;
/// Maximum NIP-44 v2 ciphertext length.
pub const NIP44_MAX_CONTENT_LEN: usize = 87_472;
/// Maximum observer plaintext JSON size accepted by helpers.
///
/// **Must stay below what NIP-44 will actually encrypt** (S2). rust-nostr caps a
/// v2 plaintext at `65_536 - 128 = 65_408`
/// (`nostr::nips::nip44::v2::MAX_SUPPORTED_PLAINTEXT_SIZE`), so a limit of 65_535
/// opened a 127-byte dead zone: `fit_observer_event_to_budget` trimmed an
/// oversized frame down to exactly this value, `nip44::encrypt` then refused it,
/// and `publish_relay_observer_event` dropped the frame with only a warning. The
/// frames that hit it are the biggest ones -- a whole turn's coalesced assistant
/// text -- so the failure took out precisely the content a human most wanted to
/// see. Observed 17 times in one day on a busy agent
/// (`failed to encrypt relay observer event: NIP-44 error: message too long`).
///
/// 64_000 rather than 65_408: the trim is computed on the payload, while the
/// limit applies to the serialized frame around it, so a value flush against the
/// ceiling re-enters the dead zone as soon as the envelope grows. The margin is
/// cheap -- elision already handles anything over it.
pub const OBSERVER_MAX_PLAINTEXT_LEN: usize = 64_000;

/// Errors returned by observer payload encryption/decryption helpers.
#[derive(Debug, Error)]
pub enum ObserverPayloadError {
    /// NIP-44 encryption or decryption failed.
    #[error("NIP-44 error: {0}")]
    Nip44(#[from] nip44::Error),
    /// JSON serialization or deserialization failed.
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),
    /// Ciphertext did not fit the expected NIP-44 v2 length envelope.
    #[error("invalid NIP-44 ciphertext length: {0}")]
    InvalidCiphertextLength(usize),
    /// Decrypted JSON exceeded the observer plaintext size limit.
    #[error("observer plaintext exceeds {max} bytes (got {got})")]
    PlaintextTooLarge {
        /// Maximum accepted plaintext bytes.
        max: usize,
        /// Actual plaintext byte count.
        got: usize,
    },
    /// A payload field violated a NIP-AM numeric constraint.
    #[error("invalid payload field: {0}")]
    InvalidPayload(String),
}

/// Returns true when `content` fits the NIP-44 v2 ciphertext length envelope.
pub fn content_looks_like_nip44(content: &str) -> bool {
    (NIP44_MIN_CONTENT_LEN..=NIP44_MAX_CONTENT_LEN).contains(&content.len())
}

/// Parse the optional channel observation scope, rejecting ambiguous routing.
pub fn observer_channel(event: &Event) -> Result<Option<uuid::Uuid>, ObserverPayloadError> {
    let tags: Vec<_> = event
        .tags
        .iter()
        .filter(|tag| tag.kind().to_string() == OBSERVER_CHANNEL_TAG)
        .collect();
    if tags.is_empty() {
        return Ok(None);
    }
    if tags.len() != 1 || tags[0].as_slice().len() != 2 {
        return Err(ObserverPayloadError::InvalidPayload(
            "ambiguous observer channel".into(),
        ));
    }
    let channel = tags[0]
        .content()
        .and_then(|v| v.parse::<uuid::Uuid>().ok())
        .ok_or_else(|| ObserverPayloadError::InvalidPayload("invalid observer channel".into()))?;
    let frames: Vec<_> = event
        .tags
        .iter()
        .filter(|tag| tag.kind().to_string() == OBSERVER_FRAME_TAG)
        .collect();
    if frames.len() != 1 || frames[0].content() != Some(OBSERVER_FRAME_TELEMETRY) {
        return Err(ObserverPayloadError::InvalidPayload(
            "channel scope is telemetry only".into(),
        ));
    }
    Ok(Some(channel))
}

/// Enforce the encrypted payload's channel boundary, including every batch item.
/// Unscoped legacy owner frames retain their original payload format.
fn validate_channel_payload(
    event: &Event,
    payload: &serde_json::Value,
) -> Result<(), ObserverPayloadError> {
    let Some(channel) = observer_channel(event)? else {
        return Ok(());
    };
    fn matches_channel(value: &serde_json::Value, channel: uuid::Uuid) -> bool {
        value
            .get("channelId")
            .and_then(|v| v.as_str())
            .and_then(|v| v.parse::<uuid::Uuid>().ok())
            == Some(channel)
    }
    if !matches_channel(payload, channel) {
        return Err(ObserverPayloadError::InvalidPayload(
            "observer payload crosses channel scope".into(),
        ));
    }
    if payload.get("kind").and_then(|v| v.as_str()) == Some("batch") {
        let items = payload
            .get("payload")
            .and_then(|v| v.get("events"))
            .and_then(|v| v.as_array())
            .ok_or_else(|| ObserverPayloadError::InvalidPayload("invalid observer batch".into()))?;
        if items.is_empty()
            || items.iter().any(|v| {
                !matches_channel(v, channel)
                    || v.get("kind").and_then(|k| k.as_str()) == Some("batch")
            })
        {
            return Err(ObserverPayloadError::InvalidPayload(
                "observer batch crosses channel scope".into(),
            ));
        }
    }
    Ok(())
}

/// Serialize and NIP-44 encrypt an observer payload for `recipient`.
pub fn encrypt_observer_payload<T: Serialize>(
    sender_keys: &Keys,
    recipient: &PublicKey,
    payload: &T,
) -> Result<String, ObserverPayloadError> {
    let mut plaintext = serde_json::to_string(payload)?;
    if plaintext.len() > OBSERVER_MAX_PLAINTEXT_LEN {
        let got = plaintext.len();
        plaintext.zeroize();
        return Err(ObserverPayloadError::PlaintextTooLarge {
            max: OBSERVER_MAX_PLAINTEXT_LEN,
            got,
        });
    }

    let encrypted = nip44::encrypt(
        sender_keys.secret_key(),
        recipient,
        &plaintext,
        nip44::Version::V2,
    )?;
    plaintext.zeroize();
    Ok(encrypted)
}

/// NIP-44 decrypt and deserialize an observer payload from `event`.
pub fn decrypt_observer_payload<T: DeserializeOwned>(
    recipient_keys: &Keys,
    event: &Event,
) -> Result<T, ObserverPayloadError> {
    if !content_looks_like_nip44(&event.content) {
        return Err(ObserverPayloadError::InvalidCiphertextLength(
            event.content.len(),
        ));
    }

    if observer_channel(event)?.is_some() {
        for (name, expected) in [
            ("p", recipient_keys.public_key()),
            (OBSERVER_AGENT_TAG, event.pubkey),
        ] {
            let tags: Vec<_> = event
                .tags
                .iter()
                .filter(|tag| tag.kind().to_string() == name)
                .collect();
            if tags.len() != 1
                || tags[0].as_slice().len() != 2
                || tags[0].content().and_then(|v| PublicKey::from_hex(v).ok()) != Some(expected)
            {
                return Err(ObserverPayloadError::InvalidPayload(
                    "invalid channel observer sender or recipient".into(),
                ));
            }
        }
    }
    let mut plaintext = nip44::decrypt(
        recipient_keys.secret_key(),
        &event.pubkey,
        event.content.as_str(),
    )?;
    if plaintext.len() > OBSERVER_MAX_PLAINTEXT_LEN {
        let got = plaintext.len();
        plaintext.zeroize();
        return Err(ObserverPayloadError::PlaintextTooLarge {
            max: OBSERVER_MAX_PLAINTEXT_LEN,
            got,
        });
    }

    // Validate the signed cleartext scope before any consumer sees the payload,
    // including archive ingestion paths that bypass the live desktop hook.
    let result = (|| {
        if observer_channel(event)?.is_some() {
            let value: serde_json::Value = serde_json::from_str(&plaintext)?;
            validate_channel_payload(event, &value)?;
        }
        Ok(serde_json::from_str(&plaintext)?)
    })();
    plaintext.zeroize();
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use nostr::{EventBuilder, Kind, Tag};

    fn scoped_frame(
        sender: &Keys,
        recipient: &Keys,
        channel: uuid::Uuid,
        payload: serde_json::Value,
    ) -> Event {
        EventBuilder::new(
            Kind::Custom(crate::kind::KIND_AGENT_OBSERVER_FRAME as u16),
            encrypt_observer_payload(sender, &recipient.public_key(), &payload).expect("encrypt"),
        )
        .tags([
            Tag::public_key(recipient.public_key()),
            Tag::parse([OBSERVER_AGENT_TAG, &sender.public_key().to_hex()]).expect("agent"),
            Tag::parse([OBSERVER_CHANNEL_TAG, &channel.to_string()]).expect("channel"),
            Tag::parse([OBSERVER_FRAME_TAG, OBSERVER_FRAME_TELEMETRY]).expect("frame"),
        ])
        .sign_with_keys(sender)
        .expect("sign")
    }

    #[test]
    fn channel_observer_encryption_is_per_recipient_and_scope_checked() {
        let agent = Keys::generate();
        let member = Keys::generate();
        let outsider = Keys::generate();
        let channel = uuid::Uuid::new_v4();
        let payload = serde_json::json!({"kind":"turn_started", "channelId": channel.to_string()});
        let event = scoped_frame(&agent, &member, channel, payload.clone());
        assert_eq!(
            decrypt_observer_payload::<serde_json::Value>(&member, &event)
                .expect("member decrypts"),
            payload
        );
        assert!(decrypt_observer_payload::<serde_json::Value>(&outsider, &event).is_err());
        let wrong = scoped_frame(
            &agent,
            &member,
            channel,
            serde_json::json!({"kind":"turn_started", "channelId": uuid::Uuid::new_v4().to_string()}),
        );
        assert!(decrypt_observer_payload::<serde_json::Value>(&member, &wrong).is_err());
    }

    #[test]
    fn channel_observer_rejects_mixed_or_nested_batches_and_missing_scope() {
        let agent = Keys::generate();
        let member = Keys::generate();
        let channel = uuid::Uuid::new_v4();
        let good = serde_json::json!({"kind":"turn_started", "channelId":channel.to_string()});
        for bad in [
            serde_json::json!({"kind":"turn_started"}),
            serde_json::json!({"kind":"turn_started", "channelId":uuid::Uuid::new_v4().to_string()}),
            serde_json::json!({"kind":"batch", "channelId":channel.to_string(), "payload":{"events":[]}}),
        ] {
            let event = scoped_frame(
                &agent,
                &member,
                channel,
                serde_json::json!({
                "kind":"batch", "channelId":channel.to_string(), "payload":{"events":[good,bad]}}),
            );
            assert!(decrypt_observer_payload::<serde_json::Value>(&member, &event).is_err());
        }
        let event = scoped_frame(
            &agent,
            &member,
            channel,
            serde_json::json!({
            "kind":"batch", "channelId":channel.to_string(), "payload":{"events":[good.clone(),good]}}),
        );
        assert!(decrypt_observer_payload::<serde_json::Value>(&member, &event).is_ok());
    }

    #[test]
    fn channel_observer_rejects_control_or_ambiguous_tags() {
        let keys = Keys::generate();
        let channel = uuid::Uuid::new_v4().to_string();
        for tags in [
            vec![
                (OBSERVER_CHANNEL_TAG, channel.as_str()),
                (OBSERVER_FRAME_TAG, OBSERVER_FRAME_CONTROL),
            ],
            vec![
                (OBSERVER_CHANNEL_TAG, channel.as_str()),
                (OBSERVER_CHANNEL_TAG, channel.as_str()),
                (OBSERVER_FRAME_TAG, OBSERVER_FRAME_TELEMETRY),
            ],
            vec![
                (OBSERVER_CHANNEL_TAG, "invalid"),
                (OBSERVER_FRAME_TAG, OBSERVER_FRAME_TELEMETRY),
            ],
        ] {
            let event = EventBuilder::new(
                Kind::Custom(crate::kind::KIND_AGENT_OBSERVER_FRAME as u16),
                "",
            )
            .tags(
                tags.into_iter()
                    .map(|(k, v)| Tag::parse([k, v]).expect("tag")),
            )
            .sign_with_keys(&keys)
            .expect("sign");
            assert!(observer_channel(&event).is_err());
        }
    }

    #[test]
    fn observer_payload_round_trips_with_nip44() {
        let sender = Keys::generate();
        let recipient = Keys::generate();
        let payload = serde_json::json!({
            "type": "turn_started",
            "turnId": "turn-1"
        });
        let encrypted = encrypt_observer_payload(&sender, &recipient.public_key(), &payload)
            .expect("encrypt payload");
        assert!(content_looks_like_nip44(&encrypted));

        let event = EventBuilder::new(
            Kind::Custom(crate::kind::KIND_AGENT_OBSERVER_FRAME as u16),
            encrypted,
        )
        .tags([Tag::public_key(recipient.public_key())])
        .sign_with_keys(&sender)
        .expect("sign event");
        let decrypted: serde_json::Value =
            decrypt_observer_payload(&recipient, &event).expect("decrypt payload");
        assert_eq!(decrypted, payload);
    }

    #[test]
    fn observer_payload_rejects_short_ciphertext() {
        let sender = Keys::generate();
        let recipient = Keys::generate();
        let event = EventBuilder::new(
            Kind::Custom(crate::kind::KIND_AGENT_OBSERVER_FRAME as u16),
            "not encrypted",
        )
        .tags([Tag::public_key(recipient.public_key())])
        .sign_with_keys(&sender)
        .expect("sign event");

        assert!(matches!(
            decrypt_observer_payload::<serde_json::Value>(&recipient, &event),
            Err(ObserverPayloadError::InvalidCiphertextLength(_))
        ));
    }
}
