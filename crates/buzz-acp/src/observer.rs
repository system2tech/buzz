//! In-process observer bus for ACP session activity.
//!
//! This is intentionally process-local infrastructure: it lets the harness
//! collect raw ACP JSON-RPC activity and publish owner-scoped encrypted relay
//! frames without exposing a local HTTP port.

use std::{
    collections::{HashSet, VecDeque},
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc, Mutex,
    },
};

use serde::Serialize;
use tokio::sync::broadcast;

const OBSERVER_BUFFER_CAP: usize = 1_000;

/// Best-effort metadata attached to observer events.
#[derive(Clone, Debug, Default)]
pub struct ObserverContext {
    /// Buzz channel UUID for the current turn, when channel-scoped.
    pub channel_id: Option<String>,
    /// ACP session ID associated with the current turn, once known.
    pub session_id: Option<String>,
    /// Local UUID for one prompt turn.
    pub turn_id: Option<String>,
    /// RFC3339 timestamp at which the current turn began, when known.
    pub started_at: Option<String>,
}

/// Handle used by the harness to publish local observer events.
#[derive(Clone)]
pub struct ObserverHandle {
    inner: Arc<ObserverInner>,
}

struct ObserverInner {
    tx: broadcast::Sender<ObserverEvent>,
    buffer: Mutex<VecDeque<ObserverEvent>>,
    seq: AtomicU64,
    /// Sessions whose history is being redrawn right now — see [`ObserverHandle::begin_replay`].
    replaying: Mutex<HashSet<String>>,
}

fn new_observer_handle() -> ObserverHandle {
    let (tx, _) = broadcast::channel(OBSERVER_BUFFER_CAP);
    ObserverHandle {
        inner: Arc::new(ObserverInner {
            tx,
            buffer: Mutex::new(VecDeque::with_capacity(OBSERVER_BUFFER_CAP)),
            seq: AtomicU64::new(1),
            replaying: Mutex::new(HashSet::new()),
        }),
    }
}

/// Suppresses observer events for one session until dropped.
///
/// Restoring a session makes the agent re-emit its entire history as ACP updates —
/// `session/load` awaits `replaySessionHistory` before it answers, so every one of
/// those updates arrives before the load returns. That redraw is what an editor wants:
/// it repaints the conversation into a local view, instantly and for free.
///
/// We are not an editor. Observer events leave this process as individually signed
/// relay frames, paced at one per second, and nothing stores them — so a redraw does
/// not repaint anything. It broadcasts hundreds of yesterday's actions as if they were
/// happening now, and the live activity panel runs minutes behind reality. That is the
/// measured failure that got session resume reverted from this fork in August (see
/// S2-CHANGES.md, "Session resume, and why we stopped"): *"activity view minutes
/// behind — hundreds of replayed frames through a publisher paced at 1/sec"*.
///
/// The gate is deliberately NOT a marker on the wire. The adapter does not tag
/// replayed updates (`isReplay` is about steered-message echoes, not history), and it
/// does not need to: buzz-acp is the side that *asks* for the redraw, so the interval
/// between sending `session/load` and receiving its result is a redraw by
/// construction. Hold this guard across that call.
///
/// Scoped per session rather than globally so that restoring one channel does not
/// blind the panel for every other channel this process is serving.
#[must_use = "dropping the guard immediately re-enables observer frames"]
pub struct ReplayGuard {
    inner: Arc<ObserverInner>,
    session_id: String,
}

impl Drop for ReplayGuard {
    fn drop(&mut self) {
        if let Ok(mut replaying) = self.inner.replaying.lock() {
            replaying.remove(&self.session_id);
        }
    }
}

/// Event delivered through the in-process observer bus.
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ObserverEvent {
    /// Monotonic process-local sequence number.
    pub seq: u64,
    /// RFC3339 UTC timestamp.
    pub timestamp: String,
    /// Observer event kind, for example `acp_read` or `turn_started`.
    pub kind: String,
    /// Pool slot index for the agent process that emitted the event.
    pub agent_index: Option<usize>,
    /// Buzz channel UUID for channel-scoped events.
    pub channel_id: Option<String>,
    /// ACP session ID when known.
    pub session_id: Option<String>,
    /// Local UUID for one prompt turn.
    pub turn_id: Option<String>,
    /// RFC3339 timestamp at which the current turn began, when known.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub started_at: Option<String>,
    /// Raw or semantic event payload.
    pub payload: serde_json::Value,
}

impl ObserverHandle {
    /// Create an in-process observer feed.
    pub fn in_process() -> Self {
        new_observer_handle()
    }

    /// Subscribe to live observer events.
    pub fn subscribe(&self) -> broadcast::Receiver<ObserverEvent> {
        self.inner.tx.subscribe()
    }

    /// Return the current replay buffer.
    pub fn snapshot(&self) -> Vec<ObserverEvent> {
        match self.inner.buffer.lock() {
            Ok(buffer) => buffer.iter().cloned().collect(),
            Err(error) => {
                tracing::warn!(target: "observer", "observer replay buffer lock poisoned: {error}");
                Vec::new()
            }
        }
    }

    /// Suppress observer events for `session_id` until the returned guard drops.
    ///
    /// Wrap a `session/load` call in this. See [`ReplayGuard`] for why, and for why
    /// the window is the request/response interval rather than a wire marker.
    pub fn begin_replay(&self, session_id: &str) -> ReplayGuard {
        if let Ok(mut replaying) = self.inner.replaying.lock() {
            replaying.insert(session_id.to_string());
        }
        ReplayGuard {
            inner: Arc::clone(&self.inner),
            session_id: session_id.to_string(),
        }
    }

    /// Whether events for this session are currently being suppressed.
    fn is_replaying(&self, session_id: Option<&str>) -> bool {
        let Some(session_id) = session_id else {
            return false;
        };
        self.inner
            .replaying
            .lock()
            .map(|replaying| replaying.contains(session_id))
            .unwrap_or(false)
    }

    /// Emit a local observer event.
    pub fn emit(
        &self,
        kind: impl Into<String>,
        agent_index: Option<usize>,
        context: &ObserverContext,
        payload: serde_json::Value,
    ) {
        // Dropped at the bus entrance, not at the relay publisher: a redraw of a long
        // session is hundreds of events, and anything let into the paced queue still
        // costs a second each to drain even if it is discarded later.
        if self.is_replaying(context.session_id.as_deref()) {
            return;
        }
        let event = ObserverEvent {
            seq: self.inner.seq.fetch_add(1, Ordering::Relaxed),
            timestamp: chrono::Utc::now().to_rfc3339(),
            kind: kind.into(),
            agent_index,
            channel_id: context.channel_id.clone(),
            session_id: context.session_id.clone(),
            turn_id: context.turn_id.clone(),
            started_at: context.started_at.clone(),
            payload,
        };

        match self.inner.buffer.lock() {
            Ok(mut buffer) => {
                if buffer.len() >= OBSERVER_BUFFER_CAP {
                    buffer.pop_front();
                }
                buffer.push_back(event.clone());
            }
            Err(error) => {
                tracing::warn!(target: "observer", "observer replay buffer lock poisoned: {error}");
            }
        }

        let _ = self.inner.tx.send(event);
    }
}

/// Build observer context values from optional channel/session/turn IDs.
pub fn context_for(
    channel_id: Option<uuid::Uuid>,
    session_id: Option<String>,
    turn_id: Option<String>,
) -> ObserverContext {
    ObserverContext {
        channel_id: channel_id.map(|id| id.to_string()),
        session_id,
        turn_id,
        started_at: None,
    }
}

/// Attach the authoritative start timestamp to every observer frame for a turn.
pub fn context_for_turn(
    channel_id: Option<uuid::Uuid>,
    session_id: Option<String>,
    turn_id: String,
    started_at: String,
) -> ObserverContext {
    ObserverContext {
        channel_id: channel_id.map(|id| id.to_string()),
        session_id,
        turn_id: Some(turn_id),
        started_at: Some(started_at),
    }
}
