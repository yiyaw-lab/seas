"""Telegram media pipeline, extracted from argo_webhook.

Five functions that take an incoming Telegram photo/document, pull the bytes down
from the Telegram CDN, persist a file to disk, and feed the content through Argo's
normal history-aware brain: download_telegram_file, download_telegram_photo,
save_incoming_file, handle_photo, handle_document. They formed one cohesive seam
in the webhook server, so they live here now -- pure of the chat routing, easy to
read in isolation.

The two handlers (handle_photo / handle_document) need the webhook's reply
function (_generate_reply, which stays there with the model routing) and a couple
of its module-level names the tests patch (FILES_DIR, _download_telegram_file,
_download_telegram_photo, _generate_reply). To avoid a circular import AND keep
those patch points live, the webhook keeps thin wrappers (_handle_photo,
_handle_document, _download_telegram_file, _download_telegram_photo,
_save_incoming_file) that forward its own globals/functions resolved at call time;
this module never imports argo_webhook. Same pattern as the argo_rating extraction.
Stdlib + the shared layer (argo_http, argo_log) only.
"""

import base64
import json
import os
import re
from pathlib import Path

import argo_http
from argo_log import get_logger

log = get_logger(__name__)


def download_telegram_file(file_id):
    """Download any Telegram file by file_id. Returns (bytes, file_path) or
    (None, None). Two steps: getFile to resolve a CDN file_path, then download
    from the file CDN -- both need the token."""
    import urllib.request

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not file_id or not token:
        return None, None
    ctx = argo_http.tls_context()
    try:
        api = f"https://api.telegram.org/bot{token.strip()}/getFile?file_id={file_id}"
        with urllib.request.urlopen(api, timeout=15, context=ctx) as r:
            path = json.loads(r.read().decode()).get("result", {}).get("file_path")
        if not path:
            return None, None
        dl = f"https://api.telegram.org/file/bot{token.strip()}/{path}"
        with urllib.request.urlopen(dl, timeout=30, context=ctx) as r:
            return r.read(), path
    except Exception as exc:
        log.warning("telegram file download failed: %s", exc)
        return None, None


def download_telegram_photo(msg, download_file=download_telegram_file):
    """Download the largest photo in a Telegram message. Returns (bytes,
    media_type) or (None, None). `download_file` is injectable so the webhook
    wrapper can forward its own (patchable) _download_telegram_file."""
    # Telegram delivers an image two ways:
    #   - "photo" (compressed, via the image picker): msg['photo'] = [sizes...]
    #   - "document" (sent as a FILE, common on desktop / to keep quality):
    #     msg['document'] = {file_id, mime_type: 'image/...'}
    # We must handle BOTH, or a screenshot sent as a file is silently dropped.
    file_id = None
    media = None
    photos = msg.get("photo") or []
    doc = msg.get("document") or {}
    if photos:
        file_id = photos[-1].get("file_id")  # array of sizes; last is largest
    elif doc and str(doc.get("mime_type", "")).startswith("image/"):
        file_id = doc.get("file_id")
        media = doc.get("mime_type")
    data, path = download_file(file_id)
    if data is None:
        return None, None
    # Prefer the document's declared mime_type; else infer from the path.
    if not media:
        media = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    return data, media


def save_incoming_file(name, data, files_dir):
    """Persist a user-sent file into files_dir (point ARGO_FILES_DIR at the
    Railway volume so it survives redeploys). Returns the saved Path. The name
    is sanitized to a safe basename and uniquified, never overwritten. `files_dir`
    is passed in so the webhook wrapper forwards its own (patchable) FILES_DIR."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name or "file").name) or "file"
    files_dir = Path(files_dir)
    files_dir.mkdir(parents=True, exist_ok=True)
    dest = files_dir / safe
    n = 1
    while dest.exists():
        dest = files_dir / f"{Path(safe).stem}-{n}{Path(safe).suffix}"
        n += 1
    dest.write_bytes(data)
    return dest


# Files Argo reads inline as text, by extension (Telegram clients' mime types
# are unreliable for these). Anything else non-PDF is saved but not parsed.
_TEXT_EXTS = frozenset({
    ".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml", ".toml", ".xml",
    ".py", ".js", ".ts", ".html", ".css", ".sh", ".log",
})
_MAX_FILE_CHARS = 12000  # keep a huge file from blowing the prompt/budget
_MAX_FILE_BYTES = 19 * 1024 * 1024  # Telegram's bot-API download cap is 20MB


def handle_photo(chat_id, msg, *, send_message, download_photo, generate_reply,
                 append_turn, user_name):
    """A screenshot/image: SEE it inside the conversation and respond to what the
    user actually wants. The image goes through Argo's normal tool-enabled brain
    (history + MCP tools), so it can react, identify, brainstorm, look things up --
    and, when it JUDGES the image is genuinely design/product inspiration, call
    save_taste_signal itself. No longer force-converted into a 'taste lesson'; no
    longer silently dropped.

    Collaborators are injected so the webhook wrapper forwards its own (patchable)
    _download_telegram_photo / _generate_reply / _append_turn and send_telegram."""
    caption = msg.get("caption", "") or ""
    img, media = download_photo(msg)
    if img is None:
        send_message(
            "got an image but couldn't pull it down, mind resending?")
        return

    # Anthropic image block + a text block carrying the caption (or a neutral note
    # so the model knows there was none). Mirrors observe.describe_image's shape.
    content = [
        {"type": "image", "source": {
            "type": "base64", "media_type": media,
            "data": base64.b64encode(img).decode(),
        }},
        {"type": "text",
         "text": caption or "[the user sent this image with no caption]"},
    ]
    log_user_text = f"[image]{(' ' + caption) if caption else ''}"
    try:
        reply = generate_reply(chat_id, content, log_user_text,
                               route_text=caption, anthropic_only=True)
    except Exception as exc:
        send_message(f"saw the image but couldn't process it: {exc}")
        return
    if reply is None:
        # No vision-capable (Anthropic) model configured this turn. Still record
        # the user's image turn so the gap shows up in history.
        append_turn(chat_id, user_name, log_user_text)
        no_vision_msg = ("got an image but can't see it right now "
                         "(no vision model configured). mind describing it, "
                         "or resending in a bit?")
        append_turn(chat_id, "Argo", no_vision_msg)
        send_message(no_vision_msg)
        return
    send_message(reply)


def handle_document(chat_id, msg, *, send_message, download_file, save_file,
                    generate_reply, append_turn, user_name):
    """A non-image file (PDF, notes, csv, code...): download it, SAVE it to
    FILES_DIR, and read it through the normal history-aware brain. Previously
    these fell through the text guard and were silently dropped -- the user
    sent Argo a file and got nothing back.

    Collaborators are injected so the webhook wrapper forwards its own (patchable)
    _download_telegram_file / _save_incoming_file / _generate_reply / _append_turn
    and send_telegram."""
    doc = msg.get("document") or {}
    caption = msg.get("caption", "") or ""
    name = doc.get("file_name") or "file"
    if (doc.get("file_size") or 0) > _MAX_FILE_BYTES:
        send_message(
            f"{name} is over Telegram's 20MB bot limit so I can't pull it "
            "down. mind sending a smaller version or a link?")
        return
    data, _ = download_file(doc.get("file_id"))
    if data is None:
        send_message(
            f"got {name} but couldn't pull it down, mind resending?")
        return
    try:
        saved_note = f"saved to {save_file(name, data)}"
    except OSError as exc:
        log.warning("could not save incoming file %s: %s", name, exc)
        saved_note = "could not be saved to disk"

    mime = str(doc.get("mime_type") or "")
    suffix = Path(name).suffix.lower()
    caption_note = caption or "[no caption -- react to the file]"
    anthropic_only = False
    if mime == "application/pdf" or suffix == ".pdf":
        # Claude reads PDFs natively via a document block (vision models only).
        anthropic_only = True
        content = [
            {"type": "document", "source": {
                "type": "base64", "media_type": "application/pdf",
                "data": base64.b64encode(data).decode(),
            }},
            {"type": "text",
             "text": f"[the user sent this PDF: {name}, {saved_note}] "
                     f"{caption_note}"},
        ]
    elif mime.startswith("text/") or suffix in _TEXT_EXTS:
        body = data.decode("utf-8", errors="replace")
        clipped = ""
        if len(body) > _MAX_FILE_CHARS:
            body = body[:_MAX_FILE_CHARS]
            clipped = ", clipped here because it's long"
        content = (f"[the user sent a file: {name}, {saved_note}{clipped}]\n"
                   f"---\n{body}\n---\n{caption_note}")
    else:
        content = (f"[the user sent a file you can't read inline: {name} "
                   f"({mime or 'unknown type'}), {saved_note}. acknowledge it "
                   f"honestly and ask what they want done with it] {caption_note}")

    log_user_text = f"[file: {name}]{(' ' + caption) if caption else ''}"
    try:
        reply = generate_reply(chat_id, content, log_user_text,
                               route_text=caption,
                               anthropic_only=anthropic_only)
    except Exception as exc:
        send_message(f"saved {name} but couldn't read it: {exc}")
        return
    if reply is None:
        # PDFs need a Claude model; or no model is configured at all. Still
        # record the turn so the gap shows up in history.
        append_turn(chat_id, user_name, log_user_text)
        no_model_msg = (f"saved {name}, but I can't read it right now (no "
                        "usable model configured). mind telling me what's in "
                        "it, or resending in a bit?")
        append_turn(chat_id, "Argo", no_model_msg)
        send_message(no_model_msg)
        return
    send_message(reply)
