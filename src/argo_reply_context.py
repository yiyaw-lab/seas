"""reply_context.py -- extract user text + reply-to context from a Telegram message dict.

Kept as a standalone module so it can be imported by argo_webhook.py without
circular deps and tested in isolation.
"""

MAX_REPLY_EXCERPT = 120  # chars; keeps context tight without flooding the prompt


def extract_user_text(message: dict) -> str:
    """Return the user's text for this turn, prepended with a reply-to excerpt
    when the message is a Telegram reply.

    Telegram sets `reply_to_message` when the user hits the Reply button on a
    prior message.  We surface that as a bracketed prefix so Argo can see what
    Yiya was reacting to, without needing to search chat history.

    Falls back cleanly to bare text / caption if there's no quoted message, so
    all existing behaviour is unchanged.
    """
    text = message.get("text") or message.get("caption") or ""

    replied = message.get("reply_to_message")
    if not replied:
        return text

    quoted = replied.get("text") or replied.get("caption") or ""
    if not quoted:
        return text

    excerpt = quoted[:MAX_REPLY_EXCERPT]
    if len(quoted) > MAX_REPLY_EXCERPT:
        excerpt += "..."

    return f'[replying to: "{excerpt}"]\n{text}'
