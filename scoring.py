"""Confidence scoring — combine Signal 1 and Signal 2 into p / confidence / band.

Implements planning.md §3 exactly:
  p          = 0.45*s1 + 0.55*s2   (or s1 alone if Signal 2 unavailable)
  confidence = 2 * |p - 0.5|        (0 at coin-flip, 1 at the extremes)
  bands      : p >= 0.70 -> likely_ai ; p < 0.40 -> likely_human ; else uncertain
  overrides  : disagreement (|s1-s2|>0.5) and single-signal cap force uncertain;
               short text (<40 words) forces uncertain with confidence 0.
"""

import config


def _band_from_p(p):
    if p >= config.THRESHOLD_LIKELY_AI:
        return "likely_ai", None
    if p < config.THRESHOLD_LIKELY_HUMAN:
        return "likely_human", None
    return "uncertain", "both_middling"


def combine(s1, s2, word_count):
    """Return the combined verdict dict.

    Args:
        s1: Signal 1 score in [0,1] (always available).
        s2: Signal 2 score in [0,1], or None if unavailable.
        word_count: number of words in the submission.

    Returns:
        {p, confidence, band, reason, s1, s2, single_signal}
    """
    s2_available = s2 is not None
    single_signal = not s2_available

    if s2_available:
        p = config.SIGNAL_1_WEIGHT * s1 + config.SIGNAL_2_WEIGHT * s2
    else:
        p = s1

    confidence = 2 * abs(p - 0.5)
    if single_signal:
        confidence *= 0.6  # one signal cannot carry full confidence

    # --- Band selection with overrides, in priority order ----------------
    if word_count < config.MIN_WORDS:
        band, reason, confidence = "uncertain", "insufficient_signal", 0.0
    elif s2_available and abs(s1 - s2) > config.DISAGREEMENT_CUTOFF:
        band, reason = "uncertain", "signals_disagree"
    else:
        band, reason = _band_from_p(p)
        # single-signal cap: never accuse on one signal alone
        if single_signal and band == "likely_ai":
            band, reason = "uncertain", "single_signal"

    return {
        "p": round(p, 4),
        "confidence": round(confidence, 4),
        "band": band,
        "reason": reason,
        "s1": round(s1, 4),
        "s2": round(s2, 4) if s2_available else None,
        "single_signal": single_signal,
    }
