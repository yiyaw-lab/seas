# ARGO Architecture

> **The product is one project, not a report.**
>
> SEAS generates knowledge. Argo generates motion.
> Argo is not an AI agent — it is a **decision engine**.

Date: 2026-06-02 (rev. 2 — incorporates Yiya's refinements)
Status: Design doc. No code, no file moves, no refactor.

## The Split

SEAS and Argo were conflated. They are different machines with different jobs.

| | **SEAS** | **Argo** |
|---|---|---|
| Type | Research engine | Frontier scout |
| Asks | "What is true?" | "What should Yiya do next?" |
| Loop | Signal → Opportunity → Experiment → Finding → Theory | Observe → Interpret → Select → Recommend → Reflect |
| Purpose | Generate knowledge | Generate action |
| Output | Findings and theories | One project worth building |
| Optimizes for | (it should be rigorous) | action, excitement, momentum, frontier relevance |
| Time horizon | Open-ended | This week |

**SEAS produces understanding. Argo produces a decision.** Argo is downstream of SEAS: it treats SEAS's knowledge as raw material and converts it into a single Monday-morning move Yiya is excited to make.

Everything below is designed around that distinction. When a choice trades research completeness for momentum, Argo picks momentum.

---

## 1. Argo's Loop

```
Observe → Interpret → Select → Recommend → Reflect
            ↑                                   │
            └───────────────────────────────────┘
```

- **Observe** — pull the current state of the world Argo can act on: SEAS findings/theories/opportunities, plus raw frontier signals, plus Yiya's recent reflections and what she's already built. Argo does not generate signals; it scouts them.
- **Interpret** — read each candidate through one lens: *"Is this a project Yiya could ship this week that would make her feel like a frontier builder?"* Not "is this true" — "is this exciting and buildable."
- **Select** — pick exactly **one** Recommendation. Argo's superpower is refusal: it kills the other good ideas so Yiya isn't paralyzed. A ranked list is a failure mode; a single confident bet is the product.
- **Recommend** — deliver the one project as a Telegram message Yiya can act on immediately: title, why now, what to build this week, artifact, 3-step plan, why it builds frontier capability. (This is exactly `demo/weekly_project_message.md`.)
- **Reflect** — capture Yiya's reaction (excited / shipped / skipped / "give me another") and feed it back into Interpret/Select so next week's pick is better calibrated to what actually moves her.

The loop's job is to **end in an action**, not a conclusion.

---

## 1a. `/another` — Recommendation Search (first-class concept)

`/another` is not just a command. It is the mechanism that defines what Argo
*is*. It transforms a single **Recommendation** into a **Recommendation Search**.

The shift it forces:

> Argo is **not** trying to find the objectively best project.
> Argo is trying to find **the project Yiya is most likely to build.**

These are different objectives. The first is a ranking problem (SEAS-like, "what
is true/best"). The second is a *search over Yiya's energy* — and the only way
to search it is to propose, observe the reaction, and propose again. `/another`
is that search step made explicit.

Consequences for the architecture:
- The Select step is a **search**, not a sort. Each `/another` is one probe.
- A Bet must always feel **non-final**. The moment a recommendation feels
  forced, the search collapses and momentum dies.
- Every `/another` is a labeled training example: "this bet did not have enough
  energy *this week*." That's the richest signal Argo gets.

`/another` is sacred. It is the difference between a decision engine and a
to-do list.

---

## 2. What Argo Should CONSUME from SEAS

Argo eats SEAS's *outputs* and *near-outputs* — the things that point toward something buildable:

- **Opportunities** — "a frontier opening that can be converted into an artifact-producing experiment." This is the highest-value SEAS object for Argo; it's already framed as buildable.
- **Findings** — fresh evidence (e.g. F-001). A finding with "Confidence: Low / only 2 signals tested" is *fuel*: the obvious next action is "go produce signal 3." Open questions in a finding are project seeds.
- **Theories** — but only their **open questions and predictions**, not the claim itself. A theory's untested prediction is a buildable experiment; the polished claim is not.
- **Signals** — the raw "something changed in the world," for *why now* and *frontier relevance*. Argo needs to know what's new to make a project feel timely.

Rule of thumb: Argo consumes anything that answers *"what's the next move?"* — openings, gaps, fresh evidence, untested predictions.

---

## 3. What Argo Should IGNORE from SEAS

Argo deliberately does **not** look at:

- **Scoring internals** — weights, `durability/leverage/alignment` numbers, weighted_score math. That's SEAS's epistemics. Argo cares whether something is *exciting and buildable*, not whether it scored 4.8.
- **Settled / high-confidence theories** — a "done" theory generates no action. Argo wants the frontier edge, not the consolidated core.
- **Research methodology** — falsification criteria, scoring rubrics, raw experiment transcripts, comparison tables. Evidence-for-truth is SEAS's concern.
- **Capability-engine and superseded-generation artifacts** — anything in (eventually) `archive/`. Dead architecture is not a signal.
- **Completeness metadata** — "n=2," "tentative," "needs more trials." Argo *reframes* incompleteness as opportunity rather than treating it as a blocker.

Heuristic: if a SEAS field exists to make knowledge *rigorous*, Argo ignores it. If it exists to make knowledge *actionable*, Argo consumes it.

---

## 4. What Argo Should STORE

Minimal, action-oriented memory. Argo is not a database; it remembers just enough to get better at picking.

- **Bet log** — every Bet Argo placed (title, date, confidence, upside, reason, message text, source Opportunity/Finding).
- **Energy Score (1–10)** — captured after every Bet: *"How much do you want to build this?"* **This is Argo's primary optimization target** (see §8). Yiya's own reviews kept returning to *exciting / compelling / delight* — energy is the measurable form of that.
- **Reactions** — built / skipped / `/another` / silence, per Bet. Categorical signal alongside the numeric Energy Score.
- **Project + Artifact ledger** — what got built and the public artifact link (repo/post/demo). The Artifact is what compounds.
- **Cooldown / dedupe state** — what's been bet recently so Argo doesn't repeat itself or re-pitch a skipped idea unchanged.
- **Preference model (lightweight)** — running notes on what gives Yiya energy (topics, formats, ambition level), inferred from Energy Scores + reactions. Prose, not a model file.

Argo does **not** store findings, theories, or signals — those live in SEAS. Argo stores *its bets and their energy*.

---

## 5. Argo's Core Objects

The core Argo object is **not** a Recommendation — it is a **Bet**. Argo is
fundamentally *placing bets* under uncertainty, the way a frontier builder does.
A Bet carries confidence and upside; a "recommendation" hides them.

| Object | Owner | Definition | Argo's relationship |
|---|---|---|---|
| **Signal** | SEAS | Something that changed in the world. | Reads (for *why now*). Does not own. |
| **Opportunity** | SEAS | A frontier opening convertible into an artifact-producing experiment. | Reads as primary input. Does not own. |
| **Bet** | **Argo** | The single project Argo wagers Yiya should build this week — carrying confidence, potential upside, and reason. Delivered as the weekly message. | **Owns.** Argo's core object. |
| **Project** | **Argo** | A Bet Yiya accepted and is building. | **Owns.** Tracks momentum. |
| **Artifact** | **Argo** | The public output of a Project (repo / post / demo). **The thing that compounds** — reputation and ecosystem value. | **Owns.** The point of the whole loop. |
| **Review** | **Argo** | Yiya's reflection after the fact: reaction, **Energy Score (1–10)**, notes. | **Owns.** Closes the loop. |

### The Bet object

```
Bet:              Agent Organization Lab
Confidence:       Medium
Potential Upside: High
Reason:           Could reveal organizational laws of intelligence.
Energy (1–10):    (set by Yiya after delivery)
```

Confidence × Upside is the frontier-builder calculus: Argo will happily place a
Medium-confidence / High-upside bet. A "safe" recommendation that nobody's
excited to build is a worse bet than a risky one with high energy.

So: **SEAS owns Signal/Opportunity (and Finding/Theory). Argo owns Bet,
Project, Artifact, Review.** A Bet links *up* to the SEAS Opportunity/Finding it
came from, and *down* to the Project → Artifact → Review it produces. That link
is the SEAS↔Argo seam.

```
Signal ─┐
Opportunity ─┼─(SEAS)
Finding ─┘
        │  Argo wagers on these
        ▼
Bet ──► Project ──► Artifact ──► Review
        (Argo owns this chain — the Artifact is what compounds)
```

---

## 6. Telegram Commands — Argo V1

V1 launches with **exactly five commands**. They form the complete learning
loop; everything else is nice-to-have and deferred.

| Command | What it does |
|---|---|
| `/weekly` | Send this week's Bet now (the weekly message on demand). |
| `/another` | Place a *different* Bet from the same pool. First-class concept (§1a): turns the recommendation into a Recommendation Search. The killer feature. |
| `/why` | Argo sells the current Bet — *why now*, confidence, upside, why it builds frontier capability. The excitement pitch, expanded. |
| `/built <link>` | Mark the current Bet as shipped; attach the Artifact link. Records Project + Artifact, boosts the streak. |
| `/help` | List the five commands. |

The loop these five create:

```
Weekly Bet  →  Alternative Bet  →  Reasoning  →  Build  →  Feedback
 /weekly        /another           /why          /built     (Energy Score)
```

That is enough to learn from. No browsing, no lists, no config, no `/status`,
no `/skip` (silence *is* a skip). Argo is a coach, not a dashboard.

> Note: the Energy Score (1–10) is prompted automatically after a Bet is
> delivered/acted on — it's part of the Reflection step, not a typed command.

---

## 7. Commands That Belong in SEAS, Not Argo

These are *research/epistemic* actions — they ask "what is true?", so they're SEAS's, not Argo's:

- `/signal <x>` — add a raw signal to study. (Intake for the research engine.)
- `/finding` or `/theory` — query or record knowledge. (SEAS outputs.)
- `/evidence`, `/score`, `/experiment` — run or inspect the research loop.
- Anything that *enriches understanding* rather than *triggers a build*.

Litmus test: if the command's goal is to make Yiya *know* more, it's SEAS. If its goal is to make Yiya *do* something this week, it's Argo. `/another` is Argo (it changes the action); `/signal` is SEAS (it changes the knowledge base).

---

## 8. How Argo Improves Over Time

**Argo optimizes for energy.** The single target: the Energy Score (1–10) Yiya
gives each Bet, validated by whether she then builds. High energy that converts
to a shipped Artifact is the win condition.

- **Energy-weighted selection** — Bets resembling past high-Energy / `/built` ones get up-weighted; low-Energy and `/another`-rejected patterns get down-weighted. The preference model is accumulated Energy Scores.
- **`/another` as gradient** — each `/another` says "not enough energy *this week*." Frequent use means recalibrate Select (§1a). It's the core search signal.
- **Shipping streak as north-star metric** — consecutive weeks Yiya ships an Artifact. Momentum compounds; that's the whole game.
- **Freshness pressure** — penalize stale/repeated themes so Bets keep pulling from the frontier edge, not last month's interests.
- **Energy calibration** — note which *framings* (the "why now," confidence/upside framing, ambition level) produced high Energy Scores, and write future Bets in that register.

Argo does **not** improve by becoming more complete or more correct. It improves by placing higher-energy bets that convert to Artifacts.

---

## 9. What Happens After Yiya Receives a Weekly Recommendation

The Reflection step. The Bet is not the end — Yiya's reaction is the input to next week.

```
Monday  → Argo places this week's Bet (Telegram)
          ↓
Yiya reacts:
   ├─ excited / starts building → /built <link>  → Project + Artifact recorded, streak++
   ├─ "not this one"            → /another        → Argo places a different Bet, same week
   ├─ wants the pitch           → /why            → Argo sells it harder
   └─ silence                   → (soft skip)     → try a different register next week
          ↓
Argo asks: "How much do you want to build this? (1–10)"   ← Energy Score
          ↓
Argo writes a Review (Energy Score + reaction + notes)
          ↓
Review feeds the preference model → higher-energy Bet next week
```

The ideal terminal state of any week is a **shipped Artifact** (repo / post /
demo) — that's momentum, frontier signaling, reputation, and the strongest
training signal, all at once. The Artifact is the thing that compounds; a
Project without an Artifact is unfinished. Silence is a soft skip and nudges
Argo to try a different register next week.

---

## 10. The Architecture

```
            ┌─────────────────────────────────────────────┐
            │  SEAS  (research engine — "What is true?")    │
            │  Signal → Opportunity → Experiment            │
            │         → Finding → Theory                    │
            │  Output: findings, theories, OPPORTUNITIES    │
            └───────────────────┬─────────────────────────┘
                                 │  consumes opportunities,
                                 │  findings, open questions
                                 ▼
            ┌─────────────────────────────────────────────┐
            │  ARGO  (decision engine — "What next?")       │
            │  Observe → Interpret → Select → Recommend     │
            │           → Reflect                            │
            │  Output: ONE Bet (confidence × upside)        │
            └───────────────────┬─────────────────────────┘
                                 │  Telegram (weekly + on-demand)
                                 ▼
            ┌─────────────────────────────────────────────┐
            │  YIYA                                         │
            │  /weekly  /another  /why  /built  /help       │
            │  builds the Bet → ships an ARTIFACT           │
            └───────────────────┬─────────────────────────┘
                                 │  /built <link>  +  Energy Score (1–10)
                                 ▼
            ┌─────────────────────────────────────────────┐
            │  REFLECTION  (Review)                         │
            │  Energy Score + reaction + Artifact link      │
            └───────────────────┬─────────────────────────┘
                                 │  energy signal
                                 ▼
                         (back into ARGO's Select)
```

Object chain through the loop:

```
Opportunity ──► Bet ──► Project ──► Artifact ──► Review
   (SEAS)      └──────────── Argo ────────────────┘
```

Two nested loops:
- **Inner (Argo↔Yiya↔Reflection):** weekly, fast, optimized for action and momentum.
- **Outer (SEAS→Argo):** SEAS keeps generating knowledge; Argo keeps mining it for the next move.

SEAS makes the knowledge. Argo makes the call. Yiya makes the thing. Reflection makes Argo smarter.

---

## Design Reminders (do not drift)

- **The product is one project, not a report.** If Argo ever emits a ranked list, it has failed.
- Optimize for: **energy** (then action, momentum, frontier relevance). The Energy Score is the target.
- Do NOT optimize for: research completeness, theoretical purity, documentation.
- Argo is a **decision engine**, not an AI agent. It places **Bets** (confidence × upside), not recommendations.
- `/another` is sacred: it is **Recommendation Search**. Argo seeks the project Yiya is *most likely to build*, not the objectively best one. Every Bet must feel non-final.
- The **Artifact** is the win condition — it's what compounds (reputation, ecosystem value). A Project without an Artifact is unfinished.
- When in doubt: SEAS asks *"What is true?"* — Argo asks *"What should Yiya do next?"* SEAS generates knowledge; Argo generates motion.
