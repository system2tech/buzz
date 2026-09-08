//! Database-backed authorization regression. Never defaults to a production database.
use super::relay_members::{check_relay_membership, materialize_nip_oa_owner, MembershipDecision};
use crate::state::AppState;
use buzz_core::TenantContext;
use buzz_sdk::nip_oa::compute_auth_tag;
use nostr::Keys;
use std::sync::Arc;

async fn delegation_test_state() -> Arc<AppState> {
    let mut config = crate::config::Config::from_env().expect("default config loads");
    config.require_relay_membership = true;
    config.allow_nip_oa_auth = true;
    config.redis_url = "redis://127.0.0.1:1".to_string();
    config.database_url = std::env::var("BUZZ_TEST_DATABASE_URL")
        .expect("set BUZZ_TEST_DATABASE_URL to an isolated migrated test database");
    let pool = sqlx::PgPool::connect(&config.database_url)
        .await
        .expect("connect test DB");
    let db = buzz_db::Db::from_pool(pool.clone());
    let redis_pool = deadpool_redis::Config::from_url(&config.redis_url)
        .create_pool(Some(deadpool_redis::Runtime::Tokio1))
        .expect("redis pool");
    let pubsub = Arc::new(
        buzz_pubsub::PubSubManager::new(&config.redis_url, redis_pool.clone())
            .await
            .expect("pubsub manager"),
    );
    let audit = buzz_audit::AuditService::new(pool.clone());
    let auth = buzz_auth::AuthService::new(config.auth.clone());
    let search = buzz_search::SearchService::new(pool.clone());
    let workflow_engine = Arc::new(buzz_workflow::WorkflowEngine::new(
        db.clone(),
        buzz_workflow::WorkflowConfig::default(),
    ));
    let media_storage = buzz_media::MediaStorage::new(&config.media).expect("media storage");
    let (state, _audit_shutdown) = AppState::new(
        config,
        db,
        redis_pool,
        audit,
        pubsub,
        auth,
        search,
        workflow_engine,
        nostr::Keys::generate(),
        media_storage,
    );
    Arc::new(state)
}

#[tokio::test]
#[ignore = "requires BUZZ_TEST_DATABASE_URL pointing at migrated test PostgreSQL"]
async fn delegated_worker_membership_real_database() {
    let state = delegation_test_state().await;
    let host = format!("delegation-{}.test", uuid::Uuid::new_v4());
    let other_host = format!("delegation-{}.test", uuid::Uuid::new_v4());
    let community = state
        .db
        .ensure_configured_community(&host)
        .await
        .unwrap()
        .id;
    let other = state
        .db
        .ensure_configured_community(&other_host)
        .await
        .unwrap()
        .id;
    let tenant = TenantContext::resolved(community, host);
    let human = Keys::generate();
    let manager = Keys::generate();
    let worker = Keys::generate();
    let child = Keys::generate();
    let forged_signer = Keys::generate();
    state
        .db
        .add_relay_member(community, &human.public_key().to_hex(), "member", None)
        .await
        .unwrap();
    let manager_tag = compute_auth_tag(&human, &manager.public_key(), "").unwrap();
    assert_eq!(
        check_relay_membership(
            &state,
            community,
            manager.public_key().as_bytes(),
            Some(&manager_tag)
        )
        .await
        .unwrap(),
        MembershipDecision::ViaOwner(human.public_key())
    );
    // Exercise the same cryptographically verified mapping persistence as authentication.
    assert!(
        materialize_nip_oa_owner(&state, &tenant, &manager.public_key(), &human.public_key()).await
    );
    let worker_tag = compute_auth_tag(&manager, &worker.public_key(), "").unwrap();
    assert_eq!(
        check_relay_membership(
            &state,
            community,
            worker.public_key().as_bytes(),
            Some(&worker_tag)
        )
        .await
        .unwrap(),
        MembershipDecision::ViaOwner(manager.public_key())
    );
    assert_eq!(
        check_relay_membership(
            &state,
            other,
            worker.public_key().as_bytes(),
            Some(&worker_tag)
        )
        .await
        .unwrap(),
        MembershipDecision::Denied
    );
    let forged = compute_auth_tag(&forged_signer, &worker.public_key(), "").unwrap();
    assert_eq!(
        check_relay_membership(
            &state,
            community,
            worker.public_key().as_bytes(),
            Some(&forged)
        )
        .await
        .unwrap(),
        MembershipDecision::Denied
    );
    // A proof for a different key must not authorize the caller.
    assert_eq!(
        check_relay_membership(
            &state,
            community,
            child.public_key().as_bytes(),
            Some(&worker_tag)
        )
        .await
        .unwrap(),
        MembershipDecision::Denied
    );
    assert!(
        materialize_nip_oa_owner(&state, &tenant, &worker.public_key(), &manager.public_key())
            .await
    );
    let child_tag = compute_auth_tag(&worker, &child.public_key(), "").unwrap();
    assert_eq!(
        check_relay_membership(
            &state,
            community,
            child.public_key().as_bytes(),
            Some(&child_tag)
        )
        .await
        .unwrap(),
        MembershipDecision::Denied
    );
    state
        .db
        .remove_relay_member(community, &human.public_key().to_hex())
        .await
        .unwrap();
    assert_eq!(
        check_relay_membership(
            &state,
            community,
            worker.public_key().as_bytes(),
            Some(&worker_tag)
        )
        .await
        .unwrap(),
        MembershipDecision::Denied
    );
    // Fixtures use unique tenants/keys in the explicitly selected test DB.
}
