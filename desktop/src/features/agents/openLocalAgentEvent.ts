const OPEN_LOCAL_AGENT_EVENT = "buzz:open-local-agent";

let pending = false;

export function requestOpenLocalAgent() {
  pending = true;
  window.dispatchEvent(new Event(OPEN_LOCAL_AGENT_EVENT));
}

export function consumePendingOpenLocalAgent() {
  const wasPending = pending;
  pending = false;
  return wasPending;
}

export function subscribeOpenLocalAgent(handler: () => void) {
  const listener = () => {
    pending = false;
    handler();
  };
  window.addEventListener(OPEN_LOCAL_AGENT_EVENT, listener);
  return () => window.removeEventListener(OPEN_LOCAL_AGENT_EVENT, listener);
}
