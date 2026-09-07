//! Metadata must stay out of search even when legacy storage indexes all kinds.

use buzz_core::CommunityId;
use buzz_search::{ChannelScope, SearchQuery, SearchService};
use sqlx::{postgres::PgPoolOptions, Executor, PgPool};
use uuid::Uuid;

async fn setup() -> (PgPool, String) {
    let url = std::env::var("BUZZ_TEST_DATABASE_URL").expect("explicit test database URL");
    let schema = format!("workspace_fts_{}", Uuid::new_v4().simple());
    let admin = PgPoolOptions::new()
        .max_connections(1)
        .connect(&url)
        .await
        .unwrap();
    sqlx::query(sqlx::AssertSqlSafe(format!("CREATE SCHEMA {schema}")))
        .execute(&admin)
        .await
        .unwrap();
    admin.close().await;
    let pool = PgPoolOptions::new()
        .max_connections(2)
        .connect(&format!("{url}?options=-c%20search_path%3D{schema}"))
        .await
        .unwrap();
    pool.execute(
        "CREATE TABLE events (
        community_id UUID, id BYTEA, pubkey BYTEA, created_at TIMESTAMPTZ,
        kind INT, tags JSONB, content TEXT, sig BYTEA, channel_id UUID,
        deleted_at TIMESTAMPTZ, search_tsv TSVECTOR GENERATED ALWAYS AS
        (to_tsvector('simple', content)) STORED)",
    )
    .await
    .unwrap();
    (pool, schema)
}

async fn teardown(pool: PgPool, schema: &str) {
    pool.close().await;
    let url = std::env::var("BUZZ_TEST_DATABASE_URL").unwrap();
    let admin = PgPoolOptions::new()
        .max_connections(1)
        .connect(&url)
        .await
        .unwrap();
    sqlx::query(sqlx::AssertSqlSafe(format!("DROP SCHEMA {schema} CASCADE")))
        .execute(&admin)
        .await
        .unwrap();
    admin.close().await;
}

#[allow(clippy::too_many_arguments)]
async fn insert_event(
    pool: &PgPool,
    community: CommunityId,
    id: [u8; 32],
    pubkey: [u8; 32],
    kind: i32,
    content: &str,
    channel_id: Option<Uuid>,
    time: i64,
) {
    sqlx::query("INSERT INTO events (community_id, id, pubkey, created_at, kind, tags, content, sig, channel_id)
        VALUES ($1, $2, $3, to_timestamp($4), $5, '[]'::jsonb, $6, $7, $8)")
        .bind(community.as_uuid()).bind(id.as_slice()).bind(pubkey.as_slice())
        .bind(time).bind(kind).bind(content).bind(b"test".as_slice()).bind(channel_id)
        .execute(pool).await.unwrap();
}

fn rand_bytes32() -> [u8; 32] {
    let mut bytes = [0u8; 32];
    bytes[..16].copy_from_slice(Uuid::new_v4().as_bytes());
    bytes
}

#[tokio::test]
#[ignore = "requires Postgres"]
async fn agent_workspace_is_not_searchable_with_legacy_fts_column() {
    let (pool, schema) = setup().await;
    let community = CommunityId::from_uuid(Uuid::new_v4());
    let metadata_id = rand_bytes32();
    let message_id = rand_bytes32();
    let pubkey = rand_bytes32();
    insert_event(
        &pool,
        community,
        metadata_id,
        pubkey,
        buzz_core::kind::KIND_AGENT_WORKSPACE as i32,
        "sleeping",
        None,
        1700000001,
    )
    .await;
    insert_event(
        &pool,
        community,
        message_id,
        pubkey,
        9,
        "the worker is sleeping",
        None,
        1700000000,
    )
    .await;
    let indexed: bool = sqlx::query_scalar(
        "SELECT search_tsv @@ to_tsquery('simple', 'sleeping') FROM events WHERE id = $1",
    )
    .bind(metadata_id.as_slice())
    .fetch_one(&pool)
    .await
    .expect("legacy row indexed");
    assert!(
        indexed,
        "test must exercise a row the legacy expression indexes"
    );
    let service = SearchService::new(pool.clone());
    for mode in [
        buzz_search::SearchMode::FullText,
        buzz_search::SearchMode::Prefix,
    ] {
        let mut query = SearchQuery {
            community,
            q: "sleeping".into(),
            channel_scope: ChannelScope::Any,
            kinds: None,
            authors: None,
            since: None,
            until: None,
            page: 1,
            per_page: 1,
            mode,
        };
        let result = service.search(&query).await.expect("search");
        assert_eq!(result.hits.len(), 1);
        assert_eq!(
            result.hits[0].event_id, message_id,
            "metadata cannot starve message pagination"
        );
        query.kinds = Some(vec![buzz_core::kind::KIND_AGENT_WORKSPACE as i32]);
        assert!(service
            .search(&query)
            .await
            .expect("explicit kind search")
            .hits
            .is_empty());
    }
    teardown(pool, &schema).await;
}
