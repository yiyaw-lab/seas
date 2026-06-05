# Argo V3 — From Curating the Frontier to Knowing What's True

*A note to investors and partners — June 5, 2026*

---

## The short version

There is no shortage of tools that summarize AI research. Argo is becoming
something rarer: a system that doesn't just tell you what's *interesting* on the
frontier, but builds a verifiable, compounding model of what's *true* — grounded
in real evidence, accountable to its own predictions, and increasingly tuned to
one person's taste and judgment.

This release, V3, is the inflection point. We moved Argo from a weekly curator to
a research engine with a strict standard for what counts as a finding — and, in
the process, laid the foundation for the part that's genuinely defensible: a body
of proprietary, accumulating knowledge that gets sharper and harder to replicate
every week it runs.

---

## The problem we're actually solving

Frontier AI moves faster than any individual builder can track, let alone judge.
The market's answer so far is summarization — newsletters, digests, "here's what
shipped this week." That's a commodity, and large models already do it for free.

The unmet need isn't *more information*. It's **judgment that compounds**: a
system that knows what you care about, separates signal you can act on from noise
that merely sounds important, holds a defensible point of view on where the
frontier is going, and gets *more* useful the longer it works for you — instead
of resetting to zero every session like a chatbot.

Argo is built to be that judgment layer. V3 is where it stopped approximating the
job and started doing it.

## What we shipped: a strict standard for genuine findings

The core of this release is a deliberate, high bar for what Argo is allowed to
call a "finding." It is no longer enough for the system to produce something that
*sounds* like a research insight. Before any claim becomes a finding, it must pass
a gate that requires three things:

- **Real, cited evidence** — with quotes verified, word for word, against the
  actual source. Fabricated or paraphrased evidence is rejected automatically.
- **A falsifiable prediction with a date** — a claim about how the world will go,
  that reality can later score.
- **An explicit statement of what would prove it wrong.**

If a claim can't clear that bar, it isn't dressed up and shipped anyway — it's
logged honestly as an open question, and the system moves on. This discipline is
the moat in miniature: most tools in this space optimize for output volume; we
optimize for output you can *trust and act on*. A team that refuses to call
plausible-sounding text "research" produces a fundamentally different — and more
valuable — asset over time.

Around that standard we built the rest of the engine: a **world model** of beliefs
whose confidence can only rise through evidence or a correct prediction (never by
assertion), a **memory of dead ends** so the system never re-runs exhausted
questions or chases a temporary outage, and full provenance on every finding —
the claim, the sources it stands on, and the prediction it will be judged by.

## The defensible part: a data flywheel, not a model

Models are a commodity that improves for everyone at once. Our defensibility isn't
in the model — it's in what accumulates *around* it, which no competitor can copy
because it's specific to you:

- **A verified belief graph** about the frontier that deepens with every run, each
  belief tied to evidence and accountable to a dated prediction.
- **A taste profile** learned directly from what you flag, build, and react to.
  This release taught Argo to *see* — send it a screenshot of a product you admire
  and it extracts the transferable lesson (not "a nice app," but the specific
  pattern worth stealing and why), filing it as durable taste that bends future
  recommendations toward what you actually want. These signals cluster into themes
  over time, so the profile sharpens the more you use it.
- **An energy signal** — your 1-to-10 reaction to each project — that, accumulated,
  teaches the system not just what's true but what's worth *your* effort.

Individually these are features. Together they are a compounding, proprietary
dataset: the longer Argo runs for a given builder, the better it gets and the more
expensive it becomes to leave. That flywheel is the asset.

## Traction: the autonomous loop already works in production

The clearest evidence that the thesis is real isn't a metric — it's a behavior we
*observed*, not scripted. While we were building this release, the live system, on
its own, identified gaps in its own frontier coverage, drafted the changes to fix
them as proper pull requests, and surfaced them for review. We merged them. Argo
extended its own capabilities through a safe, human-gated loop — unprompted, in
production.

That is the whole product thesis in one event: a system that improves itself
within boundaries a human controls. The architecture for "Argo proposes, a human
approves, the system gets better" is not a roadmap item. It's running today.

We also made the choice of which frontier model powers the engine an **evidence-
based decision, not a preference.** We built a benchmark that runs four leading
models on identical tasks and scores them objectively — quality, cost per result,
and reliability. The most expensive option was the most cautious but found a
fraction of what cheaper models found, at orders of magnitude more cost; we
selected the model with the best *sound-findings-per-dollar*, and we can defend it
with data. That instinct — measure, don't assume — is how the whole system is run.

## For partners: judgment layer, not walled garden

Argo is deliberately *not* trying to be everything. It is the layer that decides
**what is worth building and why** — stress-tested against its own world model. It
is explicitly designed to hand off the *building* to a dedicated execution
environment rather than reinvent one.

That makes Argo a natural complement to build-and-orchestration platforms rather
than a competitor: we bring the verified judgment about what to build and the
deep, accumulated context on the person it's for; a build partner brings the
persistent environment where the work gets done. The handoff boundary is clean and
intentional. If you operate the layer that turns a decision into an artifact, we're
the layer that makes that decision worth executing.

## Where this is going

Live today: the research engine with its evidence standard, a self-improving loop
proven in production, the system's first senses (it can see images and study any
source you point it at), and an honesty discipline throughout — when Argo can't do
something, it now tells you precisely why and what to fix, rather than failing
silently.

Next, and already designed: predictions that get scored against reality so the
system's confidence becomes *earned over time*; the ability to run real
experiments that surface what no existing source yet says; a safe path for Argo to
extend its own toolset; and — an idea the system itself proposed after studying a
competitor — an ambient, always-current view of what's in flight and what needs
your attention, replacing the weekly cadence.

The arc is simple to state and hard to build: Argo used to tell you what was
interesting. It's now learning to tell you what's true, to prove it, and to get
measurably better at it the longer it works for you. That compounding is the
business.

*— June 5, 2026. Technical detail in [build-log/2026-06-05.md](2026-06-05.md).*
