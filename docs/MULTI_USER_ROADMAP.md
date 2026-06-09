# Multi-user roadmap

**Honest status: Argo is single-user today.** It serves one Telegram chat and
keeps one taste/self model. This is a deliberate scope choice, not an
architectural dead end — the identity layer is already abstracted, and the data
layer needs one additive field to go multi-tenant. This doc is the credible path,
written so a reader can judge the real distance.

## What's already multi-user-ready

- **Conversation memory is keyed by `chat_id`.** `argo_memory.record/recent`
  filter by a normalized `str(chat_id)`, so two chats already keep separate
  histories. No global state mixing.
- **Identity is data, not code.** `profile.py` loads `data/profile.json` (name,
  pronouns, persona, values) and templates it into every prompt. The in-code
  `DEFAULT` is a neutral fallback. Adding a user is editing data, not prose —
  the module was extracted from ~30 hardcoded prompt strings precisely for this.
- **Path indirection exists.** `ARGO_PROFILE_PATH`, `ARGO_CHAT_LOG`,
  `ARGO_SELF_PATH`, `ARGO_TASTE_PATH` already route per-deploy state through env
  vars — the seam a per-user router would slot into.

## What's still global (the actual work)

| Store | Today | To make per-user |
|---|---|---|
| `data/argo_projects.json` | one global list | add a `user_id` field per project; filter on read |
| `data/argo_self.json` | one belief set | per-user file or a `user_id` field |
| `data/taste_signals.json` | one taste model | per-user file or a `user_id` field |
| `data/profile.json` | one active profile | `profiles/{user_id}.json`, selected by chat |
| Telegram routing | one `TELEGRAM_CHAT_ID` | a chat_id → user_id map; per-user scheduling |

## The path

1. **Add `user_id` to records** (projects, beliefs, taste). Backfill existing rows
   with a default user. Non-breaking — readers that ignore the field still work.
2. **Resolve `user_id` from `chat_id`** at the webhook edge (a small map), and pass
   it through `profile.load(user_id)` and the store accessors. The keying
   primitive already exists in `argo_memory`.
3. **Per-user scheduling.** The hourly runner loops over users instead of a single
   `TELEGRAM_CHAT_ID`; `data/schedule.json` gains a `user_id` per entry.

Estimated effort: roughly 10–20 lines per affected module, no rewrite — because
the identity abstraction and chat-id keying are already in place. SEAS (the
research engine) is inherently shared infrastructure and is unaffected; only
Argo's per-person state needs the `user_id` dimension.
