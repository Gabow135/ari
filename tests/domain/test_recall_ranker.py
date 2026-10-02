"""Unit tests for RecallRanker (pure domain service)."""
from datetime import UTC, datetime, timedelta

import pytest

from ari.domain.memory.entities import Recall
from ari.domain.memory.recall_ranker import RankWeights, RecallRanker


def _recall(score: float, days_old: float = 0.0, importance: float = 0.5, uid: str = "u1") -> Recall:
    now = datetime.now(UTC)
    created = now - timedelta(days=days_old)
    return Recall(
        id=None,
        user_id=uid,
        content="test",
        metadata={"importance": importance},
        created_at=created,
        score=score,
    )


_NOW = datetime.now(UTC)


def test_higher_similarity_ranks_first():
    recalls = [_recall(score=0.5), _recall(score=0.9), _recall(score=0.7)]
    ranker = RecallRanker(RankWeights(similarity=1.0, recency=0.0, importance=0.0))
    ranked = ranker.rank(recalls, _NOW, k=3)
    scores = [r.score for r in ranked]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == pytest.approx(0.9)


def test_more_recent_recall_outranks_older_at_equal_similarity():
    old = _recall(score=0.8, days_old=60.0)
    new = _recall(score=0.8, days_old=1.0)
    ranker = RecallRanker(RankWeights(similarity=0.0, recency=1.0, importance=0.0))
    ranked = ranker.rank([old, new], _NOW, k=2)
    # new should come first (higher recency score)
    assert ranked[0] is new
    assert ranked[1] is old


def test_candidate_below_min_similarity_is_dropped():
    below = _recall(score=0.2)
    above = _recall(score=0.5)
    ranker = RecallRanker(RankWeights(min_similarity=0.3))
    ranked = ranker.rank([below, above], _NOW, k=5)
    assert len(ranked) == 1
    assert ranked[0] is above


def test_higher_importance_raises_rank():
    low_imp = _recall(score=0.8, importance=0.1)
    high_imp = _recall(score=0.8, importance=0.9)
    ranker = RecallRanker(RankWeights(similarity=0.0, recency=0.0, importance=1.0))
    ranked = ranker.rank([low_imp, high_imp], _NOW, k=2)
    assert ranked[0] is high_imp


def test_k_limits_result_count():
    recalls = [_recall(score=0.9 - i * 0.1) for i in range(5)]
    ranker = RecallRanker()
    ranked = ranker.rank(recalls, _NOW, k=3)
    assert len(ranked) == 3


def test_empty_input_returns_empty():
    ranker = RecallRanker()
    assert ranker.rank([], _NOW, k=5) == []


def test_recall_with_none_score_gets_zero_similarity():
    """A recall with score=None is treated as similarity=0.0 (not an error)."""
    no_score = Recall(id=None, user_id="u1", content="x", metadata={}, created_at=_NOW, score=None)
    high_score = _recall(score=0.9)
    ranker = RecallRanker(RankWeights(min_similarity=0.0, similarity=1.0, recency=0.0, importance=0.0))
    ranked = ranker.rank([no_score, high_score], _NOW, k=5)
    # high_score should be first; no_score has similarity 0 so comes last
    assert ranked[0] is high_score


def test_recall_without_created_at_uses_neutral_recency():
    """A recall with created_at=None should not crash; use recency=0.5."""
    no_ts = Recall(id=None, user_id="u1", content="x", metadata={}, created_at=None, score=0.9)
    with_ts = _recall(score=0.9, days_old=0.0)
    ranker = RecallRanker(RankWeights(similarity=0.0, recency=1.0, importance=0.0, min_similarity=0.0))
    # Just ensure it doesn't raise; exact order depends on recency values
    result = ranker.rank([no_ts, with_ts], _NOW, k=5)
    assert len(result) == 2


def test_rank_weights_defaults():
    w = RankWeights()
    assert w.similarity == pytest.approx(0.6)
    assert w.recency == pytest.approx(0.25)
    assert w.importance == pytest.approx(0.15)
    assert w.recency_half_life_days == pytest.approx(30.0)
    assert w.min_similarity == pytest.approx(0.3)
