"""F7 escalation broker: the owner-decision logic behind ask_owner /
get_owner_answers.

A scheduled cloud caller (e.g. a /vacation run) has NEITHER the Railway volume
NOR the Telegram secrets, so it cannot send_telegram or read the chat log
directly (the placement-triad trap). It brokers through Argo, which is
in-container with both: ask_owner Telegrams a question and records an OPEN
pending decision; get_owner_answers reads the chat log, matches the owner's
reply to the most-recent OPEN decision, and marks it answered.

This module holds the PURE broker logic. The @mcp.tool() wrappers stay in
argo_mcp_server (so the tools register on the one FastMCP instance and inherit
the bearer-auth'd /mcp mount); they pass the volume-backed decisions PATH in, so
the store path is the caller's (patchable in tests via the server's
PENDING_DECISIONS_PATH). Reuses argo_store for atomic JSON I/O and argo_log for
logging -- no re-inlined json/ssl.
"""

from datetime import datetime, timezone

import argo_store
from argo_log import get_logger

log = get_logger(__name__)


def next_decision_id(decisions):
    """Deterministic, collision-free id: D-<n> where n is one past the highest
    D-<n> already in the store. Derived from the store (not random/uuid), so a
    test with a known store gets a known next id."""
    highest = 0
    for d in decisions:
        did = str(d.get("id", ""))
        if did.startswith("D-"):
            try:
                highest = max(highest, int(did[2:]))
            except ValueError:
                continue
    return f"D-{highest + 1:03d}"


def record_decision(question, path, decision_id=None):
    """Append an OPEN pending decision and return its record. id is derived from
    the store (or injected, for tests); ts is the same Zulu format the chat log
    uses, so get_owner_answers can compare a reply's ts against it lexically."""
    decisions = argo_store.load_json(path, [])
    if not isinstance(decisions, list):
        decisions = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rec = {
        "id": decision_id or next_decision_id(decisions),
        "ts": ts,
        "question": question,
        "status": "open",
    }
    decisions.append(rec)
    argo_store.save_json(path, decisions)
    return rec


def mark_decision(path, decision_id, status, answer=None):
    """Set a decision's status (and optionally its answer + answered_at). Returns
    True if the decision was found and updated."""
    decisions = argo_store.load_json(path, [])
    if not isinstance(decisions, list):
        return False
    for d in decisions:
        if d.get("id") == decision_id:
            d["status"] = status
            if answer is not None:
                d["answer"] = answer
                d["answered_at"] = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ")
            argo_store.save_json(path, decisions)
            return True
    return False


def ask_owner_impl(question, path):
    """Telegram the owner a question and record it as a pending decision. `path`
    is the volume-backed pending-decisions store. Returns a relayable string."""
    question = (question or "").strip()
    if not question:
        return "Refused: ask_owner needs a non-empty question."
    import send_telegram
    rec = record_decision(question, path)
    # The owner sees a plain-text prompt naming the decision id, so a free-text
    # reply can be matched back. No markdown / em dashes (Telegram, plain).
    sent = send_telegram.try_send_message(
        f"A decision is waiting on you ({rec['id']}): {question}\n\n"
        "Just reply here and I'll relay your answer.")
    if not sent:
        # Send failed: don't leave a phantom OPEN decision claiming the owner was
        # asked when they never saw it. Mark it failed so it can't be matched, and
        # report honestly (do not pretend it was delivered).
        mark_decision(path, rec["id"], status="send_failed")
        log.warning("ask_owner: send failed for %s; marked send_failed", rec["id"])
        return (f"Recorded decision {rec['id']} but could NOT deliver it to the "
                "owner (Telegram send failed). Do not wait on an answer; treat "
                "this as undelivered and decide conservatively or retry later.")
    log.info("ask_owner: asked owner, decision %s open", rec["id"])
    return (f"Asked the owner (decision {rec['id']}). Poll get_owner_answers "
            "later to pick up their reply.")


def get_owner_answers_impl(since, path):
    """Check whether the owner answered the most-recent OPEN decision. `path` is
    the volume-backed pending-decisions store. Returns a JSON match payload (id +
    answer) or a short no-match note."""
    import json
    decisions = argo_store.load_json(path, [])
    if not isinstance(decisions, list):
        decisions = []
    open_decisions = [d for d in decisions if d.get("status") == "open"]
    if not open_decisions:
        return "No open decisions waiting on the owner."
    # Match the MOST-RECENT open decision (per spec), so a new question supersedes
    # an older unanswered one for the owner's next reply. The store is append-only
    # and ts is monotonic on append, so the last open record IS the most recent --
    # no sort needed (this also makes same-second ties resolve to the latest ask).
    target = open_decisions[-1]
    # A reply can only be this decision's answer if it post-dates the question;
    # `since` narrows further. Zulu, fixed-width ts -> lexical compare is correct.
    # NOTE: the chat log stamps whole seconds, so a (rare) owner message sent in
    # the SAME second as the question, before it, can be mis-matched; we accept
    # that <1s window rather than drop genuine same-second replies with a strict >.
    floor = max(target.get("ts", ""), (since or "").strip())

    # Read ONLY the owner's conversation via argo_memory.recent(chat_id): it
    # normalizes the chat_id to str (the webhook keys turns by int Telegram id,
    # proactive sends by the TELEGRAM_CHAT_ID env string) and scopes to one chat,
    # so a reply from a different conversation can never be mis-matched. A bare
    # global scan would do neither. The owner's chat is TELEGRAM_CHAT_ID; without
    # it the bot could not have sent the question, so there is nothing to poll.
    import os
    import argo_memory
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not chat_id:
        return (f"Decision {target['id']} is open, but TELEGRAM_CHAT_ID is unset so "
                "I can't read the owner's chat to match a reply.")
    # Pull a generous recent window (the poll cadence is far tighter than this).
    turns = argo_memory.recent(chat_id, n=200)
    # The owner's turns are any role that isn't Argo's own; take the FIRST such
    # reply at/after the floor (the answer to the question we just asked).
    reply = next(
        (t for t in turns
         if t.get("role") != "Argo"
         and (t.get("text") or "").strip()
         and t.get("ts", "") >= floor),
        None,
    )
    if reply is None:
        return (f"Decision {target['id']} is still open; the owner hasn't replied "
                "yet. Poll again later.")
    answer = reply["text"].strip()
    mark_decision(path, target["id"], status="answered", answer=answer)
    log.info("get_owner_answers: matched reply to %s, marked answered", target["id"])
    return json.dumps({"id": target["id"], "answer": answer})
