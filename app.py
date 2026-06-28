"""Provenance Guard — Flask app (Milestone 3).

Submission endpoint wired to Signal 1 (stylometric heuristic). Confidence and
label are placeholders until Milestone 4/5. See planning.md.
"""

import json
import re
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import db
import labels
import scoring
import signals

_WORD = re.compile(r"\b\w+\b")

app = Flask(__name__)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

db.init_db()


def _now():
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/submit")
@limiter.limit("10 per minute;100 per day")
def submit():
    data = request.get_json(silent=True) or {}
    text = data.get("text")
    creator_id = data.get("creator_id")

    if not text or not isinstance(text, str) or not text.strip():
        return jsonify({"error": "field 'text' is required and must be a non-empty string"}), 400

    content_id = str(uuid.uuid4())
    created_at = _now()
    word_count = len(_WORD.findall(text))

    # --- Run both signals -------------------------------------------------
    s1 = signals.signal_one(text)
    s2 = signals.signal_two(text)

    # --- Combine into confidence / band (planning.md §3) ------------------
    verdict = scoring.combine(s1["score"], s2["score"], word_count)

    # Transparency label (planning.md §5): band picks the variant, confidence
    # fills the wording.
    label = labels.make_label(verdict["band"], verdict["confidence"], verdict["reason"])

    attribution = {
        "band": verdict["band"],
        "p_ai": verdict["p"],
        "reason": verdict["reason"],
        "signal_1": {
            "name": s1["name"],
            "score": s1["score"],
            "subfeatures": s1["subfeatures"],
        },
        "signal_2": {
            "name": s2["name"],
            "score": s2["score"],
            "rationale": s2["rationale"],
            "available": s2["available"],
        },
    }

    # --- Persist + audit --------------------------------------------------
    db.insert_submission({
        "content_id": content_id,
        "creator_id": creator_id,
        "text": text,
        "s1": s1["score"],
        "s1_json": json.dumps(s1["subfeatures"]),
        "s2": s2["score"],
        "s2_rationale": s2["rationale"],
        "p": verdict["p"],
        "confidence": verdict["confidence"],
        "band": verdict["band"],
        "label_variant": label["variant"],
        "label_text": label["text"],
        "status": "classified",
        "created_at": created_at,
    })
    db.append_audit(
        "submission_scored",
        content_id=content_id,
        detail={
            "creator_id": creator_id,
            "attribution": verdict["band"],
            "confidence": verdict["confidence"],
            "p_ai": verdict["p"],
            "signal_1_score": s1["score"],
            "llm_score": s2["score"],
            "single_signal": verdict["single_signal"],
            "reason": verdict["reason"],
            "status": "classified",
        },
        ts=created_at,
    )

    return jsonify({
        "content_id": content_id,
        "attribution": attribution,
        "confidence": verdict["confidence"],
        "label": label,
        "created_at": created_at,
    })


@app.post("/appeal")
def appeal():
    data = request.get_json(silent=True) or {}
    content_id = data.get("content_id")
    creator_reasoning = data.get("creator_reasoning")

    if not content_id:
        return jsonify({"error": "field 'content_id' is required"}), 400
    if not creator_reasoning or not str(creator_reasoning).strip():
        return jsonify({"error": "field 'creator_reasoning' is required"}), 400

    submission = db.get_submission(content_id)
    if not submission:
        return jsonify({"error": f"no submission found for content_id {content_id}"}), 404

    appeal_id = str(uuid.uuid4())
    created_at = _now()

    # Record the appeal and move the submission into review.
    db.insert_appeal({
        "appeal_id": appeal_id,
        "content_id": content_id,
        "appellant_id": submission.get("creator_id"),
        "reason": creator_reasoning,
        "claimed_origin": None,
        "status": "under_review",
        "reviewer_id": None,
        "note": None,
        "created_at": created_at,
        "resolved_at": None,
    })
    db.set_submission_status(content_id, "under_review")

    # Log the appeal alongside the original classification decision.
    db.append_audit(
        "appeal_received",
        content_id=content_id,
        appeal_id=appeal_id,
        detail={
            "status": "under_review",
            "appeal_reasoning": creator_reasoning,
            "original_attribution": submission.get("band"),
            "original_confidence": submission.get("confidence"),
            "original_signal_1_score": submission.get("s1"),
            "original_llm_score": submission.get("s2"),
        },
        ts=created_at,
    )

    return jsonify({
        "message": "Appeal received and queued for review.",
        "appeal_id": appeal_id,
        "content_id": content_id,
        "status": "under_review",
    })


@app.get("/log")
def log():
    """Recent audit entries for documentation/grading visibility.
    In production this would require auth (planning.md)."""
    return jsonify({"entries": db.get_log()})


@app.get("/audit/<content_id>")
def audit(content_id):
    return jsonify(db.get_audit(content_id))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
