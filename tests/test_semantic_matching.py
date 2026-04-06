"""
tests/test_semantic_matching.py

Tests for Stage 5 (src/stage5_biencoder.py) and Stage 6
(src/stage6_crossencoder.py).

All tests use in-memory SQLite databases and mocked ML models.
No network calls.
"""

from __future__ import annotations

import math
import sqlite3
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Shared fixtures and helpers
# ---------------------------------------------------------------------------


def _make_embedding(values: list[float]) -> bytes:
    """Pack floats as a numpy float32 byte array."""
    return np.array(values, dtype=np.float32).tobytes()


def _make_corpus_conn(
    pages: list[dict],
    chunks: list[dict] | None = None,
    edges: list[dict] | None = None,
) -> sqlite3.Connection:
    """Create an in-memory SQLite DB with pages, chunks, and graph_edges tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.executescript("""
        CREATE TABLE pages (
            page_id       TEXT PRIMARY KEY,
            url           TEXT NOT NULL,
            title         TEXT NOT NULL,
            content       TEXT NOT NULL,
            version_hash  TEXT NOT NULL,
            last_modified TEXT,
            last_checked  TEXT,
            last_ingested TEXT,
            doc_embedding BLOB
        );
        CREATE TABLE chunks (
            chunk_id        TEXT PRIMARY KEY,
            page_id         TEXT NOT NULL,
            chunk_text      TEXT NOT NULL,
            chunk_index     INTEGER NOT NULL,
            section_heading TEXT,
            chunk_embedding BLOB NOT NULL
        );
        CREATE TABLE graph_edges (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            source_page_id TEXT NOT NULL,
            target_page_id TEXT NOT NULL,
            edge_type      TEXT NOT NULL,
            weight         REAL NOT NULL
        );
    """)

    for p in pages:
        conn.execute(
            "INSERT INTO pages VALUES (?,?,?,?,?,?,?,?,?)",
            (
                p["page_id"],
                p.get("url", "http://example.com"),
                p.get("title", p["page_id"]),
                p.get("content", "default content"),
                p.get("version_hash", "abc"),
                None, None, None,
                p.get("doc_embedding"),
            ),
        )

    for c in (chunks or []):
        conn.execute(
            "INSERT INTO chunks VALUES (?,?,?,?,?,?)",
            (
                c["chunk_id"],
                c["page_id"],
                c.get("chunk_text", "chunk text"),
                c.get("chunk_index", 0),
                c.get("section_heading"),
                c["chunk_embedding"],
            ),
        )

    for e in (edges or []):
        conn.execute(
            "INSERT INTO graph_edges (source_page_id, target_page_id, edge_type, weight) "
            "VALUES (?,?,?,?)",
            (e["source"], e["target"], e.get("edge_type", "embedding_similarity"), e["weight"]),
        )

    conn.commit()
    return conn


def _minimal_config(overrides: dict | None = None) -> dict:
    cfg = {
        "semantic_scoring": {
            "biencoder": {
                "model": "BAAI/bge-base-en-v1.5",
                "high_threshold": 0.75,
                "low_medium_threshold": 0.45,
                "low_medium_min_chunks": 3,
            },
            "crossencoder": {
                "model": "gte-reranker-modernbert-base",
                "threshold": 0.60,
                "max_context_tokens": 8192,
            },
        },
        "graph": {
            "enabled": True,
            "max_hops": 3,
            "decay_per_hop": 0.45,
            "propagation_threshold": 0.05,
        },
    }
    if overrides:
        for key, val in overrides.items():
            cfg[key] = val
    return cfg


# ===========================================================================
# Stage 5 — Bi-Encoder
# ===========================================================================


class TestChunkText:
    def test_empty_returns_empty(self):
        from src.stage5_biencoder import _chunk_text
        assert _chunk_text("") == []
        assert _chunk_text("   ") == []

    def test_short_text_single_chunk(self):
        from src.stage5_biencoder import _chunk_text
        result = _chunk_text("short text", chunk_size=512, overlap=64)
        assert result == ["short text"]

    def test_long_text_produces_multiple_chunks(self):
        from src.stage5_biencoder import _chunk_text
        text = "a" * 1200
        chunks = _chunk_text(text, chunk_size=512, overlap=64)
        assert len(chunks) >= 2

    def test_overlap_produces_prefix_continuation(self):
        from src.stage5_biencoder import _chunk_text
        text = "a" * 600
        chunks = _chunk_text(text, chunk_size=512, overlap=64)
        # Second chunk should start 512-64=448 chars into the text.
        assert len(chunks) == 2
        # The start of the second chunk is text[448:960].
        assert chunks[1] == text[448:960]

    def test_strips_whitespace_from_chunks(self):
        from src.stage5_biencoder import _chunk_text
        result = _chunk_text("  hello world  ", chunk_size=512, overlap=64)
        assert result == ["hello world"]


class TestEncodeTexts:
    def test_none_model_returns_none(self):
        from src.stage5_biencoder import _encode_texts
        result = _encode_texts(["text"], None)
        assert result is None

    def test_returns_float32_array(self):
        from src.stage5_biencoder import _encode_texts

        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        result = _encode_texts(["text a", "text b"], mock_model)
        assert result.dtype == np.float32
        assert result.shape == (2, 2)

    def test_encoding_failure_returns_none(self):
        from src.stage5_biencoder import _encode_texts

        mock_model = MagicMock()
        mock_model.encode.side_effect = RuntimeError("GPU OOM")
        result = _encode_texts(["text"], mock_model)
        assert result is None


class TestLoadCorpusChunks:
    def test_returns_all_chunks(self):
        from src.stage5_biencoder import _load_corpus_chunks

        emb = _make_embedding([0.5, 0.5])
        conn = _make_corpus_conn(
            pages=[{"page_id": "P1", "content": "x"}],
            chunks=[
                {"chunk_id": "P1-chunk-000", "page_id": "P1", "chunk_embedding": emb},
                {"chunk_id": "P1-chunk-001", "page_id": "P1", "chunk_embedding": emb},
            ],
        )
        result = _load_corpus_chunks(conn)
        assert len(result) == 2
        assert result[0]["chunk_id"] == "P1-chunk-000"

    def test_empty_table_returns_empty(self):
        from src.stage5_biencoder import _load_corpus_chunks
        conn = _make_corpus_conn(pages=[{"page_id": "P1", "content": "x"}])
        assert _load_corpus_chunks(conn) == []


class TestScorePages:
    def _make_change_embeddings(self, vec: list[float]) -> np.ndarray:
        return np.array([vec], dtype=np.float32)

    def test_high_threshold_triggers_single_chunk_high(self):
        from src.stage5_biencoder import _score_pages

        # Change embedding: [1, 0].  Corpus chunk embedding: [1, 0] → cosine=1.0.
        change_emb = self._make_change_embeddings([1.0, 0.0])
        corpus_chunks = [
            {
                "chunk_id": "A-chunk-000",
                "page_id": "A",
                "chunk_embedding": _make_embedding([1.0, 0.0]),
            }
        ]
        results = _score_pages(change_emb, corpus_chunks, 0.75, 0.45, 3)
        assert len(results) == 1
        assert results[0].trigger_reason == "single_chunk_high"
        assert results[0].max_chunk_score >= 0.75

    def test_low_medium_threshold_triggers_multi_chunk_medium(self):
        from src.stage5_biencoder import _score_pages

        # Change embedding [1, 0]; corpus chunk embeddings [0.7, 0.714] → cos ≈ 0.7.
        # All three chunks should score ~0.7 > low_medium_threshold=0.45.
        v = [0.7, 0.714]  # roughly normalised unit vector
        change_emb = self._make_change_embeddings([1.0, 0.0])
        corpus_chunks = [
            {"chunk_id": f"A-chunk-{i:03d}", "page_id": "A",
             "chunk_embedding": _make_embedding([0.6, 0.0])}
            for i in range(3)
        ]
        # cos([1,0],[0.6,0]) = 0.6 > 0.45.  3 chunks → multi_chunk_medium.
        results = _score_pages(change_emb, corpus_chunks, high_threshold=0.75,
                               low_medium_threshold=0.45, low_medium_min_chunks=3)
        assert len(results) == 1
        # max score = 0.6 < 0.75 → not single_chunk_high
        assert results[0].trigger_reason == "multi_chunk_medium"

    def test_no_trigger_below_thresholds(self):
        from src.stage5_biencoder import _score_pages

        # cos([1,0],[0,1]) = 0.0 < any threshold.
        change_emb = self._make_change_embeddings([1.0, 0.0])
        corpus_chunks = [
            {"chunk_id": "B-chunk-000", "page_id": "B",
             "chunk_embedding": _make_embedding([0.0, 1.0])}
        ]
        results = _score_pages(change_emb, corpus_chunks, 0.75, 0.45, 3)
        assert results[0].trigger_reason is None

    def test_multiple_pages_scored_independently(self):
        from src.stage5_biencoder import _score_pages

        change_emb = self._make_change_embeddings([1.0, 0.0])
        corpus_chunks = [
            {"chunk_id": "A-chunk-000", "page_id": "A",
             "chunk_embedding": _make_embedding([1.0, 0.0])},   # high match
            {"chunk_id": "B-chunk-000", "page_id": "B",
             "chunk_embedding": _make_embedding([0.0, 1.0])},   # no match
        ]
        results = _score_pages(change_emb, corpus_chunks, 0.75, 0.45, 3)
        by_id = {r.page_id: r for r in results}
        assert by_id["A"].trigger_reason == "single_chunk_high"
        assert by_id["B"].trigger_reason is None

    def test_single_chunk_high_takes_precedence_over_multi_chunk(self):
        from src.stage5_biencoder import _score_pages

        # 4 chunks all scoring 0.9 — max ≥ 0.75 so reason is single_chunk_high.
        change_emb = self._make_change_embeddings([1.0, 0.0])
        corpus_chunks = [
            {"chunk_id": f"A-chunk-{i:03d}", "page_id": "A",
             "chunk_embedding": _make_embedding([0.9, 0.0])}
            for i in range(4)
        ]
        results = _score_pages(change_emb, corpus_chunks, 0.75, 0.45, 3)
        assert results[0].trigger_reason == "single_chunk_high"


class TestScoreBiencoder:
    def test_no_change_text_returns_empty(self):
        from src.stage5_biencoder import score_biencoder

        conn = _make_corpus_conn(pages=[{"page_id": "A", "content": "x"}])
        config = _minimal_config()

        result = score_biencoder("", conn, config, model=None)
        assert result.candidate_pages == []
        assert "warning" in result.observation_data

    def test_empty_corpus_returns_empty(self):
        from src.stage5_biencoder import score_biencoder

        conn = _make_corpus_conn(pages=[{"page_id": "A", "content": "x"}])  # no chunks
        config = _minimal_config()
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([[1.0, 0.0]], dtype=np.float32)

        result = score_biencoder("some change text", conn, config, model=mock_model)
        assert result.candidate_pages == []

    def test_candidate_page_identified(self):
        from src.stage5_biencoder import score_biencoder

        # One corpus chunk aligned with the change document.
        emb = _make_embedding([1.0, 0.0])
        conn = _make_corpus_conn(
            pages=[{"page_id": "A", "content": "patent law"}],
            chunks=[{"chunk_id": "A-chunk-000", "page_id": "A", "chunk_embedding": emb}],
        )
        config = _minimal_config()

        mock_model = MagicMock()
        # Change chunk embedding [1.0, 0.0] → cosine with [1,0] = 1.0 > high_threshold.
        mock_model.encode.return_value = np.array([[1.0, 0.0]], dtype=np.float32)

        result = score_biencoder("patent examination", conn, config, model=mock_model)
        assert len(result.candidate_pages) == 1
        assert result.candidate_pages[0].page_id == "A"
        assert result.candidate_pages[0].trigger_reason == "single_chunk_high"

    def test_observation_data_populated(self):
        from src.stage5_biencoder import score_biencoder

        emb = _make_embedding([1.0, 0.0])
        conn = _make_corpus_conn(
            pages=[{"page_id": "A", "content": "x"}],
            chunks=[{"chunk_id": "A-chunk-000", "page_id": "A", "chunk_embedding": emb}],
        )
        config = _minimal_config()
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([[1.0, 0.0]], dtype=np.float32)

        result = score_biencoder("some text", conn, config, model=mock_model)
        obs = result.observation_data
        assert obs["stage"] == "stage5_biencoder"
        assert obs["page_count"] == 1

    def test_all_pages_in_all_pages(self):
        from src.stage5_biencoder import score_biencoder

        emb_a = _make_embedding([1.0, 0.0])
        emb_b = _make_embedding([0.0, 1.0])
        conn = _make_corpus_conn(
            pages=[
                {"page_id": "A", "content": "x"},
                {"page_id": "B", "content": "y"},
            ],
            chunks=[
                {"chunk_id": "A-chunk-000", "page_id": "A", "chunk_embedding": emb_a},
                {"chunk_id": "B-chunk-000", "page_id": "B", "chunk_embedding": emb_b},
            ],
        )
        config = _minimal_config()
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([[1.0, 0.0]], dtype=np.float32)

        result = score_biencoder("change text", conn, config, model=mock_model)
        all_ids = {r.page_id for r in result.all_pages}
        assert all_ids == {"A", "B"}

    def test_model_none_returns_empty(self):
        from src.stage5_biencoder import score_biencoder

        emb = _make_embedding([1.0, 0.0])
        conn = _make_corpus_conn(
            pages=[{"page_id": "A", "content": "x"}],
            chunks=[{"chunk_id": "A-chunk-000", "page_id": "A", "chunk_embedding": emb}],
        )
        config = _minimal_config()

        with patch("src.stage5_biencoder._load_biencoder", return_value=None):
            result = score_biencoder("change text", conn, config)

        assert result.candidate_pages == []
        assert "warning" in result.observation_data

    def test_sorted_by_max_chunk_score_descending(self):
        from src.stage5_biencoder import score_biencoder

        emb_a = _make_embedding([1.0, 0.0])    # will score 1.0 vs [1,0]
        emb_b = _make_embedding([0.5, 0.866])  # will score 0.5 vs [1,0]
        conn = _make_corpus_conn(
            pages=[
                {"page_id": "A", "content": "x"},
                {"page_id": "B", "content": "y"},
            ],
            chunks=[
                {"chunk_id": "A-chunk-000", "page_id": "A", "chunk_embedding": emb_a},
                {"chunk_id": "B-chunk-000", "page_id": "B", "chunk_embedding": emb_b},
            ],
        )
        config = _minimal_config()
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([[1.0, 0.0]], dtype=np.float32)

        result = score_biencoder("change text", conn, config, model=mock_model)
        scores = [r.max_chunk_score for r in result.all_pages]
        assert scores == sorted(scores, reverse=True)


# ===========================================================================
# Stage 6 — Cross-Encoder
# ===========================================================================


class TestSigmoid:
    def test_zero_gives_half(self):
        from src.stage6_crossencoder import _sigmoid
        assert abs(_sigmoid(0.0) - 0.5) < 1e-9

    def test_large_positive_approaches_one(self):
        from src.stage6_crossencoder import _sigmoid
        assert _sigmoid(100.0) > 0.99

    def test_large_negative_approaches_zero(self):
        from src.stage6_crossencoder import _sigmoid
        assert _sigmoid(-100.0) < 0.01

    def test_symmetric(self):
        from src.stage6_crossencoder import _sigmoid
        assert abs(_sigmoid(2.0) - (1 - _sigmoid(-2.0))) < 1e-9


class TestEstimateTokens:
    def test_empty_string_returns_one(self):
        from src.stage6_crossencoder import _estimate_tokens
        assert _estimate_tokens("") == 1

    def test_400_chars_approx_100_tokens(self):
        from src.stage6_crossencoder import _estimate_tokens
        assert _estimate_tokens("a" * 400) == 100


class TestNormaliseScores:
    def test_empty_returns_empty(self):
        from src.stage6_crossencoder import _normalise_scores
        assert _normalise_scores({}) == {}

    def test_all_equal_returns_zeros(self):
        from src.stage6_crossencoder import _normalise_scores
        result = _normalise_scores({"A": 0.5, "B": 0.5})
        assert result == {"A": 0.0, "B": 0.0}

    def test_normalises_to_zero_one(self):
        from src.stage6_crossencoder import _normalise_scores
        result = _normalise_scores({"A": 0.0, "B": 0.5, "C": 1.0})
        assert result["A"] == 0.0
        assert result["C"] == 1.0
        assert abs(result["B"] - 0.5) < 1e-9

    def test_single_entry(self):
        from src.stage6_crossencoder import _normalise_scores
        result = _normalise_scores({"A": 0.7})
        assert result == {"A": 0.0}


class TestPropagateGraph:
    def _edges(self, source: str, targets: list[tuple[str, float]]) -> dict:
        """Build a graph_edges dict from simple (target, weight) tuples."""
        return {
            source: [
                {"target_page_id": t, "edge_type": "embedding_similarity", "weight": w}
                for t, w in targets
            ]
        }

    def test_single_hop_propagation(self):
        from src.stage6_crossencoder import _propagate_graph

        edges = self._edges("A", [("B", 1.0)])
        result = _propagate_graph({"A": 0.8}, edges, max_hops=1, decay_per_hop=0.45,
                                  propagation_threshold=0.05)
        # signal = 0.8 * 1.0 * 0.45 / 1 = 0.36
        assert "B" in result
        assert abs(result["B"] - 0.36) < 1e-9

    def test_out_degree_normalisation(self):
        from src.stage6_crossencoder import _propagate_graph

        # Source A → B and C with weight 1.0 each.
        edges = {"A": [
            {"target_page_id": "B", "edge_type": "emb", "weight": 1.0},
            {"target_page_id": "C", "edge_type": "emb", "weight": 1.0},
        ]}
        result = _propagate_graph({"A": 1.0}, edges, max_hops=1, decay_per_hop=0.45,
                                  propagation_threshold=0.05)
        # out_degree = 2; signal = 1.0 * 1.0 * 0.45 / 2 = 0.225
        assert abs(result["B"] - 0.225) < 1e-9
        assert abs(result["C"] - 0.225) < 1e-9

    def test_stops_below_propagation_threshold(self):
        from src.stage6_crossencoder import _propagate_graph

        edges = self._edges("A", [("B", 0.01)])
        # signal = 0.1 * 0.01 * 0.45 / 1 = 0.00045 < 0.05 threshold
        result = _propagate_graph({"A": 0.1}, edges, max_hops=3, decay_per_hop=0.45,
                                  propagation_threshold=0.05)
        assert "B" not in result

    def test_max_hops_respected(self):
        from src.stage6_crossencoder import _propagate_graph

        # A → B → C → D with 1.0 weights
        edges = {
            "A": [{"target_page_id": "B", "edge_type": "e", "weight": 1.0}],
            "B": [{"target_page_id": "C", "edge_type": "e", "weight": 1.0}],
            "C": [{"target_page_id": "D", "edge_type": "e", "weight": 1.0}],
        }
        # With max_hops=2 and starting score=1.0, D should NOT receive a signal.
        result = _propagate_graph({"A": 1.0}, edges, max_hops=2, decay_per_hop=0.45,
                                  propagation_threshold=0.0)
        assert "B" in result
        assert "C" in result
        assert "D" not in result

    def test_two_hop_decay(self):
        from src.stage6_crossencoder import _propagate_graph

        edges = {
            "A": [{"target_page_id": "B", "edge_type": "e", "weight": 1.0}],
            "B": [{"target_page_id": "C", "edge_type": "e", "weight": 1.0}],
        }
        result = _propagate_graph({"A": 1.0}, edges, max_hops=3, decay_per_hop=0.45,
                                  propagation_threshold=0.0)
        # Hop 1: B gets 1.0 * 1.0 * 0.45 / 1 = 0.45
        # Hop 2: C gets 0.45 * 1.0 * 0.45 / 1 = 0.2025
        assert abs(result["B"] - 0.45) < 1e-9
        assert abs(result["C"] - 0.2025) < 1e-9

    def test_no_source_nodes_returns_empty(self):
        from src.stage6_crossencoder import _propagate_graph

        result = _propagate_graph({}, {}, max_hops=3, decay_per_hop=0.45,
                                  propagation_threshold=0.05)
        assert result == {}

    def test_accumulates_from_multiple_sources(self):
        from src.stage6_crossencoder import _propagate_graph

        # Both A and B point to C.
        edges = {
            "A": [{"target_page_id": "C", "edge_type": "e", "weight": 1.0}],
            "B": [{"target_page_id": "C", "edge_type": "e", "weight": 1.0}],
        }
        result = _propagate_graph({"A": 1.0, "B": 1.0}, edges, max_hops=1,
                                  decay_per_hop=0.45, propagation_threshold=0.0)
        # C should get 0.45 + 0.45 = 0.90
        assert abs(result["C"] - 0.90) < 1e-9


class TestScorePair:
    def test_none_model_returns_half(self):
        from src.stage6_crossencoder import _score_pair
        assert _score_pair("page content", "change text", None) == 0.5

    def test_sigmoid_applied_to_logit(self):
        from src.stage6_crossencoder import _score_pair, _sigmoid

        mock_model = MagicMock()
        mock_model.predict.return_value = [2.0]   # logit = 2.0

        score = _score_pair("page", "change", mock_model)
        assert abs(score - _sigmoid(2.0)) < 1e-6

    def test_prediction_failure_returns_half(self):
        from src.stage6_crossencoder import _score_pair

        mock_model = MagicMock()
        mock_model.predict.side_effect = RuntimeError("model error")

        score = _score_pair("page", "change", mock_model)
        assert score == 0.5


class TestScoreCrossencoder:
    def _mock_crossencoder(self, logit: float = 1.0) -> MagicMock:
        m = MagicMock()
        m.predict.return_value = [logit]
        return m

    def test_no_candidates_returns_empty(self):
        from src.stage6_crossencoder import score_crossencoder

        conn = _make_corpus_conn(pages=[{"page_id": "A", "content": "x"}])
        config = _minimal_config()

        result = score_crossencoder([], "change text", conn, config)
        assert result.confirmed_pages == []
        assert "warning" in result.observation_data

    def test_page_above_threshold_is_confirmed(self):
        from src.stage6_crossencoder import score_crossencoder, _sigmoid

        conn = _make_corpus_conn(pages=[{"page_id": "A", "content": "patent content"}])
        config = _minimal_config()
        config["semantic_scoring"]["crossencoder"]["threshold"] = 0.50

        # logit=2.0 → sigmoid=0.88 > 0.50 → should be confirmed.
        result = score_crossencoder(
            ["A"], "change text", conn, config,
            model=self._mock_crossencoder(logit=2.0),
        )
        assert len(result.confirmed_pages) == 1
        assert result.confirmed_pages[0].page_id == "A"
        assert result.confirmed_pages[0].decision == "proceed"

    def test_page_below_threshold_is_rejected(self):
        from src.stage6_crossencoder import score_crossencoder

        conn = _make_corpus_conn(pages=[{"page_id": "A", "content": "patent content"}])
        config = _minimal_config()
        config["semantic_scoring"]["crossencoder"]["threshold"] = 0.90

        # logit=0.0 → sigmoid=0.5 < 0.90 → should be rejected.
        result = score_crossencoder(
            ["A"], "change text", conn, config,
            model=self._mock_crossencoder(logit=0.0),
        )
        assert len(result.confirmed_pages) == 0
        assert result.all_scored[0].decision == "rejected"

    def test_truncation_warning_logged_for_long_content(self):
        from src.stage6_crossencoder import score_crossencoder

        # Create a page with content that will exceed the token limit.
        long_content = "x " * 20000  # ~40,000 chars → ~10,000 tokens
        conn = _make_corpus_conn(pages=[{"page_id": "A", "content": long_content}])
        config = _minimal_config()
        config["semantic_scoring"]["crossencoder"]["max_context_tokens"] = 100

        result = score_crossencoder(
            ["A"], "change", conn, config,
            model=self._mock_crossencoder(),
        )
        assert result.all_scored[0].truncation_warning is True

    def test_no_truncation_warning_for_short_content(self):
        from src.stage6_crossencoder import score_crossencoder

        conn = _make_corpus_conn(pages=[{"page_id": "A", "content": "short content"}])
        config = _minimal_config()

        result = score_crossencoder(
            ["A"], "short change", conn, config,
            model=self._mock_crossencoder(),
        )
        assert result.all_scored[0].truncation_warning is False

    def test_stage4_lexical_scores_influence_rerank(self):
        from src.stage6_crossencoder import score_crossencoder

        conn = _make_corpus_conn(pages=[
            {"page_id": "A", "content": "alpha"},
            {"page_id": "B", "content": "beta"},
        ])
        config = _minimal_config()
        config["semantic_scoring"]["crossencoder"]["threshold"] = 0.0

        # Same cross-encoder score for both, but A has higher lexical score.
        stage4_scores = {"A": 0.9, "B": 0.1}

        result = score_crossencoder(
            ["A", "B"], "change text", conn, config,
            stage4_scores=stage4_scores,
            model=self._mock_crossencoder(logit=0.0),
        )
        by_id = {r.page_id: r for r in result.all_scored}
        assert by_id["A"].reranked_score > by_id["B"].reranked_score

    def test_graph_propagation_adds_extra_pages(self):
        from src.stage6_crossencoder import score_crossencoder

        conn = _make_corpus_conn(
            pages=[
                {"page_id": "A", "content": "direct page"},
                {"page_id": "B", "content": "related page"},
            ],
            edges=[{"source": "A", "target": "B", "weight": 1.0}],
        )
        config = _minimal_config()
        config["graph"]["decay_per_hop"] = 0.45
        config["graph"]["propagation_threshold"] = 0.0
        config["semantic_scoring"]["crossencoder"]["threshold"] = 0.0

        # logit=2.0 → sigmoid≈0.88.  Propagated signal to B should be added.
        result = score_crossencoder(
            ["A"], "change text", conn, config,
            model=self._mock_crossencoder(logit=2.0),
        )
        direct_ids = {r.page_id for r in result.all_scored}
        assert "A" in direct_ids
        # B was not a direct candidate but may appear as graph-propagated.
        propagated_ids = {r.page_id for r in result.graph_propagated_pages}
        assert "B" in propagated_ids

    def test_graph_disabled_no_propagation(self):
        from src.stage6_crossencoder import score_crossencoder

        conn = _make_corpus_conn(
            pages=[
                {"page_id": "A", "content": "direct page"},
                {"page_id": "B", "content": "related page"},
            ],
            edges=[{"source": "A", "target": "B", "weight": 1.0}],
        )
        config = _minimal_config()
        config["graph"]["enabled"] = False

        result = score_crossencoder(
            ["A"], "change text", conn, config,
            model=self._mock_crossencoder(logit=2.0),
        )
        assert result.graph_propagated_pages == []

    def test_observation_data_populated(self):
        from src.stage6_crossencoder import score_crossencoder

        conn = _make_corpus_conn(pages=[{"page_id": "A", "content": "x"}])
        config = _minimal_config()

        result = score_crossencoder(
            ["A"], "change", conn, config,
            model=self._mock_crossencoder(),
        )
        obs = result.observation_data
        assert obs["stage"] == "stage6_crossencoder"
        assert obs["candidates_scored"] == 1
        assert "distributions" in obs

    def test_all_scored_sorted_descending(self):
        from src.stage6_crossencoder import score_crossencoder

        conn = _make_corpus_conn(pages=[
            {"page_id": "A", "content": "alpha"},
            {"page_id": "B", "content": "beta"},
            {"page_id": "C", "content": "gamma"},
        ])
        config = _minimal_config()
        config["semantic_scoring"]["crossencoder"]["threshold"] = 0.0

        # Different stage4 scores so reranked scores will differ.
        stage4_scores = {"A": 1.0, "B": 0.5, "C": 0.0}

        result = score_crossencoder(
            ["A", "B", "C"], "change", conn, config,
            stage4_scores=stage4_scores,
            model=self._mock_crossencoder(logit=0.0),
        )
        final_scores = [r.final_score for r in result.all_scored]
        assert final_scores == sorted(final_scores, reverse=True)

    def test_missing_page_in_db_skipped(self):
        """A candidate_page_id not found in the DB should be silently skipped."""
        from src.stage6_crossencoder import score_crossencoder

        conn = _make_corpus_conn(pages=[{"page_id": "A", "content": "alpha"}])
        config = _minimal_config()

        result = score_crossencoder(
            ["A", "NONEXISTENT"], "change", conn, config,
            model=self._mock_crossencoder(),
        )
        ids = {r.page_id for r in result.all_scored}
        assert "NONEXISTENT" not in ids
        assert "A" in ids

    def test_graph_boost_is_additive_only(self):
        """Graph-propagated signals should never lower a direct score."""
        from src.stage6_crossencoder import score_crossencoder, _sigmoid

        conn = _make_corpus_conn(
            pages=[
                {"page_id": "A", "content": "direct"},
                {"page_id": "B", "content": "related"},
            ],
            edges=[{"source": "A", "target": "B", "weight": 0.5}],
        )
        config = _minimal_config()
        config["semantic_scoring"]["crossencoder"]["threshold"] = 0.0

        result = score_crossencoder(
            ["A", "B"], "change", conn, config,
            model=self._mock_crossencoder(logit=0.0),
        )
        by_id = {r.page_id: r for r in result.all_scored}
        # B's final_score should be >= its reranked_score (boost is additive).
        assert by_id["B"].final_score >= by_id["B"].reranked_score
