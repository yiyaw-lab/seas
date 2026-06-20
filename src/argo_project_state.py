"""Project-state lookup helpers — "which project does this refer to?"

These three functions resolve a rating / SELECT / SHIPPED / paste against the
project log and return the project it points at. They are pure project IDENTITY:
no rating is written, no prediction is armed, no Telegram is touched. The rating
ACTIONS that mutate the log (record_rating, select_latest_project,
set_project_outcome) and their prediction arming live in argo_rating, which
imports the targeting helpers from here. argo_webhook keeps thin wrappers that
forward its own PROJECTS_LOG global so the tests that patch wh.PROJECTS_LOG still
drive behavior. Stdlib + argo_store for the on-disk format.
"""

import argo_store


def target_project(log, project_id=None):
    """The project a bare rating/SELECT refers to: an explicit id if given, else
    the one most recently SHOWN to Yiya (delivered, marked shown_at), else the
    last in the log. Using 'last shown' not 'last generated' keeps a rating/SELECT
    attached to the project she's actually looking at, even if a newer one was
    generated after."""
    if project_id:
        return next((p for p in log if p.get("id") == project_id), None)
    shown = [p for p in log if p.get("shown_at")]
    if shown:
        return max(shown, key=lambda p: p["shown_at"])
    return log[-1] if log else None


def target_outcome_project(log, project_id=None):
    """The bet a SHIPPED/DROPPED grades: an explicit id if given, else the most
    recently SELECTED project -- the committed bet whose judgment prediction is
    armed. NOT 'last shown': showing a new candidate after a SELECT must not steal
    the outcome of a bet already in flight (that would grade the wrong belief). Ties
    on the minute-resolution selected_at break toward the later log entry (the more
    recent SELECT). Returns None when nothing has been selected."""
    if project_id:
        return next((p for p in log if p.get("id") == project_id), None)
    selected = [(i, p) for i, p in enumerate(log) if p.get("selected_at")]
    if selected:
        return max(selected, key=lambda ip: (ip[1]["selected_at"], ip[0]))[1]
    return None


def match_existing_project(text, projects_log):
    """If `text` looks like a paste of an EXISTING logged project (its pitch or a
    chunk of its body), return that project; else None. Stops a paste of a project
    Argo already sent from being misread as a brand-new idea (add_project)."""
    t = " ".join((text or "").split()).lower()
    if len(t) < 25:  # too short to confidently match; let the LLM handle it
        return None
    log = argo_store.load_json(projects_log, None)
    if not log:
        return None
    for p in reversed(log):  # prefer the most recent match
        body = " ".join(p.get("text", "").split()).lower()
        if not body:
            continue
        # Match if the pasted text is contained in the project (a paste of part of
        # it), or the project's distinctive first line is contained in the paste.
        first_line = body.split(".")[0]
        if (t in body) or (len(first_line) >= 25 and first_line in t):
            return p
    return None
