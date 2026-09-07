//! Workspace metadata uses ordinary channel read access but stricter publishing
//! authority: only a manager administering its home channel may describe tasks
//! it created. Neither open-channel access nor worker presence grants authority.

use buzz_core::agent_workspace::AgentWorkspace;
use buzz_core::kind::KIND_AGENT_WORKSPACE;
use buzz_core::TenantContext;
use nostr::Event;
use uuid::Uuid;

use super::ingest::IngestError;

fn publisher_authorized(
    is_root: bool,
    parent_role: Option<&str>,
    publisher_is_member: bool,
    subject_is_member: bool,
    publisher_created_task: bool,
    has_matching_root: bool,
) -> bool {
    matches!(parent_role, Some("owner" | "admin"))
        && publisher_is_member
        && subject_is_member
        && (is_root || (publisher_created_task && has_matching_root))
}

/// Enforce publisher, subject, parent, and tenant boundaries before storage.
pub(super) async fn validate_authority(
    tenant: &TenantContext,
    db: &buzz_db::Db,
    event: &Event,
    channel_id: Uuid,
    manager_channel_id: Uuid,
    workspace: &AgentWorkspace,
) -> Result<(), IngestError> {
    let db_error = |e| IngestError::Internal(format!("workspace authority lookup failed: {e}"));
    let channel = db
        .get_channel(tenant.community(), channel_id)
        .await
        .map_err(|e| match e {
            buzz_db::DbError::ChannelNotFound(_) => {
                IngestError::Rejected("invalid: workspace channel not found".into())
            }
            e => db_error(e),
        })?;
    let parent = db
        .get_channel(tenant.community(), manager_channel_id)
        .await
        .map_err(|e| match e {
            buzz_db::DbError::ChannelNotFound(_) => {
                IngestError::Rejected("invalid: manager channel not found".into())
            }
            e => db_error(e),
        })?;
    if channel.channel_type != "stream"
        || parent.channel_type != "stream"
        || channel.deleted_at.is_some()
        || parent.deleted_at.is_some()
        || parent.archived_at.is_some()
    {
        return Err(IngestError::Rejected(
            "invalid: workspace requires active stream channels".into(),
        ));
    }
    let author = event.pubkey.to_bytes();
    let subject = hex::decode(&workspace.agent_pubkey)
        .map_err(|_| IngestError::Rejected("invalid: workspace agent pubkey".into()))?;
    // Fresh database membership, without the normal open-channel fallback.
    let parent_role = db
        .get_member_role(tenant.community(), manager_channel_id, &author)
        .await
        .map_err(db_error)?;
    let publisher_role = db
        .get_member_role(tenant.community(), channel_id, &author)
        .await
        .map_err(db_error)?;
    let subject_role = db
        .get_member_role(tenant.community(), channel_id, &subject)
        .await
        .map_err(db_error)?;

    let is_root = channel_id == manager_channel_id;
    let has_matching_root = if is_root {
        true
    } else {
        // Read from the writer in the same server-resolved community. A prior
        // root publication may be followed immediately by a worker publication.
        let roots = db
            .query_events(&buzz_db::event::EventQuery {
                channel_id: Some(manager_channel_id),
                kinds: Some(vec![KIND_AGENT_WORKSPACE as i32]),
                pubkey: Some(author.to_vec()),
                d_tag: Some(manager_channel_id.to_string()),
                limit: Some(1),
                ..buzz_db::event::EventQuery::for_community(tenant.community())
            })
            .await
            .map_err(db_error)?;
        roots
            .first()
            .and_then(|root| AgentWorkspace::from_event(&root.event).ok())
            .is_some_and(|(root_channel, root)| {
                root_channel == manager_channel_id
                    && root.manager_channel_id == manager_channel_id.to_string()
                    && root.agent_pubkey == event.pubkey.to_hex()
                    && root.location == workspace.location
            })
    };
    if !publisher_authorized(
        is_root,
        parent_role.as_deref(),
        publisher_role.is_some(),
        subject_role.is_some(),
        channel.created_by.as_slice() == author.as_slice(),
        has_matching_root,
    ) {
        return Err(IngestError::AuthFailed(
            "restricted: workspace publisher must administer its manager channel, create its tasks, and include the agent as a channel member; publish the matching manager root first".into(),
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn owner_and_admin_can_publish_their_own_root() {
        for role in ["owner", "admin"] {
            assert!(publisher_authorized(
                true,
                Some(role),
                true,
                true,
                false,
                false
            ));
        }
        for role in [None, Some("member"), Some("bot"), Some("guest")] {
            assert!(!publisher_authorized(true, role, true, true, true, true));
        }
    }

    #[test]
    fn another_manager_cannot_claim_a_task_it_did_not_create() {
        assert!(!publisher_authorized(
            false,
            Some("owner"),
            true,
            true,
            false,
            true
        ));
        assert!(publisher_authorized(
            false,
            Some("owner"),
            true,
            true,
            true,
            true
        ));
    }

    #[test]
    fn task_requires_both_members_and_matching_registered_root() {
        for (publisher, subject, root) in [
            (false, true, true),
            (true, false, true),
            (true, true, false),
        ] {
            assert!(!publisher_authorized(
                false,
                Some("admin"),
                publisher,
                subject,
                true,
                root
            ));
        }
    }
}

#[cfg(test)]
#[path = "agent_workspace_tests.rs"]
mod database_tests;
