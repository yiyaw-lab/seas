# Telegram Setup — SEAS Weekly Message

One-time setup so SEAS can text you `demo/weekly_project_message.md` every
Friday via the `seas-friday-telegram` workflow.

## 1. Create a bot with BotFather

1. Open Telegram and message [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts (name + username).
3. BotFather replies with a **bot token** like `123456789:ABC-DEF...`.

## 2. Save the bot token

Keep that token handy — it becomes `TELEGRAM_BOT_TOKEN`. Treat it like a
password; do not commit it.

## 3. Send a message to your bot once

Telegram won't let a bot message you until you've started a chat with it.
Open your new bot in Telegram and send it any message (e.g. `hi`).

## 4. Get your chat_id

With the token below, fetch recent updates:

```bash
curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates"
```

Look for `"chat":{"id":<number>...}` in the JSON. That number is your
`TELEGRAM_CHAT_ID` (it may be negative for groups).

## 5. Add both as GitHub repo secrets

In the GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**.

Add two secrets:

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | the token from BotFather |
| `TELEGRAM_CHAT_ID` | the chat id from step 4 |

## 6. Test the workflow manually

In the GitHub repo: **Actions → SEAS Friday Telegram → Run workflow**.

It will generate the weekly message and send it to your Telegram. You should
receive the message within a few seconds. On success the log prints
`✅ Message delivered to Telegram.`

## Test locally (optional)

```bash
export TELEGRAM_BOT_TOKEN="123456789:ABC-DEF..."
export TELEGRAM_CHAT_ID="123456789"
python src/seas_demo.py
python src/send_telegram.py
```

## Schedule

The workflow runs every **Friday at 08:00 America/Los_Angeles**
(`cron: "0 15 * * 5"`, UTC). GitHub cron has no daylight-saving awareness, so
during PDT the actual delivery is 08:00 and during PST it is 07:00 — adjust the
cron if you want it pinned exactly.
