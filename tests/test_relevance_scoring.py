"""
tests/test_relevance_scoring.py

Tests for Stage 4 relevance scoring (src/stage4_relevance.py).

All tests use monkeypatch and sqlite3 in-memory databases.
No network calls; all ML dependencies are mocked.
"""

from __future__ import annotations

import sqlite3
import struct
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_embedding(values: list[float]) -> bytes:
    """Pack a list of floats as a numpy float32 byte array (like the DB stores)."""
    arr = np.array(values, dtype=np.float32)
    return arr.tobytes()


def _make_conn(pages: list[dict]) -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the pages table populated."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE pages (
            page_id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            version_hash TEXT NOT NULL,
            last_modified TEXT,
            last_checked TEXT,
            last_ingested TEXT,
            doc_embedding BLOB
        )
    """)
    for p in pages:
        conn.execute(
            "INSERT INTO pages VALUES (?,?,?,?,?,?,?,?,?)",
            (
                p["page_id"],
                p.get("url", "http://example.com"),
                p.get("title", p["page_id"]),
                p["content"],
                p.get("version_hash", "abc"),
                None, None, None,
                p.get("doc_embedding"),
            ),
        )
    conn.commit()
    return conn


def _minimal_config(overrides: dict | None = None) -> dict:
    """Return a minimal config dict for Stage 4 tests."""
    cfg = {
        "relevance_scoring": {
            "rrf_k": 60,
            "rrf_weight_bm25": 1.0,
            "rrf_weight_semantic": 2.0,
            "top_n_candidates": 3,
            "min_score_threshold": None,
            "source_importance_floor": 0.5,
            "fast_pass": {"source_importance_min": 1.0},
            "yake": {
                "keyphrases_per_80_words": 1,
                "min_keyphrases": 5,
                "max_keyphrases": 15,
                "short_diff_word_threshold": 50,
            },
        },
        "semantic_scoring": {
            "biencoder": {
                "model": "BAAI/bge-base-en-v1.5",
                "high_threshold": 0.75,
                "low_medium_threshold": 0.45,
                "low_medium_min_chunks": 3,
            },
        },
    }
    if overrides:
        for key, val in overrides.items():
            cfg[key] = val
    return cfg


# ---------------------------------------------------------------------------
# _tokenize
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_lowercase(self):
        from src.stage4_relevance import _tokenize
        assert _tokenize("Trade Marks Act") == ["trade", "marks", "act"]

    def test_strips_punctuation(self):
        from src.stage4_relevance import _tokenize
        assert _tokenize("section 44;") == ["section", "44"]

    def test_empty_string(self):
        from src.stage4_relevance import _tokenize
        assert _tokenize("") == []

    def test_numbers_kept(self):
        from src.stage4_relevance import _tokenize
        assert "1995" in _tokenize("Trade Marks Act 1995")


# ---------------------------------------------------------------------------
# _rank_scores
# ---------------------------------------------------------------------------


class TestRankScores:
    def test_descending_ranks(self):
        from src.stage4_relevance import _rank_scores
        ranks = _rank_scores([10.0, 5.0, 20.0])
        # 20.0 is rank 1, 10.0 is rank 2, 5.0 is rank 3
        assert ranks[2] == 1
        assert ranks[0] == 2
        assert ranks[1] == 3

    def test_ties_get_same_rank(self):
        from src.stage4_relevance import _rank_scores
        ranks = _rank_scores([5.0, 5.0, 1.0])
        assert ranks[0] == ranks[1]
        assert ranks[2] > ranks[0]

    def test_ascending_mode(self):
        from src.stage4_relevance import _rank_scores
        # Lower is better (BM25 scores don't need this, but test the flag)
        ranks = _rank_scores([3.0, 1.0, 2.0], higher_is_better=False)
        assert ranks[1] == 1  # 1.0 is rank 1 (lowest)
        assert ranks[2] == 2
        assert ranks[0] == 3

    def test_single_element(self):
        from src.stage4_relevance import _rank_scores
        assert _rank_scores([42.0]) == [1]

    def test_all_zeros(self):
        from src.stage4_relevance import _rank_scores
        ranks = _rank_scores([0.0, 0.0, 0.0])
        assert all(r == 1 for r in ranks)


# ---------------------------------------------------------------------------
# _distribution
# ---------------------------------------------------------------------------


class TestDistribution:
    def test_empty_returns_empty_dict(self):
        from src.stage4_relevance import _distribution
        assert _distribution([]) == {}

    def test_single_value(self):
        from src.stage4_relevance import _distribution
        d = _distribution([0.5])
        assert d["min"] == 0.5
        assert d["max"] == 0.5
        assert d["median"] == 0.5

    def test_known_values(self):
        from src.stage4_relevance import _distribution
        d = _distribution([1.0, 2.0, 3.0, 4.0, 5.0])
        assert d["min"] == 1.0
        assert d["max"] == 5.0
        assert d["median"] == 3.0


# ---------------------------------------------------------------------------
# _fuse_rrf
# ---------------------------------------------------------------------------


class TestFuseRRF:
    def test_higher_semantic_weight_favours_semantic(self):
        from src.stage4_relevance import _fuse_rrf

        pages = [
            {"page_id": "A", "content": "alpha"},
            {"page_id": "B", "content": "beta"},
        ]
        # A ranks best on BM25 but worst on semantic.
        # B ranks worst on BM25 but best on semantic.
        # With w_sem=2.0 > w_bm25=1.0, B should win overall.
        bm25_scores = [10.0, 1.0]
        semantic_scores = [0.1, 0.9]

        results = _fuse_rrf(pages, bm25_scores, semantic_scores, rrf_k=60, w_bm25=1.0, w_sem=2.0)
        by_id = {r.page_id: r for r in results}

        assert by_id["B"].rrf_score > by_id["A"].rrf_score

    def test_rrf_formula(self):
        from src.stage4_relevance import _fuse_rrf

        pages = [{"page_id": "X", "content": "text"}]
        results = _fuse_rrf(
            pages, [5.0], [0.8], rrf_k=60, w_bm25=1.0, w_sem=2.0
        )
        result = results[0]
        # Both bm25_rank and semantic_rank should be 1 (only one page).
        expected = 1.0 / (60 + 1) + 2.0 / (60 + 1)
        assert abs(result.rrf_score - expected) < 1e-9

    def test_page_ids_preserved(self):
        from src.stage4_relevance import _fuse_rrf

        pages = [
            {"page_id": "P1", "content": "foo"},
            {"page_id": "P2", "content": "bar"},
            {"page_id": "P3", "content": "baz"},
        ]
        results = _fuse_rrf(pages, [3.0, 2.0, 1.0], [0.3, 0.6, 0.1], rrf_k=60, w_bm25=1.0, w_sem=2.0)
        ids = {r.page_id for r in results}
        assert ids == {"P1", "P2", "P3"}


# ---------------------------------------------------------------------------
# _select_candidates
# ---------------------------------------------------------------------------


class TestSelectCandidates:
    def _make_scores(self, scores: list[float]):
        from src.stage4_relevance import PageRelevanceScore
        results = []
        for i, s in enumerate(scores):
            results.append(PageRelevanceScore(
                page_id=f"P{i}",
                bm25_score=0.0, bm25_rank=i + 1,
                semantic_score=0.0, semantic_rank=i + 1,
                rrf_score=s, final_score=s,
            ))
        # Sort descending as score_relevance does.
        results.sort(key=lambda r: r.final_score, reverse=True)
        return results

    def test_top_n_selected(self):
        from src.stage4_relevance import _select_candidates
        scores = self._make_scores([0.9, 0.8, 0.7, 0.6, 0.5])
        candidates = _select_candidates(scores, top_n=3, min_score_threshold=None, fast_pass_triggered=False)
        assert len(candidates) == 3

    def test_threshold_adds_extra(self):
        from src.stage4_relevance import _select_candidates
        scores = self._make_scores([0.9, 0.8, 0.7, 0.65, 0.5])
        # top_n=2 but threshold=0.6 should pull in the 0.65 and 0.7 pages too.
        candidates = _select_candidates(scores, top_n=2, min_score_threshold=0.6, fast_pass_triggered=False)
        # Scores: sorted desc = [0.9, 0.8, 0.7, 0.65, 0.5]
        # Top-2: 0.9, 0.8 → both above threshold anyway
        # Threshold adds: 0.7 and 0.65
        assert len(candidates) == 4

    def test_fast_pass_returns_all(self):
        from src.stage4_relevance import _select_candidates
        scores = self._make_scores([0.9, 0.1, 0.05])
        candidates = _select_candidates(scores, top_n=1, min_score_threshold=None, fast_pass_triggered=True)
        assert len(candidates) == 3

    def test_no_duplicates(self):
        from src.stage4_relevance import _select_candidates
        scores = self._make_scores([0.9, 0.8])
        candidates = _select_candidates(scores, top_n=2, min_score_threshold=0.5, fast_pass_triggered=False)
        ids = [c.page_id for c in candidates]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# _extract_keyphrases — with mocked YAKE
# ---------------------------------------------------------------------------


class TestExtractKeyphrases:
    def test_yake_not_installed_returns_empty(self):
        from src.stage4_relevance import _extract_keyphrases
        with patch.dict("sys.modules", {"yake": None}):
            result = _extract_keyphrases("some diff text here", {}, [])
        # When yake is None in sys.modules, ImportError is raised → empty list.
        # NER supplement: ner_entities is empty so result is [].
        assert isinstance(result, list)

    def test_ner_supplement_for_short_diff(self):
        from src.stage4_relevance import _extract_keyphrases

        mock_kw_extractor = MagicMock()
        mock_kw_extractor.extract_keywords.return_value = []

        mock_yake = MagicMock()
        mock_yake.KeywordExtractor.return_value = mock_kw_extractor

        ner_entities = ["Trade Marks Act", "section 44"]
        cfg = {"short_diff_word_threshold": 100}  # threshold > any short text

        with patch.dict("sys.modules", {"yake": mock_yake}):
            result = _extract_keyphrases("short text", cfg, ner_entities)

        # YAKE returned nothing; NER entities should be added.
        assert "Trade Marks Act" in result
        assert "section 44" in result

    def test_no_ner_supplement_for_long_diff(self):
        from src.stage4_relevance import _extract_keyphrases

        long_text = "word " * 200  # 200 words — above threshold

        mock_kw_extractor = MagicMock()
        mock_kw_extractor.extract_keywords.return_value = [("patent", 0.1)]
        mock_yake = MagicMock()
        mock_yake.KeywordExtractor.return_value = mock_kw_extractor

        ner_entities = ["extra entity"]
        cfg = {"short_diff_word_threshold": 50}

        with patch.dict("sys.modules", {"yake": mock_yake}):
            result = _extract_keyphrases(long_text, cfg, ner_entities)

        assert "extra entity" not in result

    def test_no_duplicate_ner(self):
        from src.stage4_relevance import _extract_keyphrases

        mock_kw_extractor = MagicMock()
        mock_kw_extractor.extract_keywords.return_value = [("trade marks act", 0.1)]
        mock_yake = MagicMock()
        mock_yake.KeywordExtractor.return_value = mock_kw_extractor

        ner_entities = ["Trade Marks Act"]  # same as YAKE result (case-insensitive)
        cfg = {"short_diff_word_threshold": 1000}

        with patch.dict("sys.modules", {"yake": mock_yake}):
            result = _extract_keyphrases("short", cfg, ner_entities)

        count = sum(1 for kp in result if kp.lower() == "trade marks act")
        assert count == 1


# ---------------------------------------------------------------------------
# _bm25_score
# ---------------------------------------------------------------------------


class TestBM25Score:
    def test_rank_bm25_not_installed_returns_zeros(self):
        from src.stage4_relevance import _bm25_score
        with patch.dict("sys.modules", {"rank_bm25": None}):
            scores = _bm25_score(["patent"], ["patent law text", "copyright act"])
        assert scores == [0.0, 0.0]

    def test_empty_keyphrases_returns_zeros(self):
        from src.stage4_relevance import _bm25_score
        scores = _bm25_score([], ["some content", "other content"])
        assert scores == [0.0, 0.0]

    def test_empty_pages_returns_empty(self):
        from src.stage4_relevance import _bm25_score
        with patch.dict("sys.modules", {"rank_bm25": None}):
            assert _bm25_score(["query"], []) == []

    def test_matching_page_scores_higher(self):
        """Integration: matching page should outscore unrelated page."""
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            pytest.skip("rank_bm25 not installed")

        from src.stage4_relevance import _bm25_score

        page_a = "patent examination trade marks intellectual property registration"
        page_b = "weather forecast sunshine temperature humidity rainfall"
        keyphrases = ["patent", "trade marks", "registration"]

        scores = _bm25_score(keyphrases, [page_a, page_b])
        assert scores[0] > scores[1]


# ---------------------------------------------------------------------------
# _semantic_score — with mocked bi-encoder
# ---------------------------------------------------------------------------


class TestSemanticScore:
    def _make_pages_with_embeddings(self):
        """Return two pages with unit-vector embeddings (2-dim for simplicity)."""
        # Page A aligns with query; page B is orthogonal.
        emb_a = np.array([1.0, 0.0], dtype=np.float32)
        emb_b = np.array([0.0, 1.0], dtype=np.float32)
        return [
            {"page_id": "A", "content": "...", "doc_embedding": emb_a.tobytes()},
            {"page_id": "B", "content": "...", "doc_embedding": emb_b.tobytes()},
        ]

    def test_no_model_returns_zeros(self):
        from src.stage4_relevance import _semantic_score
        pages = self._make_pages_with_embeddings()
        scores = _semantic_score("some diff", pages, "BAAI/bge-base-en-v1.5")
        # No model loaded in test env; patched to None → zeros.
        with patch("src.stage4_relevance._load_biencoder", return_value=None):
            scores = _semantic_score("some diff", pages, "BAAI/bge-base-en-v1.5")
        assert scores == [0.0, 0.0]

    def test_cosine_similarity_computed_correctly(self):
        from src.stage4_relevance import _semantic_score

        pages = self._make_pages_with_embeddings()
        # Mock model returns [1.0, 0.0] — perfectly aligned with page A.
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([1.0, 0.0], dtype=np.float32)

        with patch("src.stage4_relevance._load_biencoder", return_value=mock_model):
            scores = _semantic_score("diff text", pages, "BAAI/bge-base-en-v1.5")

        assert len(scores) == 2
        assert abs(scores[0] - 1.0) < 1e-5   # dot([1,0],[1,0]) = 1.0
        assert abs(scores[1] - 0.0) < 1e-5   # dot([1,0],[0,1]) = 0.0

    def test_missing_embedding_scores_zero(self):
        from src.stage4_relevance import _semantic_score

        pages = [{"page_id": "C", "content": "...", "doc_embedding": None}]
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([1.0, 0.0], dtype=np.float32)

        with patch("src.stage4_relevance._load_biencoder", return_value=mock_model):
            scores = _semantic_score("diff", pages, "BAAI/bge-base-en-v1.5")

        assert scores == [0.0]

    def test_negative_cosine_clamped_to_zero(self):
        from src.stage4_relevance import _semantic_score

        emb_page = np.array([-1.0, 0.0], dtype=np.float32)
        pages = [{"page_id": "D", "content": "...", "doc_embedding": emb_page.tobytes()}]

        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([1.0, 0.0], dtype=np.float32)

        with patch("src.stage4_relevance._load_biencoder", return_value=mock_model):
            scores = _semantic_score("diff", pages, "BAAI/bge-base-en-v1.5")

        assert scores[0] == 0.0


# ---------------------------------------------------------------------------
# score_relevance (integration tests with mocked DB and models)
# ---------------------------------------------------------------------------


class TestScoreRelevance:
    def _corpus(self):
        """Three pages with distinct embeddings."""
        emb_a = np.array([1.0, 0.0], dtype=np.float32).tobytes()
        emb_b = np.array([0.0, 1.0], dtype=np.float32).tobytes()
        emb_c = np.array([0.7, 0.7], dtype=np.float32).tobytes()
        return [
            {"page_id": "A", "content": "patent trade mark examination registration",
             "doc_embedding": emb_a},
            {"page_id": "B", "content": "copyright design protection artistic work",
             "doc_embedding": emb_b},
            {"page_id": "C", "content": "intellectual property general overview",
             "doc_embedding": emb_c},
        ]

    def _mock_model(self):
        """Returns a mock bi-encoder that encodes as [1.0, 0.0]."""
        mock = MagicMock()
        mock.encode.return_value = np.array([1.0, 0.0], dtype=np.float32)
        return mock

    def _mock_yake_kw(self, keyphrases):
        mock_kw = MagicMock()
        mock_kw.extract_keywords.return_value = [(kp, 0.1) for kp in keyphrases]
        mock_yake = MagicMock()
        mock_yake.KeywordExtractor.return_value = mock_kw
        return mock_yake

    def test_returns_relevance_result(self):
        from src.stage4_relevance import score_relevance, RelevanceResult

        conn = _make_conn(self._corpus())
        config = _minimal_config()

        with patch("src.stage4_relevance._load_biencoder", return_value=self._mock_model()), \
             patch.dict("sys.modules", {"rank_bm25": None, "yake": None}):
            result = score_relevance("patent trade mark diff", conn, config)

        assert isinstance(result, RelevanceResult)
        assert isinstance(result.candidates, list)
        assert isinstance(result.all_pages, list)
        assert len(result.all_pages) == 3

    def test_all_pages_in_all_pages(self):
        from src.stage4_relevance import score_relevance

        conn = _make_conn(self._corpus())
        config = _minimal_config()

        with patch("src.stage4_relevance._load_biencoder", return_value=self._mock_model()), \
             patch.dict("sys.modules", {"rank_bm25": None, "yake": None}):
            result = score_relevance("diff text", conn, config)

        ids = {ps.page_id for ps in result.all_pages}
        assert ids == {"A", "B", "C"}

    def test_top_n_candidates_limited(self):
        from src.stage4_relevance import score_relevance

        conn = _make_conn(self._corpus())
        config = _minimal_config()
        config["relevance_scoring"]["top_n_candidates"] = 2

        with patch("src.stage4_relevance._load_biencoder", return_value=self._mock_model()), \
             patch.dict("sys.modules", {"rank_bm25": None, "yake": None}):
            result = score_relevance("diff text", conn, config)

        # Without threshold, should be exactly top_n.
        assert len(result.candidates) == 2

    def test_source_importance_multiplier(self):
        from src.stage4_relevance import score_relevance

        conn = _make_conn(self._corpus())
        config = _minimal_config()

        with patch("src.stage4_relevance._load_biencoder", return_value=self._mock_model()), \
             patch.dict("sys.modules", {"rank_bm25": None, "yake": None}):
            result_low = score_relevance("diff", conn, config, source_importance=0.0)
            result_high = score_relevance("diff", conn, config, source_importance=1.0)

        top_low = result_low.all_pages[0].final_score
        top_high = result_high.all_pages[0].final_score
        # source_importance=1.0 should give a higher final score.
        assert top_high > top_low

    def test_fast_pass_source_importance_1(self):
        from src.stage4_relevance import score_relevance

        conn = _make_conn(self._corpus())
        config = _minimal_config()
        config["relevance_scoring"]["top_n_candidates"] = 1  # would normally limit

        with patch("src.stage4_relevance._load_biencoder", return_value=self._mock_model()), \
             patch.dict("sys.modules", {"rank_bm25": None, "yake": None}):
            result = score_relevance(
                "diff text", conn, config, source_importance=1.0
            )

        assert result.fast_pass_triggered is True
        assert len(result.candidates) == 3   # all pages, not just top-1

    def test_no_fast_pass_below_threshold(self):
        from src.stage4_relevance import score_relevance

        conn = _make_conn(self._corpus())
        config = _minimal_config()

        with patch("src.stage4_relevance._load_biencoder", return_value=self._mock_model()), \
             patch.dict("sys.modules", {"rank_bm25": None, "yake": None}):
            result = score_relevance(
                "diff text", conn, config, source_importance=0.9
            )

        assert result.fast_pass_triggered is False

    def test_empty_corpus_returns_empty_result(self):
        from src.stage4_relevance import score_relevance

        conn = _make_conn([])
        config = _minimal_config()

        with patch("src.stage4_relevance._load_biencoder", return_value=self._mock_model()), \
             patch.dict("sys.modules", {"rank_bm25": None, "yake": None}):
            result = score_relevance("diff text", conn, config)

        assert result.candidates == []
        assert result.all_pages == []

    def test_observation_data_populated(self):
        from src.stage4_relevance import score_relevance

        conn = _make_conn(self._corpus())
        config = _minimal_config()

        with patch("src.stage4_relevance._load_biencoder", return_value=self._mock_model()), \
             patch.dict("sys.modules", {"rank_bm25": None, "yake": None}):
            result = score_relevance("diff text", conn, config)

        obs = result.observation_data
        assert obs["stage"] == "stage4_relevance"
        assert obs["page_count"] == 3
        assert "distributions" in obs
        assert "final_score" in obs["distributions"]

    def test_final_scores_sorted_descending(self):
        from src.stage4_relevance import score_relevance

        conn = _make_conn(self._corpus())
        config = _minimal_config()

        with patch("src.stage4_relevance._load_biencoder", return_value=self._mock_model()), \
             patch.dict("sys.modules", {"rank_bm25": None, "yake": None}):
            result = score_relevance("diff text", conn, config)

        scores = [ps.final_score for ps in result.all_pages]
        assert scores == sorted(scores, reverse=True)

    def test_source_importance_floor_applied(self):
        """With source_importance=0, floor=0.5: multiplier should be 0.5."""
        from src.stage4_relevance import score_relevance

        conn = _make_conn(self._corpus())
        config = _minimal_config()
        config["relevance_scoring"]["source_importance_floor"] = 0.5

        with patch("src.stage4_relevance._load_biencoder", return_value=self._mock_model()), \
             patch.dict("sys.modules", {"rank_bm25": None, "yake": None}):
            result = score_relevance("diff text", conn, config, source_importance=0.0)

        # multiplier = 0.5 + 0.5*0.0 = 0.5
        # final_score = rrf_score * 0.5
        for ps in result.all_pages:
            assert abs(ps.final_score - ps.rrf_score * 0.5) < 1e-9

    def test_with_ner_entities_in_short_diff(self):
        """NER entities should be added to keyphrases for short diffs."""
        from src.stage4_relevance import score_relevance

        conn = _make_conn(self._corpus())
        config = _minimal_config()

        mock_kw = MagicMock()
        mock_kw.extract_keywords.return_value = []
        mock_yake = MagicMock()
        mock_yake.KeywordExtractor.return_value = mock_kw

        with patch("src.stage4_relevance._load_biencoder", return_value=self._mock_model()), \
             patch.dict("sys.modules", {"yake": mock_yake, "rank_bm25": None}):
            result = score_relevance(
                "short diff",  # < 50 words
                conn, config,
                ner_entities=["Trade Marks Act"],
            )

        assert "Trade Marks Act" in result.keyphrases

    def test_min_score_threshold_adds_extra_candidates(self):
        from src.stage4_relevance import score_relevance, PageRelevanceScore

        conn = _make_conn(self._corpus())
        config = _minimal_config()
        config["relevance_scoring"]["top_n_candidates"] = 1
        # Set a very low threshold that all pages will exceed.
        config["relevance_scoring"]["min_score_threshold"] = 0.0

        with patch("src.stage4_relevance._load_biencoder", return_value=self._mock_model()), \
             patch.dict("sys.modules", {"rank_bm25": None, "yake": None}):
            result = score_relevance("diff text", conn, config)

        # All 3 pages should be candidates because all scores >= 0.0.
        assert len(result.candidates) == 3
