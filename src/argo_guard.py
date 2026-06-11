"""
Argo resilience guardrails (Phase E1) — the safety floor under self-heal.

Three small, well-tested primitives, kept in one auditable place:

  - retry(fn): capped exponential backoff + jitter for TRANSIENT errors only
    (429 / 5xx / timeouts / connection). Never retries auth/4xx-client errors.
  - CircuitBreaker: per-dependency breaker; opens after N consecutive failures,
    half-opens after a cooldown to probe recovery. Stops hammering a dead dep.
  - DailyBudget: a hard per-day counter (calls and/or a cost proxy) persisted to
    disk, so a runaway loop can't rack up cost across restarts. This is the
    guard that prevents the $437 / 27M-token class of failure.

Pure stdlib. No new deps. Used by argo_observe (LLM calls) and argo_webhook
(per-day cap). Hard caps are non-negotiable: uncapped retry/recovery is itself a
documented failure mode.
"""

import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from argo_log import get_logger

log = get_logger(__name__)

ROOT = Path(__file__).resolve().parent.parent

# --- tunables (conservative on purpose) ---
MAX_RETRIES = 3
BASE_DELAY = 1.0          # seconds; grows 1, 2, 4 ... with jitter
MAX_DELAY = 20.0
BREAKER_THRESHOLD = 4     # consecutive failures before opening
BREAKER_COOLDOWN = 60.0   # seconds before a half-open probe
DAILY_CALL_CAP = 500      # hard ceiling on model calls per UTC day (all of Argo)


def _is_transient(exc):
    """True only for errors worth retrying. Auth/client(4xx, non-429) are NOT."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if any(s in name for s in ("timeout", "connection")):
        return True
    # status codes if the SDK exposes one
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(code, int):
        return code == 429 or 500 <= code < 600
    return any(s in msg for s in ("429", "timeout", "temporarily",
                                  "overloaded", "503", "502", "500", "rate limit"))


def retry(fn, *, max_retries=MAX_RETRIES, label="call"):
    """Call fn(); retry transient failures with capped backoff + jitter. Raises
    the last exception once attempts are exhausted, or immediately for
    non-transient (e.g. auth) errors so we don't waste attempts."""
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:
            attempt += 1
            if not _is_transient(exc) or attempt > max_retries:
                raise
            delay = min(BASE_DELAY * (2 ** (attempt - 1)), MAX_DELAY)
            delay += random.uniform(0, delay * 0.25)  # jitter
            log.warning("%s: transient %s, retry %d/%d in %.1fs",
                        label, type(exc).__name__, attempt, max_retries, delay)
            time.sleep(delay)


class CircuitBreaker:
    """Per-dependency breaker. Construct one per external service and wrap calls.
    open -> fail fast; half-open after cooldown -> one probe; success -> close."""

    def __init__(self, name, threshold=BREAKER_THRESHOLD, cooldown=BREAKER_COOLDOWN):
        self.name = name
        self.threshold = threshold
        self.cooldown = cooldown
        self.failures = 0
        self.opened_at = None

    def _state(self):
        if self.opened_at is None:
            return "closed"
        if time.monotonic() - self.opened_at >= self.cooldown:
            return "half-open"
        return "open"

    def call(self, fn):
        state = self._state()
        if state == "open":
            raise RuntimeError(
                f"[guard] circuit '{self.name}' is open; failing fast")
        try:
            result = fn()
        except Exception:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = time.monotonic()
                log.warning("circuit '%s' OPENED after %d failures",
                            self.name, self.failures)
                try:  # late import: guard is a low-level dep, avoid an import cycle
                    import argo_incidents
                    argo_incidents.record_incident("circuit_open", self.name)
                except Exception:
                    pass
            raise
        # success: reset
        if self.failures or self.opened_at:
            log.info("circuit '%s' recovered, closing", self.name)
        self.failures = 0
        self.opened_at = None
        return result


class DailyBudget:
    """Hard per-UTC-day call ceiling, persisted so it survives restarts. Call
    check_and_increment() before a model call; it raises BudgetExceeded at the
    cap. This is the last line of defence against a runaway loop."""

    class BudgetExceeded(RuntimeError):
        pass

    def __init__(self, path=None, cap=DAILY_CALL_CAP):
        self.path = Path(path) if path else (ROOT / "data" / "argo_budget.json")
        self.cap = cap

    def _load(self):
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except (json.JSONDecodeError, ValueError):
                pass
        return {"day": "", "count": 0}

    def _today(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def check_and_increment(self):
        state = self._load()
        today = self._today()
        if state.get("day") != today:
            state = {"day": today, "count": 0}  # new day -> reset
        if state["count"] >= self.cap:
            log.warning("daily budget exhausted: %d call cap reached for %s",
                        self.cap, today)
            raise self.BudgetExceeded(
                f"daily call cap of {self.cap} reached for {today}")
        state["count"] += 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state))
        return state["count"]
