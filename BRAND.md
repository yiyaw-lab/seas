# Seasar — Brand & Design System

*The single source of truth for how Seasar looks, sounds, and is explained. Decks,
docs, site, and reports all draw from here. Built 2026-06-05. For what Seasar IS
and the built/unbuilt line, see VISION.md.*

---

## 1. Positioning — the one line

> **Tagline: From signal to shipped.**
>
> **Seasar is a frontier-building factory where judgment is the spine — agent
> engines explore, argue, build, and turn discoveries into shipped work.**

This is the canonical one-liner. The verbs matter: *explore* (scan the frontier),
*argue* (the adversarial critic/verification spine — claims are attacked, not
asserted; this is the load-bearing wall, not a feature), *build*, *ship*. It reads
mythic-and-cognitive (a forge with crews), not SaaS-pipeline.

**The enemy is the slop factory** — a factory that generates and ships with no
judgment between stages. Seasar is the same machine with opposite epistemics: a
gate at every handoff. *The factory that argues with itself before it ships.*

"From signal to shipped" is the recurring spine phrase — title, footer rail,
close. Three words that contain the whole factory.

Seasar is the **cognitive forge**: explore → argue → build → ship.

**The engines (Seasar is the umbrella):**

- **SEAS** — research engine. *What's true? What's worth building?* (live)
- **Rehearse** — debate/simulation engine. *Does the bet survive being stressed?*
  (the keystone; aspirational)
- **Argo** — build partner. *Turn the judged-worthy bet into shipped work.* (live)

Judgment is not an engine; it is the **connective tissue** between them.

**The pipeline (the product, the spine of everything):**

```
frontier signal → what's true → what's worth building → your project → built
```

Read the headlines of any Seasar deck top-to-bottom and you should get the whole
argument without the body copy. That is the test.

## 2. The inevitability thesis (why now / why this)

- Generation is becoming free; **verified judgment** and **proprietary taste data**
  are the scarce, durable layer.
- Every stage from idea to artifact is becoming automatable. Whoever assembles
  them into one pipeline owns the outcome.
- Four of five stages already run in production; the fifth (autonomous build) is
  the inevitable close — not claimed as shipped, claimed as inevitable.

## 3. Voice

Mythic but engineered. A ship's captain reading instruments, not a salesperson.

- **Declarative, not promotional.** State facts about where things go. "Generation
  is free. Judgment isn't." Not "We're revolutionizing…".
- **Concrete over poetic.** Lead with the plain thing, then let it resonate. Never
  vibe before substance — say what it *is* first.
- **Takeaway headlines.** Every slide headline is the conclusion, not a label.
  ❌ "The shift"  ✅ "Generation is free. Judgment isn't."
- **One idea per slide.** One headline, one focal point, one short caption.
- **Earned confidence.** Show the proof (it improves itself in production; the
  benchmark). Don't assert greatness — demonstrate rigor; rigor reads as trust.
- No AI-sparkle clichés ("unleash", "supercharge", "revolutionary"), no emoji in
  formal materials, no exclamation marks.

## 4. Color — "Black paper" (Conductor-aligned)

Near-black, white text, almost no color. Restraint IS the design. No gradients,
no glows, no atmospheric washes. The only "accent" is white-on-black contrast and
the occasional functional status dot. Decoration is the enemy.

```css
:root{
  --bg:#0a0a0a;          /* near-black base */
  --bg-raised:#0d0d0d;   /* barely-raised alt section */
  --ink:#fafafa;         /* primary text (near-white) */
  --ink-dim:#a8a8a8;     /* secondary text */
  --ink-mute:#6a6a6a;    /* captions, labels, muted */
  --line:#222222;        /* hairline */
  --line-hi:#333333;     /* slightly stronger hairline */
  --btn:#1c1c1c;         /* solid button surface */
  /* functional status only — used rarely, small */
  --live:#7fd1a0;        /* green: live / verified (like Conductor's cloud icons) */
  --designed:#7aa2f7;    /* blue: designed */
  --horizon:#6a6a6a;     /* dim: horizon */
}
```

**Rule:** black + white + grey hairlines carry ~98% of every screen. Status dots
are tiny and functional, never decorative. No brand accent color washes. If a
slide feels colorful, it's wrong.

## 5. Typography — mono hero, sans body (Conductor's signature)

The defining choice. Punchy/marketing/headline copy is **monospace** (large,
confident, generous line-height). Long-form reading body is a **clean neutral
sans**. Section labels are plain sans, calm.

- **Mono (hero, headlines, labels, data, pipeline):** `JetBrains Mono` — the
  commit-mono register. Used BIG for the hero statement, normal for labels.
- **Sans (body, reading paragraphs, section heads):** `Inter` (or system
  neutral) — 400 weight, generous line-height 1.55–1.7. Calm, legible.

**Scale (clamp, responsive):**
```css
--hero:clamp(1.6rem,3.6vw,2.9rem);   /* mono hero statement — big but not huge */
--h2:clamp(1.6rem,4vw,3rem);         /* sans takeaway headline */
--body:clamp(1.05rem,1.7vw,1.45rem); /* sans body — LARGE, like Conductor */
--label:clamp(0.78rem,1.1vw,0.95rem);/* mono label */
```
Body is LARGE and airy (Conductor's body is big). Never below ~17px.

## 6. Layout & components ("Black paper" — Conductor-aligned)

The reference is conductor.build: near-black, mono hero + sans body, massive
whitespace, left-aligned single column, almost no color or ornament. The deck
`argo-v3-deck.html` is the canonical implementation of this section.

- **Left-aligned, single column.** Everything flush-left against a big left margin
  (`--pad`). Never centered (except the title + the ask slides). No multi-column.
- **Generous whitespace.** Content occupies maybe half the slide; the rest is air.
- **CONSISTENCY is a rule, not a vibe.** Every content slide top-anchors at the
  SAME vertical position (`padding-top:clamp(3rem,13vh,7rem)`), so the eyebrow
  label begins at the same Y on every slide — no vertical jumping when you advance.
  One shared spacing rhythm (label → headline → body); NO per-slide inline margins.
- **Two CTA motifs, lifted from Conductor:** a solid dark button
  (`--btn`, 1px `--line-hi`, ~10px radius, mono) and a plain arrow-link
  (`text →`, arrow nudges right on hover). Used on title + ask.
- **Diagrams: thin grey hairlines on black, NO fills, NO color.** `stroke:--line-hi`,
  ~1.2px. They draw on as the slide enters. Calm line-work, not infographic.
- **The pipeline motif** — the recurring 5-stage spine (signal → true → worth →
  project → shipped). Tiny dots: filled white = live, dashed grey = horizon. It is
  the brand's primary diagram; reuse it, never reinvent per slide.
- **Tiny status dots only** — 7–9px, functional (live/designed/horizon). Never
  decorative. The ONLY non-grey thing on a slide is the small green "live" dot.
- **No cards, no gradients, no glows, no atmosphere, no particles, no icons,
  no illustration.** If a slide has visual texture, it's wrong. Subtract.
- **Motion** — quick, simple fade-up reveals (no blur, no flash), staggered.
  Diagram strokes draw on. Respect `prefers-reduced-motion`.

## 7. Deck information architecture (YC question-order)

Headlines are takeaways. Each slide answers the next question an investor asks.

| # | Slide | The question it answers | Takeaway headline (example) |
|---|---|---|---|
| 1 | Title + one-liner | What is this? | the factory one-liner + "From signal to shipped →" |
| 2 | Why now | Why does this exist now? | "Generation is free. Judgment isn't." |
| 3 | The product (pipeline) | What exactly is it? | "One pipeline: signal → true → worth → project → built." |
| 4 | Stage: what's true | How does the core work? | "Nothing moves until it's verified." |
| 5 | Stage: worth building (moat) | Why is it defensible? | "It compounds to your taste. That can't be copied." |
| 6 | Stage: your project | What do you get? | "Out the other end: one project, with the why." |
| 7 | Stage: built (horizon) | Where's it going? | "The last bottleneck is building it." |
| 8 | Proof / traction | Why believe you? | "It already extends itself — in production." |
| 9 | Rigor / how you operate | Is the team serious? | "We measure. We don't assume." |
| 10 | Why inevitable | Why is this the winner? | "Value is leaving the model. It lands here." |
| 11 | What's next | What does the money do? | "Closing the last stages, in order." |
| 12 | The ask | What do you want? | "Own the layer from frontier to built." |

Rules: ≤12 slides. One idea each. Title-readable as a standalone argument.
The "what is it" (one-liner) appears by slide 1, concretely — never withheld for vibe.

## 8. Do / Don't

**Do:** state the conclusion in the headline · one focal point per slide · reserve
ember for the single most important mark · show proof, don't claim · generous
space · mono for instruments/data.

**Don't:** label-headlines ("Our Solution") · two ideas on one slide · color as
decoration · AI-sparkle gradients on everything · particles/glassmorphism · vibe
before the plain statement of what it is · body text below 16px.
