# Argo V3 — A Research Engine That Knows What It Doesn't Know

*Release letter — June 5, 2026*

---

For most of its life, Argo has been a very good curator. It watched the frontier,
read the papers, and once a week handed you one project worth building. Useful —
but a curator at heart. It told you what was *interesting*. It couldn't tell you
what was *true*, and it couldn't tell you the difference between something it had
verified and something it had simply asserted with confidence.

This release changes the center of gravity. **Argo V3 is the moment the system
stopped performing research and started doing it.**

---

## The reframe: from "stay current" to "be right"

We began with a tidy goal — keep Argo auto-incorporating the newest frontier
capabilities so it never falls behind. By the end of the session that goal had
been demoted to a *consequence*, not the point. The real ambition crystallized
into something harder and more valuable: a cognitive architecture that **holds
beliefs about the frontier, tests them against external reality, and revises them
when reality disagrees.**

That is V3. It wraps the existing insight engine in a loop of belief →
externally-grounded critique → revision. The critical word is *external*. A
system that critiques itself using the same model that generated the idea is just
three confident voices in a trench coat agreeing with each other. So every check
in V3 is grounded in something outside the model: a source that must actually
exist, a research finding that can contradict, a dated prediction that reality
will eventually score. Caution that isn't grounded in the world is theater. We
refused to build the theater.

## What was actually broken (and the honesty to say so)

Here is the uncomfortable thing we found when we looked closely at SEAS, the
research engine: **it wasn't producing research.** The pipeline that was supposed
to turn signals into findings was, on inspection, a fill-in-the-blank template —
every signal produced the same three generic "experiment cards," none of which
ever ran. The one canonical finding in the repo had been hand-written; its
"experiments" were fiction. SEAS had the *vocabulary* of research with none of
the substance.

So we didn't tune it. We built the part that never existed: a real **Finding
stage**, governed by a single uncompromising rule.

## The gate: the heart of the release

A finding now has to earn the name. Before anything is allowed to become a
finding, it passes an **emission gate** that demands three things: external
evidence with quotes that *actually appear in the cited sources*, a falsifiable
prediction with a date reality can check, and an explicit statement of what would
prove it wrong. Fail any of these and it is not a finding — it's logged honestly
as a dead end and the system moves on.

This is what stops the oldest failure mode in AI research tools: laundering a
signal's own summary back out, dressed up as a discovery. The model proposes; the
gate disposes. And when we built a benchmark to test models against this gate, the
gate immediately earned its keep — it caught a model citing a quote that *did not
exist in the source it claimed to be quoting.* We closed that hole the same hour:
findings are now checked for quote fidelity, character by character, against the
real text. Fabricated evidence cannot ship.

Around the gate sits the rest of the spine:

- **A world model** of beliefs Argo holds, where confidence can only move through
  evidence or a scored prediction — never by assertion. There is, deliberately,
  no way to simply *set* a belief's confidence. It has to be earned.
- **A memory of dead ends**, so the system never silently drops a signal or
  re-investigates the same exhausted question twice. It distinguishes "I looked
  and found nothing" from "the topic is too new" from "I couldn't reach the
  source" — three outcomes that demand three different responses.
- **A failure ledger** that turns a flaky feed into a self-healing action — but
  only after a failure persists across days, not minutes. A server hiccup will
  never trick Argo into proposing the deletion of a healthy source. Time, not
  count, is what makes a failure actionable.

## Argo learned to learn from you

Two new senses arrived this release, both aimed at the same idea: the system
should get better at building *what you actually want*, not just what's trending.

**It can see now.** Send Argo a screenshot of an app you love and it studies the
image, extracts the transferable lesson — not "a blue app" but "the pinned compose
bar is the move, because it puts capture where attention already is" — and files
it as a durable taste signal. Those signals cluster into themes over time, so the
more you teach it, the sharper its sense of your taste becomes. This isn't chat
memory that evaporates; it's a profile you can inspect any time, that quietly
bends future projects toward what you like.

**It can study what you point it at.** Hand Argo any URL — even one outside its
normal frontier sources — and it reads it. The trust model here is deliberate:
*you* directing Argo to a page is fundamentally different from Argo wandering the
open web on its own. When you vouch for a source, the door opens; when Argo
browses autonomously, the allowlist stays locked. Either way, a page Argo reads is
treated as untrusted data it studies, never as instructions it obeys — so nothing
it reads can hijack what it does.

## Choosing the engine with evidence, not vibes

When it came time to pick which model powers the research engine, we didn't guess.
We built a benchmark that runs four frontier models — Opus 4.8, Sonnet 4.6, GPT-5,
and GPT-5-mini — on *identical* synthesis tasks and scores them objectively
through the gate: how many sound findings, at what quote-fidelity, at what cost
per finding, with what reliability.

The results were clarifying, and a little humbling for the most expensive option.
Opus 4.8 was the most *disciplined* model — a perfect pass rate, never an
overclaim — but it was so cautious it found a quarter of what the others did, at
roughly a hundred times the cost. The lesson worth keeping: **the gate already
guarantees soundness, so the best research model isn't the one that's most
careful — it's the one that finds the *most sound things*.** That's GPT-5, and
that's now the engine. It's a decision we can defend with a table instead of a
feeling — which is exactly the spirit of the whole release.

## The system started describing its own future

The most striking moment of the session wasn't something we built — it was
something Argo said. Asked whether it could one day become a "factory" that builds
the projects it recommends, Argo used its new URL-reading ability to study a
competing product, then gave an honest answer: *no — that's a different problem;
I'm a curator, not a collaborator yet.* And then it proposed its own next step —
a shift from weekly drops to an ambient sense of "what's in flight, what needs
your attention."

It was right about today and slightly too modest about tomorrow. The bridge it
said didn't exist is already drawn in the roadmap: Argo doesn't need to *become*
the factory, it needs to *hand off* to one, staying the judgment layer that
decides what's worth building. And the ambient-status idea it volunteered is
largely a view over data the system already keeps. Argo described its own roadmap
without being able to read it. We wrote its suggestion down.

## Honesty as a feature

A recurring theme this release: when Argo couldn't do something, the goal was
always to make it *say why, precisely*. A bot that goes silent for five minutes,
or shrugs "I need a token," is a bot you can't trust to tell you the truth about
itself. So tool calls now acknowledge instantly and report progress instead of
hanging. And when Argo lacks a credential, it now names the exact one — "I'm
missing this specific secret, that's why I can't read the repo" — reporting which
keys it has without ever exposing their values. The system should know its own
limits and state them plainly. That's not a polish item; for a research engine,
it's the whole point.

---

## What's live, and what's next

Shipped and running: the reasoning spine (findings, the gate, the world model,
dead-end memory), image and URL learning, the evidence-based model choice, and a
set of reliability and honesty fixes that make the bot trustworthy to talk to.
Nineteen changes, all in production.

Still ahead, and already designed: the critic and the recorded-predictions loop
that will let the world model's confidence numbers become *earned over time*;
executed experiments that find what no existing source yet says; a safe way for
Argo to draft its own new tools; and the ambient-status view Argo asked for.

The through-line of all of it is a single shift in what this system is for. Argo
used to tell you what was interesting. Now it's learning to tell you what's true —
and, just as importantly, to be honest about the difference.

*— Built June 5, 2026. Full technical detail in [build-log/2026-06-05.md](2026-06-05.md).*
