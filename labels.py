"""Transparency label generation (planning.md §5).

The band selects the variant; the confidence word is filled independently from
the phrasing map, so label text varies with the confidence score both within and
across variants.
"""

_REASON_TEXT = {
    "insufficient_signal": "The text was too short to analyze reliably.",
    "signals_disagree": "Our two detectors reached different conclusions.",
    "both_middling": "Neither detector found a clear pattern.",
    "single_signal": "Only one detector was available, so we won't make a strong call.",
}


def confidence_word(confidence):
    if confidence >= 0.6:
        return "high"
    if confidence >= 0.3:
        return "moderate"
    return "low"


def make_label(band, confidence, reason=None):
    """Return {'variant': 'A'|'B'|'C', 'band': band, 'text': str}."""
    conf = confidence_word(confidence)

    if band == "likely_ai":
        return {
            "variant": "A",
            "band": band,
            "text": (
                "⚠️ Likely AI-generated. This text shows patterns consistent with "
                f"AI-generated writing (confidence: {conf}). This is an automated "
                "estimate, not proof. If you wrote this yourself, you can appeal "
                "this label."
            ),
        }

    if band == "likely_human":
        return {
            "variant": "B",
            "band": band,
            "text": (
                "✅ Likely human-written. This text shows patterns consistent with "
                f"human writing (confidence: {conf}). This is an automated estimate, "
                "not a guarantee of origin."
            ),
        }

    # uncertain -> C
    reason_text = _REASON_TEXT.get(reason, "")
    return {
        "variant": "C",
        "band": "uncertain",
        "text": (
            "❓ Inconclusive. Our signals disagree or aren't strong enough to call "
            f"this one (confidence: {conf}). We're not labeling it as AI or human. "
            f"{reason_text}"
        ).strip(),
    }
