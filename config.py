"""Tunable constants for Provenance Guard.

Every magic number from planning.md lives here so thresholds can be tuned
without touching logic. See planning.md §2/§3.
"""

# --- Signal 2 (Groq LLM judge) -------------------------------------------
GROQ_MODEL = "llama-3.3-70b-versatile"

# --- Signal combination weights (planning.md §2) -------------------------
SIGNAL_1_WEIGHT = 0.45
SIGNAL_2_WEIGHT = 0.55

# --- Band thresholds on p = P(AI) (planning.md §3) -----------------------
THRESHOLD_LIKELY_AI = 0.70      # p >= this  -> "Likely AI"
THRESHOLD_LIKELY_HUMAN = 0.40   # p <  this  -> "Likely human"; between = Uncertain
DISAGREEMENT_CUTOFF = 0.50      # |s1 - s2| > this -> force Uncertain

# --- Input guard (planning.md §3 short-text guard) -----------------------
MIN_WORDS = 40

# --- Signal 1 stylometric reference ranges (planning.md §2) --------------
# Burstiness = coefficient of variation of sentence lengths.
# Humans vary (high CV); AI is uniform (low CV). Lower CV -> more AI-like.
BURSTINESS_CV_AI = 0.20         # CV <= this -> fully AI-like (1.0)
BURSTINESS_CV_HUMAN = 0.70      # CV >= this -> fully human-like (0.0)

# Type-token ratio = unique words / total words. Lower -> more repetitive/AI-like.
TTR_AI = 0.40                   # TTR <= this -> more AI-like
TTR_HUMAN = 0.70                # TTR >= this -> more human-like

# Connective rate = formulaic-transition sentences / total sentences.
# Higher -> more AI-like scaffolding.
CONNECTIVE_RATE_MAX = 0.30      # rate >= this -> fully AI-like (1.0)

FORMULAIC_CONNECTIVES = {
    "moreover", "furthermore", "however", "therefore", "thus",
    "consequently", "additionally", "importantly", "notably",
    "overall", "firstly", "secondly", "thirdly", "finally",
    "in conclusion", "in summary", "on the other hand",
    "as a result", "for instance", "in addition",
}
