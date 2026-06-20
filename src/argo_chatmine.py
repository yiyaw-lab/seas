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
# anchored phrase a satisfied user would not say. The label IS the incident
# signature (see mine_chat_log) -- not the matched phrase -- so every correction of
# the same KIND rolls into ONE cluster regardless of how it's worded, letting the
# count reach diagnose()'s min_count gate. Keep these conservative -- a false
# positive here nudges the owner about a non-problem.
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
    Returns True on success, False on a write failure (logged, never raised) so the
    caller can gate recording on the advance landing -- see mine_chat_log's
    record-only-if-advanced ordering, which closes the bump-without-advance window."""
    try:
        WATERMARK_PATH.parent.mkdir(parents=True, exist_ok=True)
        argo_store.save_json(WATERMARK_PATH, {"mined_turns": int(n)})
        return True
    except (OSError, ValueError) as exc:
        log.warning("chatmine: could not write watermark: %s", exc)
        return False


def mine_chat_log(scan_turns=SCAN_TURNS):
    """Scan USER turns appended SINCE the last run for weakness signals and file each
    distinct one as a 'chat_weakness' incident (dedup/fingerprint handled by
    record_incident).

    Idempotent across runs: a high-watermark (the count of turns already mined,
    persisted to the volume-backed WATERMARK_PATH) gates the scan to turns appended
    since last time, so re-running over the same log records ZERO new incidents and
    counts don't inflate / resolved clusters don't reopen. The watermark is advanced
    BEFORE any incident is recorded, and recording is skipped if that advance fails
    to persist (the watermark and the incident ledger are separate JSON stores with
    no shared transaction). That ordering closes the "counts bumped but watermark not
    advanced" window a swallowed write-failure used to leave open. The remaining
    residual is the benign opposite: a process kill landing between the watermark
    write and the record loop UNDER-counts a few signals for one slice rather than
    re-counting them or re-opening a resolved cluster.

    Catches up oldest-first: each run scans the OLDEST `scan_turns` turns not yet
    mined (`[watermark : watermark+scan_turns]`) and advances the watermark by ONLY
    what it scanned -- never straight to the log end. So a backlog larger than
    scan_turns (a burst, or downtime) is drained over successive runs with NOTHING
    permanently skipped and nothing re-mined, while per-run work stays capped at
    scan_turns (which bounds a one-time huge first-run / post-downtime catch-up).

    Precision-biased: only anchored correction/frustration phrases match, so a
    clean transcript records nothing. Returns the number of record_incident calls
    made (>=0). Never raises -- it runs inside the scheduled diagnose path."""
    recorded = 0
    try:
        all_turns = _all_turns()
        total = len(all_turns)
        if total == 0:
            # Log missing/empty/unreadable this run. The stored watermark is a
            # positional offset into the (normally append-only) log; overwriting it
            # with 0 would re-mine every already-mined turn on the next good read,
            # re-bumping clusters and reopening resolved incidents. Preserve it.
            log.warning("chatmine: chat log empty/unreadable; preserving watermark")
            return recorded
        stored = _read_watermark()
        if stored > total:
            # The watermark sits past the log end: the log was rebuilt/replaced/
            # shrunk under us, so the stored offset no longer points at the same
            # turns. Re-mine from 0 (record_incident's cluster rollup bounds any
            # re-count) rather than treat the rebuilt log's fresh turns as mined.
            start = 0
        else:
            start = stored
        # Scan the OLDEST unmined slice, capped at `scan_turns`. We advance the
        # watermark by ONLY what we scan (`end`, below), never straight to `total`:
        # if a burst/downtime left more than scan_turns unmined, the older backlog in
        # [end:total] is caught up on the next run(s) -- oldest-first, nothing
        # permanently skipped, per-run work bounded.
        end = min(start + scan_turns, total)
        turns = all_turns[start:end]
        # Advance the watermark FIRST, then record -- and only record if the advance
        # landed (idempotency-before-effect). The watermark and the incident ledger are
        # two separate JSON stores with no shared transaction, so the dangerous window is
        # "counts bumped but watermark not advanced": the next run re-scans the same slice
        # and re-bumps clusters (and could REOPEN a resolved one). By persisting the
        # advanced watermark before any record_incident call and bailing if that write
        # fails, a record-without-advance can no longer happen. The residual flips to the
        # benign direction: an advance-without-record (a process kill between the two
        # stores) UNDER-counts a few signals rather than re-bumping/reopening -- a miss in
        # a precision-biased advisory miner, never a false un-resolve of a held fix.
        if end > start and not _write_watermark(end):
            log.warning("chatmine: watermark advance failed; skipping this slice "
                        "to avoid re-counting on the next run")
            return recorded
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
                # Signature = the signal LABEL only (the weakness CATEGORY the regex
                # matched), NOT the matched substring. _fingerprint normalizes digits/
                # URLs/hex but NOT varied wording, so keying on the substring split
                # "stop doing X" vs "you keep doing Y" -- same weakness, different words --
                # into separate clusters, and diagnose()'s min_count gate never tripped
                # for a recurring weakness re-phrased each time. Keying on the label rolls
                # every same-category correction into ONE cluster whose count actually
                # accrues. The matched phrase + excerpt still ride along as a NON-keyed
                # sample (record_incident stores samples without fingerprinting them), so
                # the cluster stays informative without splitting.
                signature = f"chat weakness: {label}"
                phrase = m.group(0).strip().lower()
                excerpt = text.strip()[:_SAMPLE_CHARS]
                sample = f"[{phrase}] {excerpt}"[:_SAMPLE_CHARS]
                if argo_incidents.record_incident(
                        kind="chat_weakness", signature=signature, sample=sample):
                    recorded += 1
        if recorded:
            log.info("chatmine: filed %d chat_weakness incident(s)", recorded)
        return recorded
    except Exception:
        log.warning("chatmine: mine_chat_log failed", exc_info=True)
        return recorded
