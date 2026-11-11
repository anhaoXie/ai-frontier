# -*- coding: utf-8 -*-
"""scoring.py 纯函数的回归测试（不联网、不读写状态文件）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.scoring import (
    dedupe_events,
    dice_sim,
    fresh_score,
    heat_score,
    jaccard_sim,
    norm_title,
    rank_and_cap,
    sig_tokens,
    time_filter,
    title_similarity,
)
from src.sources import Item

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def make_item(**kwargs):
    defaults = dict(
        title="OpenAI releases GPT-5",
        url="https://example.com/post",
        body="",
        published=NOW,
        source_id="s1",
        source_name="Source 1",
        weight=0.5,
        group="g1",
        cadence="news",
    )
    defaults.update(kwargs)
    return Item(**defaults)


class TestSimilarity:
    def test_norm_title(self):
        assert norm_title("  Hello, World!  ") == "hello world"
        assert norm_title("") == ""

    def test_sig_tokens_drops_stopwords_and_short_tokens(self):
        assert sig_tokens("The new AI model from OpenAI") == {"model", "openai"}

    def test_dice_and_jaccard(self):
        a, b = {"a", "b"}, {"b", "c"}
        assert dice_sim(a, b) == 0.5
        assert jaccard_sim(a, b) == pytest.approx(1 / 3)
        assert dice_sim(set(), b) == 0.0
        assert jaccard_sim(a, set()) == 0.0

    def test_title_similarity_uses_better_of_two(self):
        assert title_similarity("OpenAI releases GPT-5", "OpenAI releases GPT-5") == 1.0
        assert title_similarity("OpenAI releases GPT-5", "OpenAI releases GPT-5 model") == pytest.approx(6 / 7)
        assert title_similarity("", "") == 0.0


class TestTimeFilter:
    CFG = {"lookback_hours": 24, "window_floor_hours": 72, "daily_window_hours": 168}

    def test_news_window_uses_floor(self):
        kept = time_filter([make_item(published=NOW - timedelta(hours=48))], self.CFG, NOW)
        assert len(kept) == 1

    def test_news_item_beyond_72h_dropped(self):
        stats = {}
        kept = time_filter([make_item(published=NOW - timedelta(hours=100))], self.CFG, NOW, stats)
        assert kept == []
        assert stats["dropped_stale"] == 1
        assert stats["window_hours"] == 72

    def test_daily_cadence_uses_week_window(self):
        it = make_item(published=NOW - timedelta(hours=100), cadence="daily")
        assert len(time_filter([it], self.CFG, NOW)) == 1

    def test_undated_kept_and_counted(self):
        stats = {}
        kept = time_filter([make_item(published=None)], self.CFG, NOW, stats)
        assert len(kept) == 1
        assert stats["undated"] == 1


class TestScores:
    def test_fresh_score_boundaries(self):
        assert fresh_score(make_item(published=NOW - timedelta(hours=12)), NOW, 72) == 1.0
        assert fresh_score(make_item(published=NOW - timedelta(hours=48)), NOW, 72) == pytest.approx(0.5)
        assert fresh_score(make_item(published=NOW - timedelta(hours=500)), NOW, 72) == 0.0
        assert fresh_score(make_item(published=None), NOW, 72) == 0.45

    def test_heat_score_zero_and_positive(self):
        assert heat_score(make_item(metrics={})) == 0.0
        got = heat_score(make_item(source_id="hn_search", metrics={"points": 500}))
        assert got == pytest.approx(1.0)

    def test_rank_and_cap(self):
        items = [make_item(title="b"), make_item(title="a")]
        items[0].score, items[1].score = 1.0, 2.0
        ranked = rank_and_cap(items, {"max_candidates": 1})
        assert len(ranked) == 1
        assert ranked[0].title == "a"


class TestDedupe:
    def test_same_canonical_id_is_merged(self):
        a = make_item(title="A paper", source_id="arxiv", group="arxiv", canonical_id="2410.1")
        b = make_item(title="A paper (reposted)", source_id="arxiv_cscl", group="arxiv", canonical_id="2410.1")
        merged = dedupe_events([a, b], {"title_similarity": 0.82}, {"arxiv": 1, "arxiv_cscl": 2})
        assert len(merged) == 1
        assert merged[0].canonical_id == "2410.1"
        # 同一 arXiv 生态内：渠道 2 个，独立团体仍 1 个
        assert merged[0].cross_group == 2
        assert merged[0].corroboration == 1

    def test_similar_titles_without_canonical_are_merged(self):
        # 分桶按排序后的前两个 token，这里共用 alpha/beta 才会进入同一候选桶
        a = make_item(title="Alpha beta gamma", source_id="s1", group="g1")
        b = make_item(title="Alpha beta delta gamma", source_id="s2", group="g2")
        merged = dedupe_events([a, b], {"title_similarity": 0.82}, {"s1": 1, "s2": 2})
        assert len(merged) == 1
        assert merged[0].corroboration == 2

    def test_unrelated_titles_stay_separate(self):
        a = make_item(title="OpenAI releases GPT-5", source_id="s1", group="g1")
        b = make_item(title="Anthropic hires new safety lead", source_id="s2", group="g2")
        merged = dedupe_events([a, b], {"title_similarity": 0.82}, {"s1": 1, "s2": 2})
        assert len(merged) == 2
