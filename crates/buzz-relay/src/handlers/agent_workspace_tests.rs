//! Database-backed authority regression in an isolated temporary schema.

use super::*;
use buzz_core::agent_workspace::{AgentLocation, AgentWorkspaceState};
use buzz_core::channel::{ChannelType, ChannelVisibility};
use buzz_core::CommunityId;
use nostr::{Keys, Timestamp};
use sqlx::{postgres::PgPoolOptions, Executor};

fn workspace_event(
    manager: &Keys,
    channel: Uuid,
    parent: Uuid,
    subject: &Keys,
) -> (Event, AgentWorkspace) {
    let now = Timestamp::now();
    let workspace = AgentWorkspace {
        version: 1,
        manager_channel_id: parent.to_string(),
        agent_pubkey: subject.public_key().to_hex(),
        location: AgentLocation::Local,
        state: AgentWorkspaceState::Awake,
        valid_until: now.as_secs() + 180,
    };
    let event = buzz_sdk::build_agent_workspace(
        &channel.to_string(),
        &manager.public_key(),
        &workspace,
        now,
    )
    .unwrap()
    .sign_with_keys(manager)
    .unwrap();
    (event, workspace)
}

#[tokio::test]
#[ignore = "requires isolated Postgres via BUZZ_TEST_DATABASE_URL"]
async fn database_workspace_authority_enforces_roots_membership_and_tenant() {
    let url = std::env::var("BUZZ_TEST_DATABASE_URL").expect("explicit test database URL");
    let schema = format!("workspace_test_{}", Uuid::new_v4().simple());
    let admin = PgPoolOptions::new()
        .max_connections(1)
        .connect(&url)
        .await
        .unwrap();
    sqlx::query(sqlx::AssertSqlSafe(format!("CREATE SCHEMA {schema}")))
        .execute(&admin)
        .await
        .unwrap();
    let pool = PgPoolOptions::new()
        .max_connections(2)
        .connect(&format!("{url}?options=-c%20search_path%3D{schema}"))
        .await
        .unwrap();
    pool.execute(include_str!("../../../../schema/schema.sql"))
        .await
        .unwrap();
    let db = buzz_db::Db::from_pool(pool.clone());
    let community = CommunityId::from_uuid(Uuid::new_v4());
    let other_community = CommunityId::from_uuid(Uuid::new_v4());
    for (id, host) in [
        (community, "workspace.test"),
        (other_community, "other.test"),
    ] {
        sqlx::query("INSERT INTO communities (id, host, signing_key) VALUES ($1, $2, $3)")
            .bind(id.as_uuid())
            .bind(host)
            .bind(b"test".as_slice())
            .execute(&pool)
            .await
            .unwrap();
    }
    let tenant = TenantContext::resolved(community, "workspace.test");
    let other_tenant = TenantContext::resolved(other_community, "other.test");
    let manager = Keys::generate();
    let worker = Keys::generate();
    let stranger = Keys::generate();
    let parent = db
        .create_channel(
            community,
            "manager",
            ChannelType::Stream,
            ChannelVisibility::Open,
            None,
            &manager.public_key().to_bytes(),
            None,
        )
        .await
        .unwrap();
    let task = db
        .create_channel(
            community,
            "task",
            ChannelType::Stream,
            ChannelVisibility::Open,
            None,
            &manager.public_key().to_bytes(),
            None,
        )
        .await
        .unwrap();
    let (root_event, root) = workspace_event(&manager, parent.id, parent.id, &manager);
    validate_authority(&tenant, &db, &root_event, parent.id, parent.id, &root)
        .await
        .unwrap();

    let (task_event, task_workspace) = workspace_event(&manager, task.id, parent.id, &worker);
    // Neither missing root nor open channel access can stand in for registration.
    assert!(validate_authority(
        &tenant,
        &db,
        &task_event,
        task.id,
        parent.id,
        &task_workspace
    )
    .await
    .is_err());
    for key in [&worker, &stranger] {
        sqlx::query("INSERT INTO channel_members (community_id, channel_id, pubkey, role, invited_by) VALUES ($1, $2, $3, 'member', $4)")
            .bind(community.as_uuid()).bind(task.id).bind(key.public_key().to_bytes().as_slice())
            .bind(manager.public_key().to_bytes().as_slice()).execute(&pool).await.unwrap();
    }
    assert!(validate_authority(
        &tenant,
        &db,
        &task_event,
        task.id,
        parent.id,
        &task_workspace
    )
    .await
    .is_err());
    db.replace_parameterized_event(
        community,
        &root_event,
        &parent.id.to_string(),
        Some(parent.id),
    )
    .await
    .unwrap();
    validate_authority(
        &tenant,
        &db,
        &task_event,
        task.id,
        parent.id,
        &task_workspace,
    )
    .await
    .unwrap();
    let (_, inserted) = db
        .replace_parameterized_event(community, &task_event, &task.id.to_string(), Some(task.id))
        .await
        .unwrap();
    assert!(inserted);

    // Status leases never create unread/activity timestamps, even on roots.
    assert!(db
        .get_last_message_at(community, task.id)
        .await
        .unwrap()
        .is_none());
    assert!(db
        .get_last_message_at_bulk(community, &[task.id, parent.id])
        .await
        .unwrap()
        .is_empty());
    let message_time = Timestamp::from(task_event.created_at.as_secs() - 60);
    let message = nostr::EventBuilder::new(
        nostr::Kind::Custom(buzz_core::kind::KIND_STREAM_MESSAGE as u16),
        "actual conversation",
    )
    .tags([nostr::Tag::parse(["h", &task.id.to_string()]).unwrap()])
    .custom_created_at(message_time)
    .sign_with_keys(&worker)
    .unwrap();
    db.insert_event(community, &message, Some(task.id))
        .await
        .unwrap();
    let single_last = db
        .get_last_message_at(community, task.id)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(single_last.timestamp() as u64, message_time.as_secs());
    let bulk_last = db
        .get_last_message_at_bulk(community, &[task.id, parent.id])
        .await
        .unwrap();
    assert_eq!(bulk_last.get(&task.id), Some(&single_last));
    assert!(!bulk_last.contains_key(&parent.id));

    // A second manager administering its own parent cannot steal the first's task.
    let stranger_parent = db
        .create_channel(
            community,
            "stranger-manager",
            ChannelType::Stream,
            ChannelVisibility::Open,
            None,
            &stranger.public_key().to_bytes(),
            None,
        )
        .await
        .unwrap();
    let (stranger_root, _) =
        workspace_event(&stranger, stranger_parent.id, stranger_parent.id, &stranger);
    db.replace_parameterized_event(
        community,
        &stranger_root,
        &stranger_parent.id.to_string(),
        Some(stranger_parent.id),
    )
    .await
    .unwrap();
    let (forgery, forged_workspace) =
        workspace_event(&stranger, task.id, stranger_parent.id, &worker);
    assert!(matches!(
        validate_authority(
            &tenant,
            &db,
            &forgery,
            task.id,
            stranger_parent.id,
            &forged_workspace
        )
        .await,
        Err(IngestError::AuthFailed(_))
    ));

    // Same signed coordinates cannot resolve channels or roots in another tenant.
    assert!(validate_authority(
        &other_tenant,
        &db,
        &task_event,
        task.id,
        parent.id,
        &task_workspace
    )
    .await
    .is_err());

    // Removing the subject takes effect immediately, regardless of open visibility.
    sqlx::query("UPDATE channel_members SET removed_at = NOW() WHERE community_id = $1 AND channel_id = $2 AND pubkey = $3")
        .bind(community.as_uuid()).bind(task.id).bind(worker.public_key().to_bytes().as_slice())
        .execute(&pool).await.unwrap();
    assert!(matches!(
        validate_authority(
            &tenant,
            &db,
            &task_event,
            task.id,
            parent.id,
            &task_workspace
        )
        .await,
        Err(IngestError::AuthFailed(_))
    ));

    drop(db);
    pool.close().await;
    sqlx::query(sqlx::AssertSqlSafe(format!("DROP SCHEMA {schema} CASCADE")))
        .execute(&admin)
        .await
        .unwrap();
    admin.close().await;
}
