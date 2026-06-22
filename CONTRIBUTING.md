# Contributing

This is a source-available research project (see [LICENSE](LICENSE)). The bar for
a change to land is not "it looks right" — it is **verified, then adversarially
reviewed, then reviewed again until clean**. The discipline below is the actual
process every change ships through, including the ones Argo drafts for itself.

## The verify-before-merge discipline

**1. Fail-first tests (negative control).** A change that fixes a bug or adds
behavior ships with a test that *fails before the change and passes after it*. The
failing run is the proof the test actually exercises the new path — a test that
was green before it was written verifies nothing. The four recurring regression
areas (scheduler firing/grace/dedupe, seen-store dedup + legacy migration, the
rating prompt, project re-anchoring) each carry such a test, and any fix in those
areas must extend one.

Tests are pure: no network, no LLM, no real `data/*.json`. They override the
module-level path constants to a temp dir. Run them with:

```
PYTHONPATH=src python3 -m unittest discover -s tests
```

under `python3` (3.11), not the 3.9 `.venv`. The same suite runs on every push and
PR via the [`Tests`](.github/workflows/tests.yml) workflow.

**2. Fresh-context adversarial reviewer.** Before a change is proposed, a reviewer
with a clean context — no memory of why the code was written the way it was — is
prompted to *refute* it: to find the failure path, the state-machine edge, the
concurrency hole, the missing guard. Reviewing your own diff with the intent it
should pass is not this; the reviewer's job is to break it.

**3. Iterated review until a round is clean.** Pull requests go through iterated
[Cursor Bugbot](https://cursor.com/bugbot) review. Each round can surface a bug the
previous round's *fix* introduced, so review continues until a full round finds
nothing new. "Looks fine to me" after one pass is not the gate; an empty review
round is.

## Working conventions

- **Stdlib-first.** Python 3.11. Only the deps already in `requirements.txt`. Don't
  add a dependency without a reason; the core (gate, world model, probes) is pure
  standard library.
- **Surgical changes.** Touch only what the task requires; match existing style;
  every changed line should trace to the change. Compile what you touch
  (`python3 -m py_compile <files>`).
- **Additive, per-file commits.** Multiple agents and people edit this repo; stage
  with explicit `git add <path>`, never `git add -A`. Don't rewrite shared history.
- **Security stays server-side.** Web/repo access is allowlist-gated in code (never
  trust the model to self-limit); the `/mcp` endpoint is bearer-authed; secrets come
  from env and are never committed. See [SECURITY.md](SECURITY.md) to report issues.

## The human merge gate

The generator, the reviewer, and the merger are kept as three separate roles. Argo
can open a PR for a fix or an upgrade to itself; it never merges one. A human
reviews and merges. That gate is the safety boundary, and it does not move.
