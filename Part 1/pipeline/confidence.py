"""Confidence scoring per §4.1 of the execution doc.

score = 0.30*Source_Credibility
      + 0.20*Frequency_Volume
      + 0.20*Sentiment_Consistency
      + 0.15*Semantic_Clarity
      + 0.15*Cross_Source_Alignment

All sub-scores are on [0, 100].
"""
from __future__ import annotations

from collections import Counter
from math import log

# Initial source credibility weights (0..100). To be recalibrated after any real
# A/B experiment shows uplift correlation.
SOURCE_CREDIBILITY = {
    "internal_reviews": 95,
    "reddit": 80,
    "play_store": 75,
    "app_store": 75,
    "youtube": 60,
    "forum": 60,
    "twitter": 45,
}


def _source_credibility(source_counts: dict[str, int]) -> float:
    if not source_counts:
        return 0.0
    total = sum(source_counts.values())
    weighted = sum(SOURCE_CREDIBILITY.get(s, 40) * n for s, n in source_counts.items())
    return weighted / total


def _frequency_volume(unique_authors: int, saturate_at: int = 200) -> float:
    """Log-scaled — 1 author ≈ 0, ≥ saturate_at ≈ 100."""
    if unique_authors <= 1:
        return 0.0
    return min(100.0, 100.0 * log(unique_authors) / log(saturate_at))


def _sentiment_consistency(tones: list[str]) -> float:
    if not tones:
        return 50.0
    c = Counter(tones)
    dominant = c.most_common(1)[0][1]
    return 100.0 * dominant / len(tones)


def _semantic_clarity(intra_cluster_cosine_mean: float) -> float:
    """Mean pairwise cosine similarity within the cluster (0..1) → 0..100."""
    return max(0.0, min(100.0, intra_cluster_cosine_mean * 100.0))


def _cross_source_alignment(source_counts: dict[str, int]) -> float:
    n = sum(1 for v in source_counts.values() if v > 0)
    # 1 source = 20, 2 = 55, 3 = 80, 4+ = 100
    return {0: 0, 1: 20, 2: 55, 3: 80}.get(n, 100)


def score(
    *,
    source_counts: dict[str, int],
    unique_authors: int,
    tones: list[str],
    intra_cluster_cosine_mean: float,
) -> tuple[float, dict[str, float]]:
    breakdown = {
        "source_credibility": _source_credibility(source_counts),
        "frequency_volume": _frequency_volume(unique_authors),
        "sentiment_consistency": _sentiment_consistency(tones),
        "semantic_clarity": _semantic_clarity(intra_cluster_cosine_mean),
        "cross_source_alignment": _cross_source_alignment(source_counts),
    }
    total = (
        0.30 * breakdown["source_credibility"]
        + 0.20 * breakdown["frequency_volume"]
        + 0.20 * breakdown["sentiment_consistency"]
        + 0.15 * breakdown["semantic_clarity"]
        + 0.15 * breakdown["cross_source_alignment"]
    )
    return round(total, 2), {k: round(v, 2) for k, v in breakdown.items()}
