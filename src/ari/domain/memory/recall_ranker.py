"""Pure domain service: rank a list of Recall objects by a combined score."""
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class RankWeights:
    """Weights and thresholds for the recall ranking formula."""

    similarity: float = 0.6
    recency: float = 0.25
    importance: float = 0.15
    recency_half_life_days: float = 30.0
    min_similarity: float = 0.3


class RecallRanker:
    """Rank recalls by combined(similarity, recency, importance) and filter by threshold."""

    def __init__(self, weights: RankWeights | None = None) -> None:
        self._w = weights or RankWeights()

    def rank(self, recalls, now: datetime, k: int):
        """Return top-k recalls after filtering and ranking.

        Args:
            recalls: iterable of Recall objects.
            now: tz-aware datetime used as the recency reference point.
            k: maximum number of results to return.

        Returns:
            List of Recall objects sorted by combined score descending, length <= k.
        """
        w = self._w
        scored = []
        for r in recalls:
            similarity = r.score if r.score is not None else 0.0
            # Apply relevance threshold before computing combined score.
            if similarity < w.min_similarity:
                continue
            recency = self._recency(r, now, w.recency_half_life_days)
            importance = float(r.metadata.get("importance", 0.5))
            combined = w.similarity * similarity + w.recency * recency + w.importance * importance
            scored.append((combined, r))

        scored.sort(key=lambda t: t[0], reverse=True)
        return [r for _, r in scored[:k]]

    @staticmethod
    def _recency(recall, now: datetime, half_life_days: float) -> float:
        """Exponential decay: 1.0 at age=0, 0.5 at age=half_life_days."""
        if recall.created_at is None:
            return 0.5
        # Ensure now is tz-aware for comparison; created_at may be tz-aware already.
        created = recall.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        age_days = max(0.0, (now - created).total_seconds() / 86400.0)
        return 0.5 ** (age_days / half_life_days)
