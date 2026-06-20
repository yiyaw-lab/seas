"""Mine the chat log for weakness signals and file them into the incident ledger.

The diagnose loop (argo_diagnose) only ever sees failures that some module
explicitly recorded into argo_incidents -- a logged send error, a budget cap, a
model timeout. But the loudest weakness signal Argo has is the user telling it,
in plain words, that it got something wrong ("no, that's not what i meant",
"you misunderstood"). Those corrections lived only in the chat log and never
reached the self-improvement funnel, so a recurring quality problem the user
keeps re-explaining never surfaced as an incident.

This miner reads recent USER turns from the shared chat log and, for the few
unambiguous correction/frustration phrases, records a "chat_weakness" incident
(reusing record_incident's dedup + fingerprint rollup). It is deliberately
PRECISION-BIASED: a clean, satisfied transcript must yield ZERO incidents. We
match only anchored phrases that a satisfied user would not say, never a bare
keyword like "wrong" that fires on benign questions ("what went wrong with X?").

Placement triad (the scheduled-behavior contract): this rides the same
LOCAL_COMMAND as diagnose -- argo_diagnose.run_cli calls mine_chat_log() BEFORE
diagnose(), so it (1) TRIGGERS only where diagnose triggers (the webhook's
in-process local_loop on the Railway volume, never Actions cron -- the chat log
is gitignored and absent in a fresh Actions checkout), (2) reads the SAME
volume-backed CHAT_LOG_PATH the live bot writes, and (3) feeds the SAME incident
ledger diagnose consumes. No new scheduler wiring.

A USER turn is any turn whose role is not "Argo": the webhook records user turns
under the profile name (e.g. "Yiya"), not the literal "user". Stdlib + the
shared-utils layer (argo_store/argo_memory/argo_incidents/argo_log).
"""

import re

import argo_incidents
import argo_memory
import argo_paths
import argo_store
from argo_log import get_logger

log = get_logger(__name__)

# How many of the most recent turns (across all chats) to scan. Bounded so the
# miner stays cheap and only reflects current behavior, not ancient history.
SCAN_TURNS = 60

# High-watermark store: the count of chat-log turns already mined. Re-exported as a
# module-level constant (the test patch point) so the helper reads the override at
# call time -- see argo_paths.CHATMINE_WATERMARK_PATH for the placement rationale.
WATERMARK_PATH = argo_paths.CHATMINE_WATERMARK_PATH

# Precision-biased weakness signals. Each (label, pattern) fires only on an
# anchored phrase a satisfied user would not say. The label becomes the stable
# part of the incident signature, so repeats of the same KIND of correction roll
# into one cluster via record_incident's fingerprint. Keep these conservative --
# a false positive here nudges the owner about a non-problem.
_SIGNALS = (
    ("misunderstood", re.compile(
        r"\byou\s+(?:completely\s+)?(?:misunderstood|misread|missed the point)\b", re.I)),
    ("not_what_i_meant", re.compile(
        r"\b(?:that'?s|that is|thats)\s+not\s+what\s+i\s+(?:meant|asked|said)\b", re.I)),
    ("not_what_i_meant", re.compile(
        r"\bnot\s+what\s+i\s+(?:meant|asked for|was asking)\b", re.I)),
    ("thats_wrong", re.compile(
        r"\b(?:no,?\s+)?(?:that'?s|that is|thats|this is|you'?re|youre|you are)\s+"
        r"(?:completely\s+|just\s+)?wrong\b", re.I)),
    ("incorrect", re.compile(
        r"\b(?:that'?s|that is|thats|this is)\s+(?:incorrect|not correct|not right)\b", re.I)),
    ("didnt_answer", re.compile(
        r"\b(?:you|that)\s+(?:didn'?t|did not|didnt)\s+answer\b", re.I)),
    ("wrong_again", re.compile(r"\bwrong again\b", re.I)),
    ("still_wrong", re.compile(r"\bstill\s+(?:wrong|incorrect|not right)\b", re.I)),
    ("not_listening", re.compile(r"\byou'?re\s+not\s+listening\b", re.I)),
    # Precision-biased: anchor to a corrective object so benign lines don't fire.
    # "told you not to ..." only counts when bound to a do/say/keep/repeat behavior
    # ("not to do that", "not to keep doing"), never "told you not to worry". "to
    # stop" only counts when it's the bare stop-correction or "stop doing/saying
    # that", never the locative "stop by the store".
    ("stop_doing_that", re.compile(
        r"\b(?:i\s+)?(?:already\s+)?(?:told|asked)\s+you\s+"
        r"(?:not\s+to\s+(?:do|say|keep|repeat|bring(?:\s+up)?)\b"
        r"|to\s+stop(?=\s+(?:do(?:ing)?|say(?:ing)?|that|it|this)\b|[.!?,]|$))", re.I)),
)

# Trim the excerpt kept as the incident sample (record_incident also caps it).
_SAMPLE_CHARS = 200


def _all_turns():
    """Every turn in the shared chat log, oldest-first, or [] if missing/unreadable.

    Reads the whole store directly (not argo_memory.recent, which filters to a
    single chat_id): the miner scans across chats. Never raises."""
    try:
        log_data = argo_store.load_json(argo_memory.CHAT_LOG_PATH, [])
        return log_data if isinstance(log_data, list) else []
    except (OSError, ValueError) as exc:
        log.warning("chatmine: could not read chat log: %s", exc)
        return []


def _is_user_turn(turn):
    """A USER turn is anything not authored by Argo. The webhook stores user turns
    under the profile name (e.g. 'Yiya'), so role == 'user' would miss them all."""
    role = (turn.get("role") or "").strip().lower()
    return role not in ("", "argo")


def _read_watermark():
    """Count of chat-log turns already mined (an offset into the append-only log).
    0 if missing/unreadable/malformed. Never raises."""
    try:
        data = argo_store.load_json(WATERMARK_PATH, {})
        n = data.get("mined_turns") if isinstance(data, dict) else 0
        return int(n) if isinstance(n, (int, float)) and n >= 0 else 0
    except (OSError, ValueError, TypeError) as exc:
        log.warning("chatmine: could not read watermark: %s", exc)
        return 0


def _write_watermark(n):
    """Persist the high-watermark (turns mined so far) to the volume-backed store.
    Best-effort: a write failure just means the next run re-scans, never a crash."""
    try:
        WATERMARK_PATH.parent.mkdir(parents=True, exist_ok=True)
        argo_store.save_json(WATERMARK_PATH, {"mined_turns": int(n)})
    except (OSError, ValueError) as exc:
        log.warning("chatmine: could not write watermark: %s", exc)


def mine_chat_log(scan_turns=SCAN_TURNS):
    """Scan USER turns appended SINCE the last run for weakness signals and file each
    distinct one as a 'chat_weakness' incident (dedup/fingerprint handled by
    record_incident).

    Idempotent across runs: a high-watermark (the count of turns already mined,
    persisted to the volume-backed WATERMARK_PATH) gates the scan to turns appended
    since last time, so re-running over the same log records ZERO new incidents and
    counts don't inflate / resolved clusters don't reopen. Bounded to the most recent
    `scan_turns` of those new turns so the miner stays cheap.

    Precision-biased: only anchored correction/frustration phrases match, so a
    clean transcript records nothing. Returns the number of record_incident calls
    made (>=0). Never raises -- it runs inside the scheduled diagnose path."""
    recorded = 0
    try:
        all_turns = _all_turns()
        total = len(all_turns)
        # Clamp to [0, total]: a watermark past the log end means the store was
        # truncated/reset under us -- treat as fully mined, don't re-mine from zero.
        watermark = min(_read_watermark(), total)
        # New turns are those after the watermark; still cap the scan to the most
        # recent `scan_turns` of them so a long backlog stays cheap.
        start = max(watermark, total - scan_turns)
        turns = all_turns[start:]
        for turn in turns:
            if not isinstance(turn, dict) or not _is_user_turn(turn):
                continue
            text = turn.get("text") or ""
            if not isinstance(text, str) or not text.strip():
                continue
            seen_labels = set()
            for label, pattern in _SIGNALS:
                if label in seen_labels:
                    continue  # one incident per signal-kind per turn
                m = pattern.search(text)
                if not m:
                    continue
                seen_labels.add(label)
                # Signature: the signal label + the matched phrase. The label keeps
                # repeats of one correction KIND in a single cluster; record_incident's
                # fingerprint strips the volatile remainder.
                signature = f"user correction: {label} ({m.group(0).strip().lower()})"
                excerpt = text.strip()[:_SAMPLE_CHARS]
                if argo_incidents.record_incident(
                        kind="chat_weakness", signature=signature, sample=excerpt):
                    recorded += 1
        # Advance the watermark to the full log length, not just the scanned window:
        # turns the scan cap skipped are still "past" and must not be re-mined later.
        _write_watermark(total)
        if recorded:
            log.info("chatmine: filed %d chat_weakness incident(s)", recorded)
        return recorded
    except Exception:
        log.warning("chatmine: mine_chat_log failed", exc_info=True)
        return recorded
