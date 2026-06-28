# Provenance Guard — Planning

> Authoritative design spec. This document is written *before* implementation and
> is the primary context handed to AI code-generation tools in Milestones 3–5.
> Every number here (weights, thresholds, label text) is a decision, not a
> placeholder. If you change a number, change it here first.

**Stack:** Python 3.11 · Flask · flask-limiter (rate limiting) · Groq API (LLM
signal) · python-dotenv · SQLite (persistence). No frontend framework — JSON API
+ optional minimal HTML.

---

## 1. Problem framing & architecture narrative

Provenance Guard accepts a piece of text, estimates how likely it was AI-generated,
and returns a **transparency label** the user can trust *because the reasoning is
visible and contestable*. The system is deliberately humble: it never claims
certainty, it shows its confidence, and it lets a human creator appeal a result
that misjudged them.

### The path of one piece of text (submission → label)

1. **Client** sends raw text to `POST /submit`.
2. **Rate limiter** (flask-limiter) checks the caller hasn't exceeded quota; if OK,
   the request proceeds.
3. **Input guard** validates the text (non-empty, length bounds, language sniff).
   Too-short input is flagged "insufficient signal" and short-circuited to an
   *uncertain* label — we never pretend to judge 20 words.
4. **Signal 1 — Stylometric heuristic** runs locally (no network). It reads the raw
   text and emits `s1 ∈ [0,1]` (higher = more AI-like) plus its sub-feature values.
5. **Signal 2 — LLM judge (Groq)** sends the text to a Groq model with a
   structured rubric and gets back `s2 ∈ [0,1]` plus a short natural-language
   rationale.
6. **Confidence scorer** combines `s1` and `s2` into a single AI-likelihood `p`,
   then derives a **confidence** value and a **band** (likely AI / uncertain /
   likely human).
7. **Label generator** turns the band + confidence into the exact user-facing
   label text (one of three variants).
8. **Persistence + audit log** writes the submission, the two signal outputs, the
   combined score, and the label to SQLite, and appends an `submission_scored`
   event to the audit log.
9. **Response** returns the submission id, scores, signal breakdown, and label to
   the client.

### The path of an appeal (appeal → audit)

1. A **creator** who disagrees with a label calls `POST /appeal` with the
   `submission_id`, who they are, why they disagree, and the claimed origin of the
   text.
2. The **appeal handler** verifies the submission exists, creates an appeal record
   with status `received`, and links it to the original submission.
3. An **audit event** `appeal_received` is logged.
4. The appeal enters the **reviewer queue** (`GET /appeals`). A human reviewer
   reads the original text, both signal outputs, the label shown, and the
   appellant's reason.
5. The reviewer resolves it via `POST /appeal/<id>/resolve`: status moves to
   `upheld` (label overridden/removed) or `rejected` (label stands). Each
   transition logs `appeal_status_changed`, and an upheld appeal also logs
   `label_overridden`.

---

## 2. Detection signals

Two independent signals with different failure modes, so one covers the other's
blind spot. Each outputs a float in `[0,1]` where **higher = more AI-like**.

### Signal 1 — Stylometric heuristic (local, deterministic)

**What it measures:** structural uniformity of the writing, via three sub-features:

| Sub-feature | What it captures | Human tendency | AI tendency |
|---|---|---|---|
| **Burstiness** | Coefficient of variation of sentence lengths (`stdev/mean`) | High — humans mix long and short sentences | Low — uniform, even sentence lengths |
| **Lexical diversity** | Type–token ratio (unique words / total words) | Often higher / messier | Often moderate and "safe" |
| **Connective uniformity** | Rate of formulaic transitions ("Moreover", "Furthermore", "In conclusion") and low punctuation variety | Lower / varied | Higher — AI leans on tidy connectives |

**Why it differs human vs. AI:** LLMs decode toward high-probability, locally
fluent tokens, which produces *even rhythm* and *predictable connective scaffolding*.
Human writing carries more variance — abrupt short sentences, idiosyncratic word
choice, uneven structure.

**Output shape:**
```json
{
  "score": 0.71,
  "subfeatures": {
    "burstiness_cv": 0.34,
    "type_token_ratio": 0.48,
    "connective_rate": 0.09
  }
}
```
Each sub-feature is normalized to a 0–1 "AI-likeness" via fixed reference ranges
(documented in code constants), then averaged:
`s1 = mean(ai_burstiness, ai_lexical, ai_connective)`.

**Blind spot:** It is a *style* detector, not a *provenance* detector. Formulaic
human writing (legal boilerplate, lab reports, recipes, SEO copy) is structurally
uniform and scores AI-like. Conversely, AI prompted to "write erratically" can
manufacture burstiness. It also degrades badly on short text and on non-prose
(poems, lists, code). See §6.

### Signal 2 — LLM judge via Groq

**What it measures:** holistic, semantic "feel" of authorship — does the text have
the hedging, generic framing, and seam-free coherence typical of model output, or
the lived specificity, opinion, and small inconsistencies typical of a human.

**Why it differs human vs. AI:** an LLM can recognize the *distributional
fingerprint* of its own kind — over-balanced arguments, absence of concrete
first-hand detail, "as an overview" framing — patterns the local heuristic can't
see because they're semantic, not structural.

**Output shape:** the model is prompted to return strict JSON:
```json
{ "score": 0.66, "rationale": "Even-handed, generic phrasing; no concrete personal detail." }
```
`s2 = score`. The rationale is stored and surfaced to reviewers (never used in
math). If Groq is unavailable or returns malformed JSON, `s2` is marked
`unavailable` and the combiner falls back to `s1` alone with a confidence penalty
(see §3).

**Blind spot:** non-deterministic and promptable. It can be fooled by AI text that
imitates human quirks, and it can over-flag highly polished human experts. It also
has no ground truth — it's a sophisticated guess, not a measurement. Cost/latency
and rate limits make it unsuitable as the *only* signal.

### Combining the signals

```
p = 0.45 * s1 + 0.55 * s2          # AI-likelihood, [0,1]; LLM weighted slightly higher
```
If `s2` is unavailable: `p = s1`, and confidence is capped (§3). Weights live in
one config constant so they're tunable without touching logic.

---

## 3. Uncertainty representation

**What `p` means:** `p` is the system's estimated probability that the text is
AI-generated. **What *confidence* means:** how far the evidence is from a coin
flip. We do **not** report `p` as "confidence" — a `p` of 0.5 is *maximum
uncertainty*, not 50% confidence.

```
confidence = 2 * abs(p - 0.5)      # 0.0 at p=0.5 (pure uncertainty) → 1.0 at p=0 or p=1
```

So a confidence score of **0.6** means: the evidence leans clearly toward one side
(`p ≈ 0.20` or `p ≈ 0.80`) but not overwhelmingly — a reasonable, not airtight,
call. We will phrase this to users as "fairly confident," never as a percentage of
correctness.

**Bands (the three label variants key off these):**

| Band | Condition | Meaning |
|---|---|---|
| **Likely AI** | `p ≥ 0.70` | Both signals lean AI |
| **Uncertain** | `0.40 ≤ p < 0.70` | Signals disagree or both middling |
| **Likely human** | `p < 0.40` | Both signals lean human |

**Calibration / safeguards:**
- **Disagreement override:** if `abs(s1 - s2) > 0.5`, force band = **Uncertain**
  regardless of `p`. Two signals that strongly disagree is *definitionally*
  uncertain, and this is our main false-positive guard (§4).
- **LLM-unavailable penalty:** if running on `s1` alone, multiply `confidence` by
  `0.6` and never assign **Likely AI** (cap at Uncertain). One signal cannot carry
  an accusation.
- **Short-text guard:** under 40 words → forced **Uncertain**, confidence `0.0`,
  reason `insufficient_signal`.

The thresholds (0.40 / 0.70), weights (0.45 / 0.55), and disagreement cutoff (0.5)
are all named constants in one config module.

---

## 4. The false-positive problem (and how the system absorbs it)

**Scenario:** A human writer submits a tightly edited, formal essay. It's uniform
in rhythm and uses clean transitions, so **Signal 1 scores 0.78 (AI-like)**.
**Signal 2**, reading actual content, notices concrete personal argument and scores
**0.30 (human-like)**.

Trace:
- `abs(s1 - s2) = 0.48` — under the 0.5 override, so no forced uncertain... but
  `p = 0.45*0.78 + 0.55*0.30 = 0.516` → band **Uncertain** anyway. The system
  *does not* accuse. Good.
- Now a worse case: a formulaic-but-human grant application where Signal 2 *also*
  leans AI (0.62). `p = 0.45*0.78 + 0.55*0.62 = 0.692` — just under 0.70, lands
  **Uncertain**. If it tipped to 0.71, the creator sees a "Likely AI" label they
  disagree with.
- **The label reflects uncertainty** by never stating provenance as fact ("This
  text *shows patterns consistent with* AI generation") and by showing the
  confidence value and the dissenting signal rationale.
- **The creator appeals** via `POST /appeal`, stating they wrote it and offering
  evidence (drafts, process notes). The appeal is logged, queued, and a human
  reviewer can override the label to `human_overridden`. The audit trail records
  the original automated call *and* the override, so the system's mistake is
  permanently visible rather than silently erased.

**Design consequences (feed into Milestone 2):** the disagreement override, the
single-signal cap, and non-committal label wording all exist specifically to make
false positives *recoverable and honest* rather than *confident and wrong*.

---

## 5. Transparency label design

Three variants. Each shows: a headline, a plain-English explanation, the confidence
phrasing, and a contestability note. **Exact text:**

**A. High-confidence AI** (`band = Likely AI`, `confidence ≥ 0.6`):
> ⚠️ **Likely AI-generated.** This text shows strong patterns consistent with
> AI-generated writing (confidence: high). This is an automated estimate, not
> proof. If you wrote this yourself, you can appeal this label.

**B. High-confidence human** (`band = Likely human`, `confidence ≥ 0.6`):
> ✅ **Likely human-written.** This text shows patterns consistent with human
> writing (confidence: high). This is an automated estimate, not a guarantee of
> origin.

**C. Uncertain** (`band = Uncertain`, *or* any result with `confidence < 0.6`):
> ❓ **Inconclusive.** Our signals disagree or aren't strong enough to call this
> one (confidence: low). We're not labeling it as AI or human. {reason}

Where `{reason}` is filled from: `insufficient_signal` ("The text was too short to
analyze reliably."), `signals_disagree` ("Our two detectors reached different
conclusions."), or `both_middling` ("Neither detector found a clear pattern.").

**Confidence phrasing map:** `confidence ≥ 0.6` → "high"; `0.3 ≤ confidence < 0.6`
→ "moderate"; `< 0.3` → "low". (A "Likely AI/human" band with moderate confidence
uses variant A/B but swaps the confidence word.)

> **Review note:** label text deliberately avoids "is" / "was" — always "shows
> patterns consistent with." Provenance is never asserted as fact.

---

## 6. Anticipated edge cases

1. **Formulaic human writing** (recipes, legal boilerplate, lab reports, résumés):
   structurally uniform → Signal 1 false-positives. *Mitigation:* Signal 2's
   semantic read + the disagreement override usually pull these back to Uncertain.
2. **Poetry / lists / very short text:** heuristics assume prose; a repetitive poem
   with simple vocabulary scores AI-like, and a haiku has no sentence-length
   variance to measure. *Mitigation:* short-text guard (< 40 words → Uncertain);
   document that non-prose is out of reliable scope.
3. **Hybrid text** (human draft polished by AI, or AI draft heavily rewritten):
   genuinely sits at `p ≈ 0.5`. *Mitigation:* this is *correctly* Uncertain — the
   band is honest, not a failure.
4. **Non-English / code / mixed-language:** both signals' reference ranges are
   English-prose-tuned. *Mitigation:* language sniff in the input guard; flag
   unsupported input as Uncertain rather than scoring it.
5. **Adversarial input** (prompt injection inside the submitted text aimed at
   Signal 2): the Groq prompt isolates user text as data and ignores embedded
   instructions; if the model returns non-conforming JSON, treat `s2` as
   unavailable.

---

## Architecture

```
SUBMISSION FLOW
                         raw text
   ┌────────┐  ───────────────────────►  ┌──────────────┐
   │ Client │                            │ POST /submit │
   └────────┘  ◄───────────────────────  └──────┬───────┘
                  label + scores (JSON)          │ raw text
                                                 ▼
                                   ┌───────────────────────────┐
                                   │ Rate limiter + input guard │
                                   └─────────────┬─────────────┘
                                  raw text       │      raw text
                       ┌─────────────────────────┴───────────────┐
                       ▼                                          ▼
            ┌────────────────────┐                   ┌────────────────────────┐
            │ Signal 1           │                   │ Signal 2 (Groq LLM)    │
            │ stylometric        │                   │ judge + rationale      │
            └─────────┬──────────┘                   └───────────┬────────────┘
                  s1 ∈ [0,1]                              s2 ∈ [0,1] + rationale
                       └──────────────────┬───────────────────────┘
                                          ▼
                             ┌──────────────────────────┐
                             │ Confidence scorer        │
                             │ p = .45·s1 + .55·s2      │
                             │ confidence, band         │
                             └────────────┬─────────────┘
                                  p, confidence, band
                                          ▼
                             ┌──────────────────────────┐
                             │ Label generator          │
                             │ → variant A / B / C text │
                             └────────────┬─────────────┘
                              submission + scores + label
                                          ▼
                             ┌──────────────────────────┐
                             │ SQLite + Audit log        │
                             │ event: submission_scored  │
                             └──────────────────────────┘

APPEAL FLOW
   ┌─────────┐  submission_id, reason,   ┌──────────────┐
   │ Creator │  appellant, claimed_origin│ POST /appeal │
   └─────────┘  ─────────────────────►   └──────┬───────┘
        ▲                                        │ appeal record (status=received)
        │  appeal_id + status                    ▼
        └────────────────────────  ┌──────────────────────────┐
                                   │ Audit log                 │
                                   │ event: appeal_received    │
                                   └────────────┬─────────────┘
                                                ▼
                                   ┌──────────────────────────┐    GET /appeals
                                   │ Reviewer queue            │ ◄──────────────── Human
                                   │ text + s1 + s2 + label    │                  reviewer
                                   │ + appellant reason        │
                                   └────────────┬─────────────┘
                          POST /appeal/<id>/resolve  (upheld | rejected)
                                                ▼
                                   ┌──────────────────────────┐
                                   │ Status update + audit     │
                                   │ appeal_status_changed     │
                                   │ (+ label_overridden)      │
                                   └──────────────────────────┘
```

**Narrative.** *Submission:* a client POSTs raw text, which passes the rate
limiter and input guard, fans out to the two detection signals, gets fused into an
AI-likelihood + confidence + band by the scorer, is turned into one of three label
variants, and is persisted with an audit entry before the JSON response returns.
*Appeal:* a creator who disputes a label POSTs an appeal referencing the
submission; it's recorded as `received`, logged, and queued for a human reviewer
who sees the full evidence and resolves it to `upheld` (label overridden) or
`rejected`, with every transition written to the audit log.

### API surface (the contract)

| Method & path | Accepts | Returns |
|---|---|---|
| `POST /submit` | `{ "text": str, "author_id"?: str }` | `{ submission_id, ai_likelihood (p), confidence, band, label:{variant,text}, signals:{s1,s1_subfeatures,s2,s2_rationale|null}, created_at }` |
| `GET /result/<submission_id>` | — | same body as `/submit` for a stored result, `404` if unknown |
| `POST /appeal` | `{ submission_id, appellant_id, reason, claimed_origin }` | `{ appeal_id, status:"received", submission_id }` |
| `GET /appeals?status=` | query filter (default `received`) | `[{ appeal_id, submission_id, status, reason, claimed_origin, original_label, s1, s2, created_at }]` |
| `POST /appeal/<appeal_id>/resolve` | `{ decision:"upheld"\|"rejected", reviewer_id, note? }` | `{ appeal_id, status, label_after }` |
| `GET /audit/<submission_id>` | — | `[{ event, ts, detail }]` ordered |
| `GET /health` | — | `{ status:"ok" }` |

**Appeal status machine:** `received → under_review → (upheld | rejected)`.
**Audit event types:** `submission_scored`, `appeal_received`,
`appeal_status_changed`, `label_overridden`.

**Persistence (SQLite tables):**
`submissions(id, text, s1, s1_json, s2, s2_rationale, p, confidence, band,
label_variant, label_text, created_at)`;
`appeals(id, submission_id, appellant_id, reason, claimed_origin, status,
reviewer_id, note, created_at, resolved_at)`;
`audit_log(id, submission_id, appeal_id, event, detail_json, ts)`.

---

## AI Tool Plan

For each milestone: the spec sections fed to the AI tool, what to ask it to
generate, and how to verify before moving on.

### M3 — Submission endpoint + first signal
- **Provide:** §2 (Detection signals, esp. Signal 1), the Architecture diagram +
  API surface, §3 output shapes.
- **Ask for:** Flask app skeleton (`/health`, `/submit` returning a stubbed label),
  flask-limiter wiring, SQLite init, and the `signal_one(text) -> {score,
  subfeatures}` stylometric function with the documented sub-features and
  normalization constants.
- **Verify:** call `signal_one` directly on ~5 samples (a uniform AI-style
  paragraph, a bursty human paragraph, a recipe, a 10-word string, a poem) and
  confirm scores move in the expected direction *before* wiring it into `/submit`.
  Then POST those samples and check the response shape matches the contract.

### M4 — Second signal + confidence scoring
- **Provide:** §2 (Signal 2 + combining), §3 (Uncertainty representation, bands,
  overrides, penalties), the diagram.
- **Ask for:** `signal_two(text) -> {score, rationale}` calling Groq with the
  strict-JSON rubric + fallback-to-unavailable handling, and `combine(s1, s2) ->
  {p, confidence, band, reason}` implementing the weights, thresholds,
  disagreement override, single-signal cap, and short-text guard.
- **Verify:** run clearly-AI vs. clearly-human samples and confirm `p` and
  `confidence` separate meaningfully (AI near `p≥0.7`, human near `p<0.4`); force
  Groq failure and confirm the single-signal cap + confidence penalty kick in;
  feed a disagreement case and confirm the override yields Uncertain.

### M5 — Production layer (labels + appeals)
- **Provide:** §5 (three label variants + confidence phrasing map + reason
  strings), §1/Architecture appeal flow, the appeal status machine + audit event
  list, API surface.
- **Ask for:** `make_label(band, confidence, reason) -> {variant, text}` producing
  exact §5 wording, the `/appeal`, `/appeals`, `/appeal/<id>/resolve` endpoints, and
  audit-log writes on every state change.
- **Verify:** craft inputs that reach all three label variants (A/B/C) and confirm
  exact text; submit an appeal and confirm status goes `received`, appears in
  `/appeals`, resolves to `upheld`/`rejected`, the label is overridden on upheld,
  and `GET /audit/<id>` shows the full event chain.

---

## Open decisions / revision log
- Weights `0.45/0.55`, thresholds `0.40/0.70`, disagreement cutoff `0.5`, short-text
  floor `40 words`, confidence bands `0.3/0.6` — all tunable constants; revisit
  after M4 once real score distributions are visible.
- Stretch features: re-read and update this file *before* starting any.
