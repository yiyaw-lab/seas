"""Argo's chat memory -- the one append-only conversation log, shared.

This was private to argo_webhook (`_append_turn`/`_recent_turns`), so only turns
that came IN through the webhook were remembered. Argo's PROACTIVE sends -- the
weekly project (argo_project) and tripwire alerts (argo_watch) -- went straight to
Telegram via send_telegram and never landed here, so when the user replied about an
article Argo had just pushed ("is this a counter to Mythos?"), the webhook had no
record of it and Argo looked like it had amnesia.

Extracting the store into its own tiny module lets the proactive senders record
what they send WITHOUT importing the heavy Flask/MCP webhook (which would pull in
half the app and risk an import cycle). The webhook now delegates to this.

chat_id is normalized to str on both write and read: the webhook gets an int
chat_id from Telegram, but the proactive path keys off the TELEGRAM_CHAT_ID env
var (a string). Without normalization the webhook's reads wouldn't see the
proactive writes -- defeating the whole point. Comparing as str unifies them and
still matches legacy int-keyed entries.

Backed by the volume-capable ARGO_CHAT_LOG path (see argo_paths); stdlib + the
shared argo_store I/O only.
"""

from datetime import datetime, timezone

import argo_paths
import argo_store

# Module-level so tests can patch it (mock.patch.object(argo_memory, "CHAT_LOG_PATH",
# tmp)); record/recent read this global at call time so the override bites.
CHAT_LOG_PATH = argo_paths.CHAT_LOG_PATH

# How many recent turns to feed the model as short-term memory.
HISTORY_TURNS = 12


def record(chat_id, role, text):
    """Append one turn to the durable chat log (creates the file/dir if needed).

    No-op if chat_id is falsy (e.g. TELEGRAM_CHAT_ID unset on a proactive call)
    so we never write a turn that can't be keyed back to a conversation.
    """
    if not chat_id:
        return
    CHAT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = argo_store.load_json(CHAT_LOG_PATH, [])
    if not isinstance(log, list):  # never lose a turn over a corrupt/odd file
        log = []
    log.append({
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "chat_id": str(chat_id),
        "role": role,
        "text": text,
    })
    argo_store.save_json(CHAT_LOG_PATH, log)


def recent(chat_id, n=HISTORY_TURNS):
    """Return the last n turns for this chat (str-compared, so proactive sends and
    legacy int-keyed turns are both visible). Empty list if none/unreadable."""
    log = argo_store.load_json(CHAT_LOG_PATH, [])
    if not isinstance(log, list):
        return []
    turns = [t for t in log if str(t.get("chat_id")) == str(chat_id)]
    return turns[-n:]
