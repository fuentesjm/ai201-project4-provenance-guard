# Provenance Guard

A transparency-first AI-text detection service. It estimates how likely a piece of
text was AI-generated using **two independent detection signals**, fuses them into a
calibrated confidence score, returns a plain-English **transparency label**, writes
a structured **audit log**, and lets creators **appeal** a result they disagree with.

The guiding principle is humility: the system never claims certainty, always shows
its confidence, and makes every decision contestable. Full design rationale lives in
[planning.md](planning.md); this README is the implementation-and-reasoning summary.

## Architecture

```
SUBMISSION FLOW
  Client ──raw text──> POST /submit ──> [rate limiter + input guard]
                                              │ raw text
                          ┌───────────────────┴───────────────────┐
                          ▼                                        ▼
                 Signal 1 (stylometric)                   Signal 2 (Groq LLM)
                  s1 ∈ [0,1] + subfeatures            s2 ∈ [0,1] + rationale
                          └───────────────┬────────────────────────┘
                                          ▼
                          confidence scorer  (p = .45·s1 + .55·s2,
                                              confidence = 2·|p−0.5|, band)
                                          ▼
                          transparency label (variant A / B / C)
                                          ▼
                          SQLite + audit log (event: submission_scored)
                                          ▼
                          JSON response (content_id, scores, label)

APPEAL FLOW
  Creator ──content_id + reasoning──> POST /appeal ──> status: under_review
                                          ▼
                          audit log (event: appeal_received, with original decision)
                                          ▼
                          confirmation response
```

**Submission narrative:** a client POSTs text, which passes the rate limiter and
input guard, fans out to the two signals, gets fused into an AI-likelihood +
confidence + band, is rendered into one of three label variants, and is persisted
with an audit entry before the JSON response returns. **Appeal narrative:** a
creator who disputes a label POSTs the `content_id` and their reasoning; the
submission moves to `under_review`, an audit entry records the appeal alongside the
original classification, and a confirmation returns.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
echo "GROQ_API_KEY=your-key-here" > .env
python app.py        # serves on :5000 (use :5050 if macOS AirPlay holds 5000)
```

## Endpoints

| Method & path | Purpose |
|---|---|
| `POST /submit` | Classify text. Body: `{text, creator_id}`. Returns `content_id`, `attribution` (both signal scores + band), `confidence`, `label`. |
| `POST /appeal` | Contest a label. Body: `{content_id, creator_reasoning}`. Sets status `under_review`, logs the appeal + original decision. |
| `GET /log` | Recent structured audit entries (`{entries: [...]}`). |
| `GET /audit/<content_id>` | Full event chain for one submission. |
| `GET /health` | Liveness check. |

## Detection signals — and why

The two signals were chosen to **fail in different ways**, so each covers the
other's blind spot. One is structural and deterministic; the other is semantic and
probabilistic.

### Signal 1 — Stylometric heuristic (local, deterministic)

Measures structural uniformity via three sub-features:
- **Burstiness** — coefficient of variation of sentence lengths. Humans mix long
  and short sentences; LLMs decode toward even, uniform rhythm.
- **Type-token ratio** — lexical diversity. AI text trends toward "safe," moderately
  repetitive vocabulary.
- **Connective uniformity** — rate of formulaic transitions ("Moreover",
  "Furthermore", "In conclusion"). AI leans on tidy scaffolding.

**Why this signal:** it's free, instant, fully reproducible, and needs no network or
API key — so the system always has at least one opinion even if the LLM is down. It
captures a real artifact of how language models decode (local fluency → low
variance).

**Blind spot:** it's a *style* detector, not a *provenance* detector. Formulaic
human writing (legal boilerplate, lab reports, recipes) is structurally uniform and
scores AI-like; AI told to "write erratically" can fake burstiness.

### Signal 2 — Groq LLM judge (semantic)

Sends the text to a Groq-hosted model with a forensic rubric; returns a 0–1 score
plus a short rationale. It reads what the heuristic can't see — generic hedging,
even-handed framing, absence of concrete first-hand detail.

**Why this signal:** it catches *semantic* fingerprints the structural signal is
blind to, and the rationale gives a human reviewer something legible. An LLM is
unusually good at recognizing the distributional "feel" of model output.

**Blind spot:** non-deterministic and promptable; it can over-flag polished human
experts and be fooled by AI imitating human quirks. It has no ground truth — it's a
sophisticated guess, not a measurement.

**What I'd change for a real deployment:** replace the LLM judge's self-report with
a calibrated detector (e.g. a fine-tuned classifier or token-probability/perplexity
measure from a known model), validate the stylometric reference ranges against a
labeled corpus instead of hand-picked constants, and make Signal 1 length-aware so
its lexical component stops collapsing to 0 on short inputs (see Limitations).

## Confidence scoring — and why

```
p          = 0.45·s1 + 0.55·s2        # AI-likelihood; LLM weighted slightly higher
confidence = 2·|p − 0.5|              # 0 at a coin flip, 1 at the extremes
bands      : p ≥ 0.70 → likely AI ; p < 0.40 → likely human ; else uncertain
```

**Why separate `p` from `confidence`:** the single most important scoring decision.
`p` is *which way* the evidence leans; confidence is *how far from a coin flip* it
is. A `p` of 0.5 is **maximum uncertainty**, not "50% confident" — collapsing the
two would let the system display a confident-looking number for its most uncertain
case. Deriving confidence as distance from 0.5 makes "we don't know" a first-class,
honest output.

**Why these guards:** a **disagreement override** (if `|s1−s2| > 0.5`, force
Uncertain) and a **single-signal cap** (never label "Likely AI" on one signal)
exist so a false positive becomes an honest "Uncertain" rather than a confident
accusation. The LLM weight (0.55) is only slightly above the heuristic because the
LLM is more capable but less trustworthy (non-deterministic).

**What I'd change for a real deployment:** the thresholds (0.40 / 0.70) and weights
are hand-set; with a labeled dataset I'd fit them to a target false-positive rate
and calibrate `p` to an actual probability (e.g. isotonic/Platt scaling) so a
reported 0.7 means "70% of such texts are truly AI."

### Example submissions (real scores from Milestone 4 testing)

**High-confidence case** — casual, irregular human writing:
> "ok so i finally tried that new ramen place downtown and honestly? underwhelming.
> the broth was fine but they put WAY too much sodium in it…"

```
s1 (stylometric) = 0.06    s2 (LLM) = 0.20
p = 0.14   confidence = 0.73   band = likely_human
```

**Lower-confidence case** — formal human writing (the false-positive scenario):
> "The relationship between monetary policy and asset price inflation has been
> extensively studied in the literature. Central banks face a fundamental tension…"

```
s1 (stylometric) = 0.30    s2 (LLM) = 0.80
p = 0.57   confidence = 0.15   band = uncertain  (reason: signals_disagree)
```

The 0.73 vs 0.15 gap shows the scorer produces meaningful variation, not a constant.
The second case is the design working: the LLM over-flags formal prose, the
stylometric signal disagrees, and instead of a wrong "Likely AI" accusation the
system honestly returns low-confidence Uncertain.

## Transparency labels (exact text of all three variants)

The **band** selects the variant; the **confidence word** (high / moderate / low) is
filled from the score, so text varies within and across variants. Verbatim output:

**Variant A — Likely AI** (`band = likely_ai`):
> ⚠️ Likely AI-generated. This text shows patterns consistent with AI-generated
> writing (confidence: high). This is an automated estimate, not proof. If you wrote
> this yourself, you can appeal this label.

**Variant B — Likely human** (`band = likely_human`):
> ✅ Likely human-written. This text shows patterns consistent with human writing
> (confidence: high). This is an automated estimate, not a guarantee of origin.

**Variant C — Inconclusive** (`band = uncertain`):
> ❓ Inconclusive. Our signals disagree or aren't strong enough to call this one
> (confidence: low). We're not labeling it as AI or human. Our two detectors reached
> different conclusions.

The trailing sentence of variant C is filled by reason: `insufficient_signal` ("The
text was too short to analyze reliably."), `signals_disagree` ("Our two detectors
reached different conclusions."), `both_middling` ("Neither detector found a clear
pattern."), or `single_signal` ("Only one detector was available, so we won't make a
strong call."). Labels never assert provenance as fact — always "patterns consistent
with."

## Appeals workflow

`POST /appeal` accepts `{content_id, creator_reasoning}`. It verifies the submission
exists, records an appeal at status `under_review`, moves the submission's status to
`under_review`, and writes an `appeal_received` audit entry that carries the
appellant's reasoning **alongside the original classification** (band, confidence,
both signal scores) so a reviewer sees the decision and the contest side by side. It
returns a confirmation with an `appeal_id`. Automated re-classification is out of
scope; resolution is a human reviewer's job.

## Rate limiting

```python
@limiter.limit("10 per minute;100 per day")   # storage_uri="memory://"
def submit(): ...
```

**Reasoning (defensible, not arbitrary):** a genuine writer checking their own work
submits a handful of pieces in a sitting — **10/minute** covers iterative editing and
re-checks while making a scripted flood impossible. **100/day** reflects that even a
heavy individual user rarely exceeds a few dozen distinct submissions a day; beyond
100 is automation, not a human. The two tiers stop both burst floods (per-minute)
and slow-drip scraping (per-day).

**Evidence** — 12 rapid requests in one fresh window (first 10 succeed, rest 429):

```
200  200  200  200  200  200  200  200  200  200  429  429
```

## Audit log

Every submission writes one structured JSON entry; every appeal writes another.
SQLite-backed (not console output). A classification entry captures `timestamp`,
`content_id`, `creator_id`, `attribution`, `confidence`, `p_ai`, `signal_1_score`,
`llm_score`, `single_signal`, `status`. An appeal entry captures `appeal_reasoning`,
`status: under_review`, and the original decision. A fresh-DB `GET /log` after three
submissions and one appeal:

```
appeal_received    status=under_review  attribution=likely_ai
submission_scored  status=classified    attribution=uncertain
submission_scored  status=classified    attribution=likely_human
submission_scored  status=classified    attribution=likely_ai
```

Example appeal entry:

```json
{
  "event": "appeal_received",
  "content_id": "f2a5ab81-4c56-43ee-9d16-836cb4c1950b",
  "status": "under_review",
  "appeal_reasoning": "I wrote this myself from personal experience. I am a non-native English speaker and my writing style may appear more formal than typical.",
  "original_attribution": "likely_ai",
  "original_confidence": 0.4824,
  "original_signal_1_score": 0.5471,
  "original_llm_score": 0.9,
  "appeal_id": "56461143-19fc-494e-a047-d20c9d5f9c5e",
  "timestamp": "2026-06-28T16:28:30.049988+00:00"
}
```

## Known limitations

- **Formal/formulaic human writing is the headline failure.** Academic prose, legal
  boilerplate, and recipes are structurally uniform with tidy connectives, so Signal
  1 scores them AI-like — *because the signal measures style, not authorship*. In
  testing, a real monetary-policy paragraph drove `s1 = 0.30` but the LLM pushed
  `s2 = 0.80`; only the disagreement override kept it from a false "Likely AI." A
  formal piece where the LLM *also* leans AI would tip over the line. This is a
  direct consequence of using a structural proxy for a property (provenance) it
  can't actually observe.
- **Short text degrades the heuristic.** Type-token ratio is length-sensitive; on
  short inputs nearly every word is unique, so the lexical component collapses to 0
  and the separation leans entirely on the LLM. The 40-word short-text guard forces
  Uncertain below the floor, but inputs just above it are weak.
- **Non-prose (poems, lists, code) is out of scope** — the signals assume English
  prose with sentence structure to measure.
- **The LLM judge is non-deterministic and self-reported**, with no ground truth; it
  can over-flag polished experts and be gamed by AI imitating human quirks.

## Spec reflection

**How the spec helped:** writing planning.md §3 *before* coding forced me to pin
exact constants — weights `0.45/0.55`, thresholds `0.40/0.70`, the disagreement
cutoff `0.5`. When I built `scoring.py`, there were no open decisions left; the code
is a direct transcription of the spec, and I could verify it line-for-line against
the document instead of inventing scoring logic mid-implementation.

**How the implementation diverged:** (1) planning.md originally named the request
field `author_id`, but the assignment's grading interface uses `creator_id`, so I
diverged to match the real contract. (2) planning.md §5's label rules were internally
contradictory — variant C claimed "any confidence < 0.6" *and* a note said
likely-AI/human at moderate confidence stays A/B. During the M5 review step I
resolved it to a single rule (**band selects variant, confidence selects the word**)
and updated planning.md so spec and code agree.

## AI usage

This project was built with AI assistance; I directed and reviewed every step.

1. **Signal 2 + scoring generation.** I directed the AI to generate the Groq LLM
   judge and the confidence-combiner from planning.md §2/§3. It produced working
   scoring logic that matched my thresholds, but the LLM prompt used Python
   `str.format()` while containing a literal JSON example `{"score": ...}` — the
   braces were parsed as format fields, so **every** call failed with `KeyError` and
   silently fell back to "unavailable." I caught it only because I tested the signal
   standalone first; I overrode the approach by switching to `str.replace()` for the
   text placeholder.

2. **Label generation.** I directed the AI to generate `make_label()` from the §5
   variants. It implemented the literal text faithfully — which surfaced that *my
   spec* was contradictory about variant selection. I overrode by rewriting the
   selection rule (band → variant, confidence → word) and revising planning.md, then
   had the function regenerated against the corrected spec.

3. **Rate limiter configuration.** The AI initially wired Flask-Limiter with
   `default_limits=["100 per hour"]` and no storage backend. I overrode it to the
   spec's `10 per minute;100 per day` on the submit route with `storage_uri="memory://"`,
   and documented the reasoning behind those specific numbers above.
