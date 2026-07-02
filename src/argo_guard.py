"""
Argo resilience guardrails (Phase E1) — the safety floor under self-heal.

Three small, well-tested primitives, kept in one auditable place:

  - retry(fn): capped exponential backoff + jitter for TRANSIENT errors only
    (429 / 5xx / timeouts / connection). Never retries auth/4xx-client errors.
  - CircuitBreaker: per-dependency breaker; opens after N consecutive failures,
    half-opens after a cooldown to probe recovery. Stops hammering a dead dep.
  - DailyBudget: a hard per-day call-count ceiling (persisted to disk, so a
    runaway loop can't rack up calls across restarts) PLUS a hard per-day
    estimated-cost ceiling (DAILY_COST_CAP_USD, read live off argo_cost's
    ledger). This is the guard that prevents the $437 / 27M-token class of
    failure -- and, since a premium model can blow a cost budget well under the
    call-count cap, the DAILY_COST_CAP_USD half specifically.

Pure stdlib. No new deps. Used by argo_observe (LLM calls) and argo_webhook
(per-day cap). Hard caps are non-negotiable: uncapped retry/recovery is itself a
documented failure mode.
"""

import json
import os
import random
import threading
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
# Hard ceiling on ESTIMATED model spend per UTC day (all of Argo), independent of
# the flat call-count cap above -- a premium model (e.g. claude-fable-5) can blow a
# cost budget well under 500 calls. Estimated via argo_cost.cost_today_usd() (a
# $/MTok table applied to recorded token counts), not a billing-accurate figure.
DAILY_COST_CAP_USD = float(os.environ.get("ARGO_DAILY_COST_CAP", "20.0"))

# Serializes DailyBudget.check_and_increment()'s read-check-increment across
# concurrent webhook threads (module-level, not per-instance: today's process
# uses one DailyBudget, and a module lock also covers the pathological case of
# two instances racing the same on-disk state/ledger). Without it, N threads can
# all read cost_today_usd()/state below the cap before any of their usage lands
# in the ledger (the ledger write happens later, inside the actual model call),
# so the cap can overshoot. This lock only serializes the check+file-count bump
# here; it does NOT reserve spend ahead of the call, so the residual overshoot
# bound is: (in-flight premium calls already past this check) x (max single-call
# cost) -- calls that passed the gate before their own usage row was written.
# Widening this to a true reservation would need the ledger write itself gated
# on a matching reservation release; not done here to keep the fix minimal and
# consistent with the rest of the module (see argo_cost._write_lock for the
# analogous ledger-side lock).
_budget_lock = threading.Lock()


def _is_transient(exc):
    """True only for errors worth retrying. Auth/client(4xx, non-429) are NOT."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if any(s in name for s in ("timeout", "connection")):
        return True
    # urllib.error.URLError hides the real cause in .reason (often an OSError like
    # ConnectionRefusedError / socket.timeout / gaierror) -- a bare URLError's own
    # type name is just "URLError" and its message phrasing ("timed out") dodges the
    # checks above, so unwrap .reason and re-check it by type name.
    reason = getattr(exc, "reason", None)
    if reason is not None and reason is not exc:
        rname = type(reason).__name__.lower()
        if any(s in rname for s in ("timeout", "connection", "gaierror", "reset")):
            return True
    # status codes if the SDK exposes one
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(code, int):
        return code == 429 or 500 <= code < 600
    return any(s in msg for s in ("429", "timeout", "timed out", "temporarily",
                                  "overloaded", "503", "502", "500", "rate limit",
                                  "connection refused", "connection reset",
                                  "name resolution", "temporary failure",
                                  "network is unreachable"))


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
        except Exception as exc:
            # Only retry-eligible (transient) failures count toward opening. A
            # permanent error -- a billing 400, an auth failure -- fails fast and
            # propagates, but must NOT advance the breaker: a half-open probe can
            # never recover a billing/auth condition, and counting it would mask a
            # since-recovered provider behind a still-open breaker. So circuit_open
            # now means a genuine provider outage, not "out of credits".
            if not _is_transient(exc):
                # The provider answered (just with a non-transient error like a billing
                # 400), so any transient outage the breaker was guarding is over: close
                # it. Without this, a half-open probe that hits a non-transient error
                # re-raises WITHOUT refreshing opened_at, leaving the breaker dangling
                # half-open so every later call runs the provider instead of failing
                # fast. The error still propagates and fails fast at the call level.
                self.failures = 0
                self.opened_at = None
                raise
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = time.monotonic()
                log.warning("circuit '%s' OPENED after %d transient failures",
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
    """Hard per-UTC-day call ceiling (+ an estimated-cost ceiling), persisted so
    the call count survives restarts. Call check_and_increment() before a model
    call; it raises BudgetExceeded at either cap. This is the last line of
    defence against a runaway loop -- or a premium model that blows a cost
    budget well under the call-count cap.

    The cost check is a live read of argo_cost.cost_today_usd() (today's
    estimated spend across every model, from the same ledger every model call
    already writes to via argo_cost.record_usage) rather than its own persisted
    counter: the ledger IS the source of truth for spend, so a second counter
    would just be a second, driftable copy of the same number."""

    class BudgetExceeded(RuntimeError):
        pass

    def __init__(self, path=None, cap=DAILY_CALL_CAP, cost_cap=DAILY_COST_CAP_USD):
        self.path = Path(path) if path else (ROOT / "data" / "argo_budget.json")
        self.cap = cap
        self.cost_cap = cost_cap

    def _load(self):
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except (json.JSONDecodeError, ValueError):
                pass
        return {"day": "", "count": 0}

    def _today(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _check_cost(self, today):
        # Late import: argo_cost is a peer low-level dep (both sit under
        # argo_observe); importing at call time, not module load, avoids any
        # import-order fragility between the two and keeps this cheap when the
        # ledger is empty/missing (cost_today_usd degrades to 0.0, never raises).
        import argo_cost
        spent = argo_cost.cost_today_usd()
        if spent >= self.cost_cap:
            log.warning("daily budget exhausted: $%.2f cost cap reached for %s "
                        "(spent $%.2f)", self.cost_cap, today, spent)
            raise self.BudgetExceeded(
                f"daily cost cap of ${self.cost_cap:.2f} reached for {today} "
                f"(spent ${spent:.2f})")

    def check_and_increment(self):
        # Hold the module lock across the whole read-check-increment so
        # concurrent webhook threads serialize instead of all reading the same
        # under-cap state before any of their usage lands (see _budget_lock).
        with _budget_lock:
            today = self._today()
            self._check_cost(today)
            state = self._load()
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
