# Routing guard: when to call new_project

This doc captures the rule added to `build_system_prompt` in PR #7.
It lives here so reviewers can read it without wading into the full prompt string.

## Rule

Fire `new_project` ONLY on an explicit imperative from Yiya:

**YES -- generate a new project:**
- "new project"
- "give me a project" / "send me a project"
- "give me another" / "give me a different one"
- "fresh project" / "fresh pitch"
- "another one" (when clearly referring to wanting a new project suggestion)

**NO -- do NOT generate:**
- "my project does X" -- she's describing her own work
- "the project vault" -- discussing infra/tooling
- "re: that project" -- referencing an existing one
- "I'm working on a project" -- context-setting
- Any question that contains the word 'project' but isn't asking for a new one

**Re-show instead (get_latest_project):**
- "show me the project again"
- "where is it" / "what did you suggest"
- "the one you sent" / "that project"
- She pastes a pitch Argo sent earlier

**If unclear:** ask once -- "do you mean the one I just sent, or a new one?"
Only generate when intent is unambiguous.

## Why this matters

Calling `new_project` is destructive: it replaces the current project in the
store. A false trigger on a casual "project" mention throws away a project Yiya
may be mid-decision on.
