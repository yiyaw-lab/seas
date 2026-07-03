"""Anti-bluff / phantom-send gate, extracted from argo_webhook.

A reply can CLAIM an action the model never took: "opening a PR" with no
propose_change, "I read the link" with no fetch, "reply CONFIRM" with nothing
staged. tool_events is the receipt -- the tools that actually fired this turn
(argo_observe.chat_with_mcp). A claim with no backing tool in the receipt is a
phantom and must never reach the user. classify_claim does the detection;
guard_phantom_send is the terminal backstop that replaces the bluff with an honest
correction. _generate_reply re-prompts the model once for the doable-in-turn
classes (PR/CONFIRM) before falling back to guard_phantom_send's suppression.

These functions formed one cohesive seam in the webhook server (no external
callers besides _generate_reply / _guard_phantom_send) so they live here now,
pure of Telegram and the model loop. The webhook keeps thin wrappers
(_classify_claim, _guard_phantom_send, _pr_blocker, _claim_unbacked) that forward
its own MCP_SERVERS global and _note_incident -- the names the tests patch on
argo_webhook -- so this module needs no knowledge of the override and there is no
circular import. Same pattern as the argo_rating extraction. Stdlib only.
"""

import os
import re
from collections import namedtuple

# Project tools that produce a real artifact (legacy class, kept verbatim).
_PROJECT_TOOLS = frozenset({
    "new_project", "add_project", "project_too_complex", "recommend_project",
    "get_latest_project", "scaffold_project", "rehearse_project",
})
# Tools that back a PR claim, a read/link claim, and a CONFIRM prompt.
_PR_TOOLS = frozenset({"propose_change"})
_LINK_READ_TOOLS = frozenset({
    "web_fetch", "study_url", "github_read_file", "github_list",
    "verify_feed", "read_findings", "read_self", "read_taste",
})
_HEAL_TOOLS = frozenset({"reregister_webhook", "refetch_signals"})

_PHANTOM_CLAIM_RE = re.compile(
    r"captured your idea"
    r"|putting (a|the|your) project together"
    r"|(sending|sent|drafting|building|shaping)\b[^.!?\n]{0,40}\b(proposal|project)\b",
    re.IGNORECASE)
# "I'm opening a PR", "I'll open the PR", "I put up a PR", "the PR is up". The
# first-person lead (I/we/let me) is what separates an action CLAIM from PR
# workflow ADVICE ("opening a PR for review involves..."), which must NOT trip.
_PR_CLAIM_RE = re.compile(
    r"\b(?:I'?m|I'?ve|I'?ll|I|we|let me|lemme)\b[^.!?\n]{0,18}"
    r"\b(?:open(?:ed|ing)?|draft(?:ed|ing)?|submit(?:ted|ting)?|rais(?:e|ed|ing)"
    r"|creat(?:e|ed|ing)|put(?:ting)? up)\b[^.!?\n]{0,30}\b(?:PR|pull request)\b"
    # Softer verbs (write/try/wrap up) count as a PR CLAIM only when the PR is the
    # near-direct object -- "I'll write the PR", "let me try the PR" -- NOT when it's
    # mentioned downstream ("I'll try to explain the PR", "write you a summary of the
    # PR"), which is honest talk, not a claim. Tight object gap, no {0,30} reach.
    r"|\b(?:I'?m|I'?ve|I'?ll|I|we|let me|lemme)\b[^.!?\n]{0,18}"
    r"\b(?:writ(?:e|ing|ten)|tr(?:y|ying)|wrap(?:ped|ping)? up)\b"
    r"\s+(?:the |a |this |that |my |your |our )?(?:PR|pull request)\b"
    r"|\b(?:PR|pull request)\b[^.!?\n]{0,15}\b(?:is now|is|has been)\s+"
    r"(?:open|opened|ready|drafted|submitted|up|live)\b"
    # PR used as a verb on a specific object: "I'll PR it", "I'm gonna PR this".
    # Object is a pronoun (it/this/that/them/those) -- NOT bare "a"/"the"/"in",
    # which over-match prose like "we PR the changes via the dashboard".
    r"|\b(?:I'?m|I'?ve|I'?ll|I|we|let me|lemme)\b[^.!?\n]{0,20}"
    r"\bPR(?:'?d|ing)?\b\s+(?:it|this|that|them|those)\b"
    # Writing a repo file is a propose_change action: "I'll add the feed to
    # feeds.json", "I'm editing feeds.json". (feeds live in the repo.)
    r"|\b(?:I'?m|I'?ve|I'?ll|I|we|let me|lemme)\b[^.!?\n]{0,35}"
    r"\b(?:add(?:ed|ing)?|edit(?:ed|ing)?|updat(?:e|ed|ing)?|commit(?:ted|ting)?"
    r"|writ(?:e|ing)|stag(?:e|ed|ing)|push(?:ed|ing)?|put(?:ting)?)\b"
    r"[^.!?\n]{0,40}\bfeeds\.json\b",
    re.IGNORECASE)
# "I read/checked/fetched the link", "I looked it up", "the page says", "per URL".
_LINK_READ_CLAIM_RE = re.compile(
    r"\bI(?:'ve| have)?\s+(?:just\s+)?(?:read|checked|looked at|reviewed|fetched"
    r"|pulled|opened|visited|skimmed)\b[^.!?\n]{0,40}\b(?:link|page|article|url"
    r"|site|repo|file|docs?|post|paper|release)\b"
    r"|\bI(?:'ve| have)?\s+(?:just\s+)?(?:looked it up|looked up|checked the latest"
    r"|did some digging|dug into it|searched (?:for|online))\b"
    r"|\bthe (?:page|article|link|docs?|file|repo|post|paper|release)\b"
    r"[^.!?\n]{0,20}\b(?:says?|shows?|states?|reads?|confirms?|mentions?)\b"
    r"|\b(?:per|according to)\s+(?:https?://|the\s)",
    re.IGNORECASE)
# Model typing the CONFIRM ritual itself ("reply CONFIRM").
_CONFIRM_PROMPT_RE = re.compile(
    r"\b(?:reply|say|send|type|respond(?: with)?|hit)\b[^.!?\n]{0,15}\bCONFIRM\b",
    re.IGNORECASE)
# An action mentioned AFTER one of these is being OFFERED, not claimed ("I can
# open a PR if you want"), or honestly declined ("I can't open a PR").
_NONCOMMITTAL_RE = re.compile(
    r"\b(?:can|can't|cannot|could|would|should|may|might|able to|happy to"
    r"|want me to|do you want|shall i|if you)\b",
    re.IGNORECASE)

# What classify_claim hands back about an unbacked claim. reattemptable=True means
# the action is doable in one turn (PR/CONFIRM), so _generate_reply re-prompts the
# model with gap_note before suppressing; else replacement is sent as-is.
_Violation = namedtuple(
    "_Violation", "reattemptable replacement gap_note incident_kind incident_sig")

_PROJECT_NUDGE = ("hang on, I didn't actually build anything yet. say 'give me a "
                  "proposal' and I'll ship one for real.")
_PR_NUDGE = ("correction: I haven't actually opened a PR -- no propose_change ran. "
             "Say 'propose it' and I'll open a real one for you to merge.")
_READ_NUDGE = ("correction: I didn't actually fetch that -- no read tool ran, so I "
               "won't pretend I saw it. Want me to pull it up for real?")
_CONFIRM_NUDGE = ("correction: there's nothing staged behind a CONFIRM. Tell me "
                  "what to do (e.g. 'reregister webhook') and I'll stage it for real.")

_PR_GAP = ("\n\n[system note: you said you'd open a PR but no propose_change fired "
           "this turn. Call propose_change now, or state the EXACT blocker (e.g. a "
           "missing ARGO_PROPOSE_TOKEN, via check_config). Do NOT repeat the claim "
           "without calling the tool.]")
_CONFIRM_GAP = ("\n\n[system note: you told the user to reply CONFIRM but you "
                "staged nothing (no reregister_webhook/refetch_signals call). Call "
                "the heal tool now so there's something behind CONFIRM, or don't "
                "ask for CONFIRM.]")


def claim_unbacked(claim_re, reply):
    """True if claim_re matches reply somewhere the match span carries NO
    non-committal marker, so an offer/decline ('I can open a PR if you want',
    'I can't open a PR') doesn't count. The window spans the match itself (the
    claim is first-person-anchored, so 'can'/'could'/etc. sit inside it) plus a
    few leading chars for a marker that immediately precedes the subject."""
    for m in claim_re.finditer(reply):
        window = reply[max(0, m.start() - 4):m.end()]
        if not _NONCOMMITTAL_RE.search(window):
            return True
    return False


def pr_blocker(mcp_servers):
    """A concrete reason propose_change cannot open a PR right now, or None if the
    config looks complete. Lets an unbacked PR claim name the real blocker ('repo
    still points at the placeholder') instead of the generic 'say propose it', and
    lets classify_claim skip a pointless re-attempt -- re-prompting the model can't
    conjure a missing token. Reads os.environ fresh (not the import-time constants in
    argo_mcp_server) so a late-set var is seen. `mcp_servers` is forwarded by the
    webhook wrapper from its own MCP_SERVERS global (the patch point tests use)."""
    if mcp_servers is None:
        return ("no MCP server is wired (WEBHOOK_URL / ARGO_MCP_TOKEN unset), so I "
                "can't run propose_change at all")
    if not os.environ.get("ARGO_PROPOSE_TOKEN"):
        return "ARGO_PROPOSE_TOKEN isn't set, so I can't push a branch to GitHub"
    repo = os.environ.get("ARGO_PROPOSE_REPO", "")
    # "your-org/your-repo" mirrors the PROPOSE_REPO default in argo_mcp_server.py --
    # keep the two in sync. Not imported: argo_mcp_server is faked in tests and lazily
    # imported elsewhere to avoid a cycle, so a shared constant isn't worth it.
    if not repo or repo == "your-org/your-repo":
        return ("ARGO_PROPOSE_REPO still points at the placeholder repo, so there's "
                "nowhere to open the PR")
    return None


def classify_claim(reply, tool_events, mcp_servers):
    """Return a _Violation if the reply makes an action-claim no tool in tool_events
    backs, else None. Ordered: project -> PR -> link/read -> CONFIRM. `mcp_servers`
    is forwarded by the webhook wrapper from its own MCP_SERVERS global."""
    fired = set(tool_events)
    if not (fired & _PROJECT_TOOLS) and _PHANTOM_CLAIM_RE.search(reply):
        return _Violation(False, _PROJECT_NUDGE, None, "phantom_send",
                          "reply claimed a proposal but no project tool fired")
    if not (fired & _PR_TOOLS) and claim_unbacked(_PR_CLAIM_RE, reply):
        blocker = pr_blocker(mcp_servers)
        if blocker is None:
            # Config is fine -- the model just didn't call the tool. Worth one
            # re-attempt to make it actually fire propose_change.
            return _Violation(True, _PR_NUDGE, _PR_GAP, "phantom_claim",
                              "reply narrated a PR but propose_change never fired")
        # A hard config blocker: re-prompting can't fix it, so don't retry --
        # replace the bluff with the honest, specific reason so the user can act.
        return _Violation(False,
                          f"correction: I haven't opened a PR and can't right now -- "
                          f"{blocker}. Flagging the config so you can fix it.",
                          None, "phantom_claim",
                          "reply narrated a PR but propose_change is misconfigured")
    if not (fired & _LINK_READ_TOOLS) and claim_unbacked(_LINK_READ_CLAIM_RE, reply):
        return _Violation(False, _READ_NUDGE, None, "phantom_claim",
                          "reply claimed it read a source but no read tool fired")
    if _CONFIRM_PROMPT_RE.search(reply) and not (fired & _HEAL_TOOLS):
        staged = None
        try:  # late import + swallow: observability never breaks a chat turn
            import argo_mcp_server
            staged = argo_mcp_server.pending_heal_action()
        except Exception:
            staged = None
        if staged is None:
            return _Violation(True, _CONFIRM_NUDGE, _CONFIRM_GAP, "phantom_claim",
                              "reply asked the user to reply CONFIRM with nothing staged")
    return None


def guard_phantom_send(reply, tool_events, mcp_servers, note_incident, log):
    """Terminal claim<->receipt backstop: if the reply makes an action-claim no tool
    backs (opened a PR / read a link / asked for CONFIRM with nothing staged),
    replace the false claim with an honest correction and log it. _generate_reply
    re-prompts the doable classes (PR/CONFIRM) once before this fires; the
    deterministic routes (FIX/EVOLVE/CONFIRM gates) open real PRs and never pass
    through here. `mcp_servers`, `note_incident`, and `log` are forwarded by the
    webhook wrapper (its MCP_SERVERS global, _note_incident, and module logger)."""
    v = classify_claim(reply, tool_events, mcp_servers)
    if v is None:
        return reply
    log.warning("phantom claim suppressed: %s (events=%s)",
                v.incident_sig, tool_events or "none")
    note_incident(v.incident_kind, v.incident_sig,
                  f"events={tool_events or 'none'}; reply={reply[:120]}")
    return v.replacement


# --- URL-before-fetch gate -------------------------------------------------
# classify_claim only fires when the REPLY claims it read something; a reply
# that silently answers ABOUT a link from priors passes it. This gate keys off
# the USER's message instead -- the code enforcement of Argo's most-logged
# chat_weakness (self-flagged 4x: it composed replies about a URL before the
# fetch path ever ran, so the guardrail existed only as a stated intention).
_URL_IN_MSG_RE = re.compile(r"https?://[^\s)>\]]+")

_URL_FETCH_GAP = (
    "\n\n[system note: the user's message contains a URL but no read tool ran "
    "this turn. Redo the reply: either call web_fetch/study_url on that URL now "
    "and answer from what it actually returns, or state plainly that you have "
    "not opened the link and answer only from what the user themselves wrote. "
    "Never describe or summarize a link you did not fetch.]")


def url_fetch_gap(user_text, tool_events):
    """Return the gap note for ONE forced re-attempt when the user's turn
    contains a URL and no read-family tool fired; None when the gate passes
    (no URL, or a read receipt exists)."""
    if not isinstance(user_text, str) or not _URL_IN_MSG_RE.search(user_text):
        return None
    if set(tool_events) & _LINK_READ_TOOLS:
        return None
    return _URL_FETCH_GAP
