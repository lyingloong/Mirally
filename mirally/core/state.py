"""Product lifecycle vocabulary, without presentation copy or engine names."""

TERMINAL_STATUSES = frozenset({"completed", "partial", "incomplete", "failed", "cancelled"})
PHASE_NAMES = frozenset({
    "idle", "preparing", "thinking", "observing", "planning", "waiting_model",
    "executing", "verifying", "pausing", "paused", "takeover", "waiting_user",
    "waiting_confirmation", "stopping",
}) | TERMINAL_STATUSES
