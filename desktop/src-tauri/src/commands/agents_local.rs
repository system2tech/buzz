//! Provision an independently admitted local manager around the managed-agent runtime.

use std::time::Duration;

use nostr::{EventBuilder, Keys, Kind, Tag, Timestamp};
use reqwest::Method;
use serde::Deserialize;
use tauri::{AppHandle, Manager};
use uuid::Uuid;

use crate::{
    app_state::AppState,
    events,
    managed_agents::{
        current_instance_id, load_managed_agents, process_is_running, save_managed_agents,
        stop_managed_agent_process, sync_managed_agent_processes, BackendKind,
        LocalAgentSetupRequest, ManagedAgentRecord, ManagedAgentRuntimeKey,
    },
    nostr_convert,
    relay::{
        build_nip98_auth_header_for_keys, classify_request_error, parse_json_response,
        query_relay_at_with_keys, relay_error_message, relay_http_base_url,
        relay_ws_url_with_override, submit_event_at_with_keys,
    },
};
use buzz_core_pkg::agent_workspace::{AgentLocation, AgentWorkspace, AgentWorkspaceState};

const COORDINATION_CHANNEL_NAME: &str = "agent-managers";
const INVITE_TTL_SECONDS: u64 = 600;
const INVITE_REQUEST_TIMEOUT: Duration = Duration::from_secs(15);
const WORKSPACE_LEASE_SECONDS: u64 = 300;
const WORKSPACE_REPORT_INTERVAL: Duration = Duration::from_secs(120);

#[derive(Debug, Deserialize)]
struct MintInviteResponse {
    code: String,
}

#[derive(Debug, Deserialize)]
struct ClaimInviteResponse {
    status: String,
    role: String,
}

#[derive(Debug, Clone)]
pub(super) struct LocalAgentPlan {
    pub channel_name: String,
    pub relay_ws_url: String,
    pub relay_api_base_url: String,
    pub owner_keys: Keys,
}

#[derive(Debug, Clone)]
pub(super) struct ProvisionedLocalAgent {
    pub channel_id: String,
    pub relay_ws_url: String,
    pub relay_api_base_url: String,
    pub owner_keys: Keys,
}

pub(super) struct LocalAgentCreation {
    pub plan: Option<LocalAgentPlan>,
    pub expected_relay_url: Option<String>,
    pub expected_signer_pubkey: Option<String>,
    pub auth_tag: Option<String>,
}

pub(super) fn prepare_local_agent_creation(
    state: &AppState,
    input: &crate::managed_agents::CreateManagedAgentRequest,
    agent_keys: &Keys,
) -> Result<LocalAgentCreation, String> {
    let plan = input
        .local_agent_setup
        .as_ref()
        .map(|setup| bind_local_agent_plan(state, setup))
        .transpose()?;
    let (expected_relay_url, expected_signer_pubkey) = input
        .local_agent_setup
        .as_ref()
        .map(|setup| {
            (
                Some(setup.expected_relay_url.clone()),
                Some(setup.expected_signer_pubkey.clone()),
            )
        })
        .unwrap_or((None, None));
    let auth_tag = if plan.is_some() {
        None
    } else {
        let owner_keys = state.signing_keys()?;
        let compat_owner = nostr::Keys::parse(&owner_keys.secret_key().to_secret_hex())
            .map_err(|error| format!("failed to bridge owner keys: {error}"))?;
        let compat_agent = nostr::PublicKey::from_hex(&agent_keys.public_key().to_hex())
            .map_err(|error| format!("failed to bridge agent pubkey: {error}"))?;
        Some(
            buzz_sdk_pkg::nip_oa::compute_auth_tag(&compat_owner, &compat_agent, "")
                .map_err(|error| format!("failed to compute NIP-OA auth tag: {error}"))?,
        )
    };
    Ok(LocalAgentCreation {
        plan,
        expected_relay_url,
        expected_signer_pubkey,
        auth_tag,
    })
}

pub(super) async fn provision_requested_local_agent(
    state: &AppState,
    creation: &mut LocalAgentCreation,
    agent_keys: &Keys,
    agent_name: &str,
) -> Result<Option<ProvisionedLocalAgent>, String> {
    match creation.plan.take() {
        Some(plan) => provision_local_agent(state, plan, agent_keys, agent_name)
            .await
            .map(Some),
        None => Ok(None),
    }
}

pub(super) fn profile_relay_url(
    state: &AppState,
    provisioned: Option<&ProvisionedLocalAgent>,
    resolved_relay_url: &str,
) -> String {
    provisioned
        .map(|agent| agent.relay_ws_url.clone())
        .unwrap_or_else(|| {
            crate::relay::effective_agent_relay_url(
                resolved_relay_url,
                &relay_ws_url_with_override(state),
            )
        })
}

pub(super) async fn merge_initial_workspace_status_error(
    state: &AppState,
    provisioned: Option<&ProvisionedLocalAgent>,
    agent_keys: &Keys,
    started: bool,
    profile_error: Option<String>,
) -> Option<String> {
    let Some(provisioned) = provisioned else {
        return profile_error;
    };
    let workspace_state = if started {
        AgentWorkspaceState::Awake
    } else {
        AgentWorkspaceState::Failed
    };
    match publish_workspace_status(
        state,
        &provisioned.relay_api_base_url,
        agent_keys,
        &provisioned.channel_id,
        workspace_state,
    )
    .await
    {
        Ok(()) => profile_error,
        Err(error) => Some(match profile_error {
            Some(profile_error) => format!("{profile_error}; workspace status: {error}"),
            None => format!("workspace status: {error}"),
        }),
    }
}

pub(super) fn validate_local_agent_request(
    input: &crate::managed_agents::CreateManagedAgentRequest,
) -> Result<(), String> {
    let Some(setup) = input.local_agent_setup.as_ref() else {
        return Ok(());
    };
    if crate::managed_agents::access_policy::owner_only() {
        return Err("independent local agents are unavailable in this build".to_string());
    }
    if input.backend != BackendKind::Local {
        return Err("local agent setup requires the local backend".to_string());
    }
    if input
        .persona_id
        .as_deref()
        .is_some_and(|value| !value.trim().is_empty())
        || input
            .team_id
            .as_deref()
            .is_some_and(|value| !value.trim().is_empty())
    {
        return Err("local agent setup cannot be linked to a persona or team".to_string());
    }
    if !input.spawn_after_create || !input.start_on_app_launch {
        return Err("local agents must start now and when Buzz opens".to_string());
    }
    if input.respond_to != Some(crate::managed_agents::RespondTo::Anyone)
        || !input.respond_to_allowlist.is_empty()
    {
        return Err("local agents must respond to workspace members".to_string());
    }
    let channel_name = buzz_core_pkg::channel::canonical_channel_name(&setup.channel_name);
    crate::managed_agents::validate_visible_text(channel_name, "Channel name", false)?;
    if channel_name.is_empty() {
        return Err("channel name is required".to_string());
    }
    if setup.expected_relay_url.trim().is_empty() || setup.expected_signer_pubkey.trim().is_empty()
    {
        return Err("active Buzz workspace is required".to_string());
    }
    let pinned_relay = input
        .relay_url
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "local agent setup must pin its relay".to_string())?;
    let pinned = buzz_core_pkg::relay::normalize_relay_url(pinned_relay)
        .map_err(|error| error.to_string())?;
    let expected = buzz_core_pkg::relay::normalize_relay_url(&setup.expected_relay_url)
        .map_err(|error| error.to_string())?;
    if pinned != expected {
        return Err("local agent relay does not match the active workspace".to_string());
    }
    Ok(())
}

pub(super) fn bind_local_agent_plan(
    state: &AppState,
    setup: &LocalAgentSetupRequest,
) -> Result<LocalAgentPlan, String> {
    let workspace_relay = crate::relay::bind_expected_relay_scope(
        Some(&setup.expected_relay_url),
        relay_ws_url_with_override(state),
    )?;
    let owner_keys = state.signing_keys()?;
    crate::relay::assert_expected_signer(
        Some(&setup.expected_signer_pubkey),
        &owner_keys.public_key().to_hex(),
    )?;
    let relay_ws_url = workspace_relay.as_str().to_string();
    let relay_api_base_url = relay_http_base_url(&relay_ws_url);
    Ok(LocalAgentPlan {
        channel_name: buzz_core_pkg::channel::canonical_channel_name(&setup.channel_name)
            .to_string(),
        relay_ws_url,
        relay_api_base_url,
        owner_keys,
    })
}

async fn post_json_with_keys<T: for<'de> Deserialize<'de>>(
    state: &AppState,
    api_base_url: &str,
    path: &str,
    keys: &Keys,
    body: serde_json::Value,
) -> Result<T, String> {
    crate::relay_admission::wait_for_rate_limit().await;
    let url = format!("{}{}", api_base_url.trim_end_matches('/'), path);
    let body = serde_json::to_vec(&body)
        .map_err(|error| format!("invite request serialization failed: {error}"))?;
    let auth = build_nip98_auth_header_for_keys(keys, &Method::POST, &url, &body)?;
    let response = state
        .http_client
        .post(url)
        .header("Authorization", auth)
        .header("Content-Type", "application/json")
        .timeout(INVITE_REQUEST_TIMEOUT)
        .body(body)
        .send()
        .await
        .map_err(|error| classify_request_error(&error))?;
    if !response.status().is_success() {
        return Err(relay_error_message(response).await);
    }
    parse_json_response(response).await
}

fn exact_coordination_channel(events: &[nostr::Event]) -> Result<Uuid, String> {
    let matches = events
        .iter()
        .filter_map(|event| nostr_convert::channel_info_from_event(event, None, None).ok())
        .filter(|channel| {
            channel.name.eq_ignore_ascii_case(COORDINATION_CHANNEL_NAME)
                && channel.visibility == "open"
                && channel.channel_type == "stream"
                && channel.archived_at.is_none()
        })
        .filter_map(|channel| Uuid::parse_str(&channel.id).ok())
        .collect::<Vec<_>>();
    if matches.len() != 1 {
        return Err(format!(
            "required active open #{COORDINATION_CHANNEL_NAME} channel must exist exactly once; found {}",
            matches.len()
        ));
    }
    Ok(matches[0])
}

fn workspace_builder(
    channel_id: &str,
    agent_pubkey: &str,
    state: AgentWorkspaceState,
) -> Result<EventBuilder, String> {
    let now = Timestamp::now();
    let workspace = AgentWorkspace {
        version: 1,
        manager_channel_id: channel_id.to_string(),
        agent_pubkey: agent_pubkey.to_string(),
        location: AgentLocation::Local,
        state,
        valid_until: now.as_secs() + WORKSPACE_LEASE_SECONDS,
    };
    let content = serde_json::to_string(&workspace)
        .map_err(|error| format!("workspace status serialization failed: {error}"))?;
    let d_tag = Tag::parse(["d", channel_id])
        .map_err(|error| format!("workspace channel tag failed: {error}"))?;
    let h_tag = Tag::parse(["h", channel_id])
        .map_err(|error| format!("workspace scope tag failed: {error}"))?;
    Ok(EventBuilder::new(
        Kind::Custom(buzz_core_pkg::kind::KIND_AGENT_WORKSPACE as u16),
        content,
    )
    .tags([d_tag, h_tag])
    .custom_created_at(now))
}

pub(super) async fn publish_workspace_status(
    state: &AppState,
    api_base_url: &str,
    agent_keys: &Keys,
    channel_id: &str,
    workspace_state: AgentWorkspaceState,
) -> Result<(), String> {
    let builder = workspace_builder(
        channel_id,
        &agent_keys.public_key().to_hex(),
        workspace_state,
    )?;
    submit_event_at_with_keys(builder, state, api_base_url, agent_keys).await?;
    Ok(())
}

async fn best_effort_cleanup(
    state: &AppState,
    plan: &LocalAgentPlan,
    agent_keys: &Keys,
    channel_id: Option<Uuid>,
) {
    if let Some(channel_id) = channel_id {
        if let Ok(builder) = events::build_delete_channel(channel_id) {
            let _ = submit_event_at_with_keys(builder, state, &plan.relay_api_base_url, agent_keys)
                .await;
        }
        state.clear_pending_owned_channel(
            &plan.owner_keys.public_key().to_hex(),
            &channel_id.to_string(),
        );
    }
    if let Ok(builder) = events::build_relay_admin_remove(&agent_keys.public_key().to_hex()) {
        let _ =
            submit_event_at_with_keys(builder, state, &plan.relay_api_base_url, &plan.owner_keys)
                .await;
    }
}

pub(super) async fn provision_local_agent(
    state: &AppState,
    plan: LocalAgentPlan,
    agent_keys: &Keys,
    agent_name: &str,
) -> Result<ProvisionedLocalAgent, String> {
    // Resolve the shared coordination room before consuming an invite or
    // creating a channel, so a broken workspace fails with no residue.
    let directory = query_relay_at_with_keys(
        state,
        &plan.relay_api_base_url,
        &[serde_json::json!({ "kinds": [39000], "limit": 500 })],
        &plan.owner_keys,
        None,
    )
    .await?;
    let coordination_channel = exact_coordination_channel(&directory)?;

    let minted: MintInviteResponse = post_json_with_keys(
        state,
        &plan.relay_api_base_url,
        "/api/invites",
        &plan.owner_keys,
        serde_json::json!({ "ttl_secs": INVITE_TTL_SECONDS, "max_uses": 1 }),
    )
    .await?;
    let claimed: ClaimInviteResponse = match post_json_with_keys(
        state,
        &plan.relay_api_base_url,
        "/api/invites/claim",
        agent_keys,
        serde_json::json!({ "code": minted.code }),
    )
    .await
    {
        Ok(claimed) => claimed,
        Err(error) => {
            // The relay may have committed the claim before the response was
            // lost. Removing an absent member is harmless; leaving a hidden
            // orphan is not.
            best_effort_cleanup(state, &plan, agent_keys, None).await;
            return Err(error);
        }
    };
    if !matches!(claimed.status.as_str(), "joined" | "already_member") || claimed.role != "member" {
        best_effort_cleanup(state, &plan, agent_keys, None).await;
        return Err("relay returned an invalid invite-claim response".to_string());
    }

    let channel_id = Uuid::new_v4();
    let setup_result = async {
        submit_event_at_with_keys(
            events::build_create_channel(
                channel_id,
                &plan.channel_name,
                "private",
                "stream",
                Some(&format!("Conversation with {agent_name}")),
                None,
            )?,
            state,
            &plan.relay_api_base_url,
            agent_keys,
        )
        .await?;
        submit_event_at_with_keys(
            events::build_add_member(
                channel_id,
                &plan.owner_keys.public_key().to_hex(),
                Some("owner"),
            )?,
            state,
            &plan.relay_api_base_url,
            agent_keys,
        )
        .await?;
        state.mark_pending_owned_channel(
            &plan.owner_keys.public_key().to_hex(),
            &channel_id.to_string(),
        );
        submit_event_at_with_keys(
            events::build_join(coordination_channel)?,
            state,
            &plan.relay_api_base_url,
            agent_keys,
        )
        .await?;
        publish_workspace_status(
            state,
            &plan.relay_api_base_url,
            agent_keys,
            &channel_id.to_string(),
            AgentWorkspaceState::Starting,
        )
        .await
    }
    .await;

    if let Err(error) = setup_result {
        best_effort_cleanup(state, &plan, agent_keys, Some(channel_id)).await;
        return Err(format!("local agent setup failed: {error}"));
    }

    Ok(ProvisionedLocalAgent {
        channel_id: channel_id.to_string(),
        relay_ws_url: plan.relay_ws_url,
        relay_api_base_url: plan.relay_api_base_url,
        owner_keys: plan.owner_keys,
    })
}

pub(super) async fn rollback_provisioned_local_agent(
    state: &AppState,
    provisioned: &ProvisionedLocalAgent,
    agent_keys: &Keys,
) {
    let plan = LocalAgentPlan {
        channel_name: String::new(),
        relay_ws_url: provisioned.relay_ws_url.clone(),
        relay_api_base_url: provisioned.relay_api_base_url.clone(),
        owner_keys: provisioned.owner_keys.clone(),
    };
    best_effort_cleanup(
        state,
        &plan,
        agent_keys,
        Uuid::parse_str(&provisioned.channel_id).ok(),
    )
    .await;
}

/// Stop a direct local manager before its relay identity is removed. The
/// record stays on disk until relay cleanup succeeds, so a failed network or
/// authorization step remains retryable from the normal delete action.
pub(super) fn prepare_local_agent_deprovision(
    app: &AppHandle,
    pubkey: &str,
) -> Result<Option<ManagedAgentRecord>, String> {
    let state = app.state::<AppState>();
    let _store_guard = state
        .managed_agents_store_lock
        .lock()
        .map_err(|error| error.to_string())?;
    let mut records = load_managed_agents(app)?;
    let mut runtimes = state
        .managed_agent_processes
        .lock()
        .map_err(|error| error.to_string())?;
    let (sync_changed, exited_pubkeys) =
        sync_managed_agent_processes(&mut records, &mut runtimes, &current_instance_id(app));
    for exited_pubkey in &exited_pubkeys {
        state.clear_agent_session_caches(exited_pubkey);
    }

    let Some(index) = records.iter().position(|record| record.pubkey == pubkey) else {
        return Err(format!("agent {pubkey} not found"));
    };
    if records[index].manager_channel_id.is_none() {
        if sync_changed {
            save_managed_agents(app, &records)?;
        }
        return Ok(None);
    }
    if records[index].backend != BackendKind::Local {
        return Err("independent manager cleanup requires the local backend".to_string());
    }

    stop_managed_agent_process(app, &mut records[index], &mut runtimes)?;
    let record = records[index].clone();
    save_managed_agents(app, &records)?;
    state.clear_agent_session_caches(pubkey);
    Ok(Some(record))
}

fn relay_members_include(events: &[nostr::Event], pubkey: &str) -> Result<bool, String> {
    let snapshot = events
        .first()
        .ok_or_else(|| "relay member directory is unavailable".to_string())?;
    Ok(nostr_convert::relay_members_from_event(snapshot)
        .get("members")
        .and_then(serde_json::Value::as_array)
        .is_some_and(|members| {
            members.iter().any(|member| {
                member.get("pubkey").and_then(serde_json::Value::as_str) == Some(pubkey)
            })
        }))
}

/// Delete the manager conversation and remove its independent relay member.
/// The membership snapshot makes retries safe when a previous request reached
/// the relay but its response did not reach Desktop.
pub(super) async fn deprovision_local_agent(
    state: &AppState,
    record: &ManagedAgentRecord,
) -> Result<(), String> {
    let channel_id = record
        .manager_channel_id
        .as_deref()
        .ok_or_else(|| "local manager channel is missing".to_string())?;
    let channel_uuid = Uuid::parse_str(channel_id)
        .map_err(|error| format!("invalid local manager channel: {error}"))?;
    let relay_ws = crate::relay::bind_expected_relay_scope(
        Some(&record.relay_url),
        relay_ws_url_with_override(state),
    )?;
    let api_base_url = relay_http_base_url(relay_ws.as_str());
    let owner_keys = state.signing_keys()?;
    let agent_keys = Keys::parse(&record.private_key_nsec)
        .map_err(|error| format!("invalid local manager identity: {error}"))?;
    let agent_pubkey = agent_keys.public_key().to_hex();
    let member_events = query_relay_at_with_keys(
        state,
        &api_base_url,
        &[serde_json::json!({ "kinds": [13534], "limit": 1 })],
        &owner_keys,
        None,
    )
    .await?;
    let is_member = relay_members_include(&member_events, &agent_pubkey)?;

    let channel_signer = if is_member { &agent_keys } else { &owner_keys };
    if let Err(error) = submit_event_at_with_keys(
        events::build_delete_channel(channel_uuid)?,
        state,
        &api_base_url,
        channel_signer,
    )
    .await
    {
        if !error.contains("channel not found") && !error.contains("already deleted") {
            return Err(format!(
                "could not delete the manager conversation: {error}"
            ));
        }
    }
    state.clear_pending_owned_channel(&owner_keys.public_key().to_hex(), channel_id);

    if is_member {
        submit_event_at_with_keys(
            events::build_relay_admin_remove(&agent_pubkey)?,
            state,
            &api_base_url,
            &owner_keys,
        )
        .await
        .map_err(|error| format!("could not remove the manager from the workspace: {error}"))?;
    }
    Ok(())
}

pub(super) async fn deprovision_before_delete(app: &AppHandle, pubkey: &str) -> Result<(), String> {
    let record = {
        let app = app.clone();
        let pubkey = pubkey.to_string();
        tokio::task::spawn_blocking(move || prepare_local_agent_deprovision(&app, &pubkey))
            .await
            .map_err(|error| format!("spawn_blocking failed: {error}"))??
    };
    if let Some(record) = record {
        deprovision_local_agent(&app.state::<AppState>(), &record).await?;
    }
    Ok(())
}

pub(super) fn is_direct_local_manager(records: &[ManagedAgentRecord], pubkey: &str) -> bool {
    records
        .iter()
        .find(|record| record.pubkey == pubkey)
        .is_some_and(|record| record.manager_channel_id.is_some())
}

pub(super) fn tombstone_after_delete(
    app: &AppHandle,
    state: &AppState,
    pubkey: &str,
    direct_local_manager: bool,
) {
    if !direct_local_manager {
        super::tombstone_managed_agent_pending(app, state, pubkey);
    }
}

fn workspace_state(record: &ManagedAgentRecord, running: bool) -> AgentWorkspaceState {
    if running {
        AgentWorkspaceState::Awake
    } else if record.last_error.is_some() || record.last_exit_code.is_some_and(|code| code != 0) {
        AgentWorkspaceState::Failed
    } else {
        AgentWorkspaceState::Sleeping
    }
}

async fn refresh_local_agent_statuses(app: &AppHandle) -> Result<(), String> {
    let state = app.state::<AppState>();
    let snapshots = {
        let _store_guard = state
            .managed_agents_store_lock
            .lock()
            .map_err(|error| error.to_string())?;
        let records = load_managed_agents(app)?;
        let runtimes = state
            .managed_agent_processes
            .lock()
            .map_err(|error| error.to_string())?;
        records
            .into_iter()
            .filter_map(|record| {
                let channel_id = record.manager_channel_id.clone()?;
                let relay_ws = buzz_core_pkg::relay::normalize_relay_url(&record.relay_url).ok()?;
                let running = ManagedAgentRuntimeKey::new(record.pubkey.clone(), &relay_ws)
                    .ok()
                    .is_some_and(|key| runtimes.contains_key(&key))
                    || record.runtime_pid.is_some_and(process_is_running);
                let keys = Keys::parse(&record.private_key_nsec).ok()?;
                Some((
                    keys,
                    relay_http_base_url(&relay_ws),
                    channel_id,
                    workspace_state(&record, running),
                ))
            })
            .collect::<Vec<_>>()
    };
    for (keys, api_base_url, channel_id, workspace_state) in snapshots {
        if let Err(error) =
            publish_workspace_status(&state, &api_base_url, &keys, &channel_id, workspace_state)
                .await
        {
            eprintln!(
                "buzz-desktop: local agent workspace status failed for {}: {error}",
                keys.public_key().to_hex()
            );
        }
    }
    Ok(())
}

/// Keep the manager grouping and lifecycle state fresh while Desktop owns the
/// local process. When Desktop exits, the lease naturally becomes unknown.
pub(crate) async fn run_local_agent_reporter(app: AppHandle) {
    tokio::time::sleep(Duration::from_secs(10)).await;
    loop {
        if let Err(error) = refresh_local_agent_statuses(&app).await {
            eprintln!("buzz-desktop: local agent reporter: {error}");
        }
        tokio::time::sleep(WORKSPACE_REPORT_INTERVAL).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn local_agent_request(channel_name: &str) -> crate::managed_agents::CreateManagedAgentRequest {
        serde_json::from_value(serde_json::json!({
            "name": "Daily coordinator",
            "relayUrl": "wss://buzz.example.com",
            "acpCommand": "buzz-acp",
            "agentCommand": "claude",
            "mcpCommand": null,
            "turnTimeoutSeconds": null,
            "idleTimeoutSeconds": null,
            "maxTurnDurationSeconds": null,
            "parallelism": null,
            "systemPrompt": "Coordinate daily updates.",
            "avatarUrl": null,
            "model": null,
            "provider": null,
            "spawnAfterCreate": true,
            "startOnAppLaunch": true,
            "backend": { "type": "local" },
            "respondTo": "anyone",
            "localAgentSetup": {
                "channelName": channel_name,
                "expectedRelayUrl": "wss://buzz.example.com",
                "expectedSignerPubkey": "11".repeat(32),
            },
        }))
        .unwrap()
    }

    #[test]
    fn local_agent_request_requires_a_channel_and_managed_lifecycle() {
        let mut request = local_agent_request("daily-coordinator");
        if crate::managed_agents::access_policy::owner_only() {
            assert!(validate_local_agent_request(&request).is_err());
        } else {
            assert!(validate_local_agent_request(&request).is_ok());
        }

        request.local_agent_setup.as_mut().unwrap().channel_name = "  ".into();
        assert!(validate_local_agent_request(&request).is_err());

        let mut request = local_agent_request("daily-coordinator");
        request.start_on_app_launch = false;
        assert!(validate_local_agent_request(&request).is_err());

        let mut request = local_agent_request("daily-coordinator");
        request.respond_to = Some(crate::managed_agents::RespondTo::OwnerOnly);
        assert!(validate_local_agent_request(&request).is_err());
    }

    #[test]
    fn coordination_channel_must_be_one_open_stream() {
        let keys = Keys::generate();
        let channel_id = Uuid::new_v4().to_string();
        let event = EventBuilder::new(Kind::Custom(39000), "")
            .tags([
                Tag::parse(["d", channel_id.as_str()]).unwrap(),
                Tag::parse(["name", COORDINATION_CHANNEL_NAME]).unwrap(),
                Tag::parse(["t", "stream"]).unwrap(),
                Tag::parse(["public"]).unwrap(),
            ])
            .sign_with_keys(&keys)
            .unwrap();
        assert!(exact_coordination_channel(std::slice::from_ref(&event)).is_ok());
        assert!(exact_coordination_channel(&[]).is_err());
        assert!(exact_coordination_channel(&[event.clone(), event]).is_err());
    }

    #[test]
    fn manager_workspace_event_uses_direct_identity_and_local_location() {
        let keys = Keys::generate();
        let channel = Uuid::new_v4().to_string();
        let event = workspace_builder(
            &channel,
            &keys.public_key().to_hex(),
            AgentWorkspaceState::Awake,
        )
        .unwrap()
        .sign_with_keys(&keys)
        .unwrap();
        let workspace: AgentWorkspace = serde_json::from_str(&event.content).unwrap();
        assert_eq!(workspace.manager_channel_id, channel);
        assert_eq!(workspace.location, AgentLocation::Local);
        assert_eq!(workspace.state, AgentWorkspaceState::Awake);
        assert_eq!(event.pubkey, keys.public_key());
    }

    #[test]
    fn relay_member_snapshot_supports_idempotent_cleanup() {
        let relay_keys = Keys::generate();
        let member_keys = Keys::generate();
        let member_pubkey = member_keys.public_key().to_hex();
        let event = EventBuilder::new(Kind::Custom(13534), "")
            .tags([Tag::parse(["member", member_pubkey.as_str(), "member"]).unwrap()])
            .sign_with_keys(&relay_keys)
            .unwrap();
        assert!(relay_members_include(&[event], &member_pubkey).unwrap());
        assert!(relay_members_include(&[], &member_pubkey).is_err());
    }
}
