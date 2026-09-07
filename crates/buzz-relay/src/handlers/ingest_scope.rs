//! Publish-scope registry kept separate from the ingestion pipeline.

use super::*;

/// Determine the required scope for a given event kind.
///
/// Returns `Err` for unknown kinds — the relay rejects them.
pub(super) fn required_scope_for_kind(kind: u32, event: &Event) -> Result<Scope, &'static str> {
    match kind {
        KIND_PROFILE => Ok(Scope::UsersWrite),
        KIND_TEXT_NOTE | KIND_LONG_FORM => Ok(Scope::MessagesWrite),
        KIND_CONTACT_LIST | KIND_READ_STATE | KIND_USER_STATUS | KIND_AGENT_ENGRAM
        | KIND_EVENT_REMINDER | KIND_PERSONA | KIND_TEAM | KIND_MANAGED_AGENT
        | KIND_PRIVATE_MANAGED_AGENT | KIND_TEAM_CATALOG | super::super::push_lease::KIND_PUSH_LEASE => {
            Ok(Scope::UsersWrite)
        }
        // NIP-AM: agent turn metrics are agent-authored global events (encrypted to owner).
        KIND_AGENT_TURN_METRIC => Ok(Scope::MessagesWrite),
        // NIP-56 reports are ordinary member writes into the mod-only queue.
        // Ingest persists them to `moderation_reports` and suppresses public
        // storage/fanout; reports are signals, never enforcement triggers.
        KIND_REPORT | KIND_PRODUCT_FEEDBACK => Ok(Scope::MessagesWrite),
        // Community moderation commands are direct, mod-authz-gated writes.
        // Scope only proves the transport can submit message writes; the
        // command handler owns role/capability authorization.
        k if buzz_core::kind::is_moderation_command_kind(k) => Ok(Scope::MessagesWrite),
        // NIP-51 standard lists and NIP-65 relay list — user-owned global state,
        // same ownership shape as kind:3 (contacts) and kind:0 (profile).
        KIND_MUTE_LIST
        | KIND_PIN_LIST
        | KIND_NIP65_RELAY_LIST_METADATA
        | KIND_BOOKMARK_LIST
        | KIND_FOLLOW_SET
        | KIND_BOOKMARK_SET
        // NIP-30/NIP-51: per-user custom emoji set (30030) and emoji list (10030).
        // User-owned global state, keyed by (pubkey, kind[, d_tag]); the workspace
        // palette is the client-side union of every member's own set.
        | KIND_EMOJI_SET
        | KIND_EMOJI_LIST
        | KIND_AGENT_PROFILE => Ok(Scope::UsersWrite),
        KIND_DELETION
        | KIND_REACTION
        | KIND_GIFT_WRAP
        | KIND_STREAM_MESSAGE
        | KIND_STREAM_MESSAGE_V2
        | KIND_NIP29_DELETE_EVENT
        | KIND_STREAM_MESSAGE_EDIT
        | KIND_STREAM_MESSAGE_PINNED
        | KIND_STREAM_MESSAGE_BOOKMARKED
        | KIND_STREAM_MESSAGE_SCHEDULED
        | KIND_STREAM_REMINDER
        | KIND_STREAM_MESSAGE_DIFF
        | KIND_FORUM_POST
        | KIND_FORUM_VOTE
        | KIND_FORUM_COMMENT => Ok(Scope::MessagesWrite),
        KIND_NIP29_PUT_USER | KIND_NIP29_REMOVE_USER | KIND_NIP29_DELETE_GROUP => {
            Ok(Scope::AdminChannels)
        }
        // NIP-43: relay membership admin commands (9030–9032) + Buzz
        // workspace-profile command (9033).
        k if k == RELAY_ADMIN_ADD_MEMBER
            || k == RELAY_ADMIN_REMOVE_MEMBER
            || k == RELAY_ADMIN_CHANGE_ROLE
            || k == RELAY_ADMIN_SET_WORKSPACE_PROFILE =>
        {
            Ok(Scope::AdminUsers)
        }
        // NIP-IA: identity archive/unarchive requests (9035/9036).
        // Scope is intentionally UsersWrite, not AdminUsers: NIP-IA's self and
        // owner-of-agent paths are open to ordinary users (a user retiring their
        // own key, or an owner archiving their agent). Real authorization is the
        // consent-path check inside handle_identity_archive_event — the relay
        // verifies self / admin-role / owner-via-live-kind:0 there. This gate
        // only ensures the actor can write user-scoped state, which any
        // profile-publishing user already holds.
        KIND_IA_ARCHIVE_REQUEST | KIND_IA_UNARCHIVE_REQUEST => Ok(Scope::UsersWrite),
        KIND_NIP29_EDIT_METADATA => {
            // kind:9002 scope split: archived tag → AdminChannels, else ChannelsWrite
            let has_archived = event
                .tags
                .iter()
                .any(|t| t.kind().to_string() == "archived");
            if has_archived {
                Ok(Scope::AdminChannels)
            } else {
                Ok(Scope::ChannelsWrite)
            }
        }
        KIND_NIP29_CREATE_GROUP | KIND_CANVAS | KIND_AGENT_WORKSPACE => Ok(Scope::ChannelsWrite),
        KIND_NIP29_JOIN_REQUEST | KIND_NIP29_LEAVE_REQUEST | KIND_NIP43_LEAVE_REQUEST => {
            Ok(Scope::ChannelsRead)
        }
        // Huddle lifecycle events + guidelines
        KIND_HUDDLE_STARTED
        | KIND_HUDDLE_PARTICIPANT_JOINED
        | KIND_HUDDLE_PARTICIPANT_LEFT
        | KIND_HUDDLE_ENDED
        | KIND_HUDDLE_GUIDELINES => Ok(Scope::ChannelsWrite),
        // NIP-34: Git repository events
        KIND_GIT_REPO_ANNOUNCEMENT | KIND_GIT_REPO_STATE => Ok(Scope::ReposWrite),
        // NIP-MP: a project is repository metadata — grouping repositories needs
        // the same scope as announcing them.
        KIND_PROJECT => Ok(Scope::ReposWrite),
        KIND_GIT_PATCH
        | KIND_GIT_PULL_REQUEST
        | KIND_GIT_PR_UPDATE
        | KIND_GIT_ISSUE
        | KIND_GIT_STATUS_OPEN
        | KIND_GIT_STATUS_MERGED
        | KIND_GIT_STATUS_CLOSED
        | KIND_GIT_STATUS_DRAFT => Ok(Scope::MessagesWrite),
        // Command kinds — DM management, workflows, approvals
        KIND_DM_OPEN | KIND_DM_ADD_MEMBER | KIND_DM_HIDE => Ok(Scope::MessagesWrite),
        KIND_WORKFLOW_DEF | KIND_WORKFLOW_TRIGGER => Ok(Scope::MessagesWrite),
        KIND_APPROVAL_GRANT | KIND_APPROVAL_DENY => Ok(Scope::MessagesWrite),
        _ => Err("restricted: unknown event kind"),
    }
}
