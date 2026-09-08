mod channel_observer_access {
    use super::*;
    use buzz_core::observer::OBSERVER_CHANNEL_TAG;
    use buzz_core::{CommunityId, StoredEvent};

    async fn publish_accepted(
        state: Arc<crate::state::AppState>,
        event: nostr::Event,
        community: CommunityId,
    ) -> bool {
        let (tx, mut rx) = mpsc::channel(8);
        let (ctrl, _) = mpsc::channel(8);
        let conn = Arc::new(crate::connection::ConnectionState {
            conn_id: Uuid::new_v4(),
            tenant: buzz_core::TenantContext::resolved(community, "observer-test.invalid"),
            remote_addr: "127.0.0.1:12345".parse().expect("address"),
            auth_state: RwLock::new(crate::connection::AuthState::Authenticated(
                buzz_auth::AuthContext {
                    pubkey: event.pubkey,
                    scopes: vec![],
                    channel_ids: None,
                    auth_method: buzz_auth::AuthMethod::Nip42,
                    agent_owner_pubkey: None,
                },
            )),
            subscriptions: Arc::new(Mutex::new(HashMap::new())),
            send_tx: tx,
            ctrl_tx: ctrl,
            cancel: CancellationToken::new(),
            backpressure_count: Arc::new(AtomicU8::new(0)),
            grace_limit: 3,
        });
        let id = event.id.to_hex();
        super::super::handle_agent_observer_event(event, conn.conn_id, &id, conn, state).await;
        let axum::extract::ws::Message::Text(text) = rx.recv().await.expect("ack") else {
            panic!("text ack");
        };
        let ack: serde_json::Value = serde_json::from_str(&text).expect("ack JSON");
        assert_eq!(ack[0], "OK");
        ack[2].as_bool().expect("accepted bool")
    }

    // Deliberately requires real infrastructure; never reports success by skipping.
    #[tokio::test]
    #[ignore = "requires local Postgres and Redis"]
    async fn channel_observer_membership_revocation_and_tenant_boundary() {
        let (state, shutdown, pool) = super::fanout_access::audit_state()
            .await
            .expect("local Postgres and Redis required");
        let community_uuid = Uuid::new_v4();
        let community = CommunityId::from_uuid(community_uuid);
        let channel = Uuid::new_v4();
        let agent = Keys::generate();
        let member = Keys::generate();
        let outsider = Keys::generate();
        sqlx::query("INSERT INTO communities (id, host) VALUES ($1,$2)")
            .bind(community_uuid)
            .bind(format!("observer-{}.invalid", community_uuid))
            .execute(&pool)
            .await
            .expect("community");
        sqlx::query("INSERT INTO channels (community_id,id,name,created_by,visibility) VALUES ($1,$2,'observer-test',$3,'open')")
            .bind(community_uuid).bind(channel).bind(agent.public_key().to_bytes().to_vec())
            .execute(&pool).await.expect("channel");
        for key in [&agent, &member] {
            sqlx::query(
                "INSERT INTO channel_members (community_id,channel_id,pubkey) VALUES ($1,$2,$3)",
            )
            .bind(community_uuid)
            .bind(channel)
            .bind(key.public_key().to_bytes().to_vec())
            .execute(&pool)
            .await
            .expect("member");
        }
        let event = EventBuilder::new(
            Kind::Custom(KIND_AGENT_OBSERVER_FRAME as u16),
            encrypt_observer_payload(
                &agent,
                &member.public_key(),
                &serde_json::json!({
                "kind":"turn_started", "channelId":channel.to_string()}),
            )
            .expect("encrypted"),
        )
        .tags([
            Tag::public_key(member.public_key()),
            Tag::parse([OBSERVER_AGENT_TAG, &agent.public_key().to_hex()]).expect("agent"),
            Tag::parse([OBSERVER_FRAME_TAG, OBSERVER_FRAME_TELEMETRY]).expect("frame"),
            Tag::parse([OBSERVER_CHANNEL_TAG, &channel.to_string()]).expect("channel"),
        ])
        .sign_with_keys(&agent)
        .expect("signed");
        assert!(
            publish_accepted(state.clone(), event.clone(), community).await,
            "channel members do not require owner mapping"
        );
        let stored = StoredEvent::new(event.clone(), None);
        let mut connections = Vec::new();
        let mut receivers = Vec::new();
        for key in [&member, &outsider] {
            let id = Uuid::new_v4();
            let (tx, rx) = mpsc::channel(8);
            let (ctrl, _) = mpsc::channel(8);
            state.conn_manager.register(
                id,
                tx,
                ctrl,
                None,
                CancellationToken::new(),
                community,
                Arc::new(AtomicU8::new(0)),
                Arc::new(Mutex::new(HashMap::new())),
                3,
            );
            state
                .conn_manager
                .set_authenticated_pubkey(id, key.public_key().to_bytes().to_vec());
            connections.push((id, "observer".to_owned()));
            receivers.push(rx);
        }
        let out = super::super::filter_fanout_by_access(
            &state,
            community,
            &stored,
            connections.clone(),
            None,
        )
        .await;
        assert_eq!(
            out,
            vec![connections[0].clone()],
            "only addressed member receives, even on an open channel"
        );
        let elsewhere = CommunityId::from_uuid(Uuid::new_v4());
        assert!(super::super::filter_fanout_by_access(
            &state,
            elsewhere,
            &stored,
            connections.clone(),
            None
        )
        .await
        .is_empty());
        sqlx::query("UPDATE channel_members SET removed_at=NOW() WHERE community_id=$1 AND channel_id=$2 AND pubkey=$3")
            .bind(community_uuid).bind(channel).bind(member.public_key().to_bytes().to_vec())
            .execute(&pool).await.expect("remove viewer");
        assert!(
            !publish_accepted(state.clone(), event.clone(), community).await,
            "removed viewer cannot receive a new publication"
        );
        assert!(
            super::super::filter_fanout_by_access(
                &state,
                community,
                &stored,
                connections.clone(),
                None
            )
            .await
            .is_empty(),
            "existing subscriptions cannot keep receiving after removal"
        );
        sqlx::query(
            "UPDATE channel_members SET removed_at=NULL WHERE community_id=$1 AND channel_id=$2",
        )
        .bind(community_uuid)
        .bind(channel)
        .execute(&pool)
        .await
        .expect("rejoin");
        assert_eq!(
            super::super::filter_fanout_by_access(
                &state,
                community,
                &stored,
                connections.clone(),
                None
            )
            .await
            .len(),
            1
        );
        sqlx::query("UPDATE channel_members SET removed_at=NOW() WHERE community_id=$1 AND channel_id=$2 AND pubkey=$3")
            .bind(community_uuid).bind(channel).bind(agent.public_key().to_bytes().to_vec())
            .execute(&pool).await.expect("remove worker");
        assert!(super::super::filter_fanout_by_access(
            &state,
            community,
            &stored,
            connections,
            None
        )
        .await
        .is_empty());
        shutdown.drain(std::time::Duration::from_secs(5)).await;
        sqlx::query("DELETE FROM channels WHERE community_id=$1")
            .bind(community_uuid)
            .execute(&pool)
            .await
            .expect("cleanup channels");
        sqlx::query("DELETE FROM communities WHERE id=$1")
            .bind(community_uuid)
            .execute(&pool)
            .await
            .expect("cleanup community");
    }
}
