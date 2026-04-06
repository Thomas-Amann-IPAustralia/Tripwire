"""
tests/test_config_validation.py

Tests for src/config.py — loading, validation, and snapshot.
No network calls; uses tmp_path and monkeypatch fixtures.
"""

import json
from pathlib import Path

import pytest
import yaml

from src.config import load_config, snapshot_config, get, ConfigError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_config(path: Path, cfg: dict) -> Path:
    """Write a YAML config dict to *path* and return the path."""
    with path.open("w") as fh:
        yaml.dump(cfg, fh)
    return path


def _minimal_valid_config() -> dict:
    """Return the smallest valid config dict for tests."""
    return {
        "pipeline": {
            "observation_mode": True,
            "run_frequency_hours": 24,
            "max_retries": 3,
            "retry_base_delay_seconds": 2.0,
            "llm_temperature": 0.2,
            "llm_model": "gpt-4o",
            "deferred_trigger_max_age_days": 7,
        },
        "change_detection": {
            "significance_fingerprint": True,
        },
        "relevance_scoring": {
            "rrf_k": 60,
            "rrf_weight_bm25": 1.0,
            "rrf_weight_semantic": 2.0,
            "top_n_candidates": 5,
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
            "edge_types": {
                "embedding_similarity": {"enabled": True, "weight": 1.0, "top_k": 5, "min_similarity": 0.40},
                "entity_overlap": {"enabled": True, "weight": 0.8, "min_jaccard": 0.30},
                "internal_links": {"enabled": False, "weight": 0.6},
            },
        },
        "storage": {
            "content_versions_retained": 6,
            "sqlite_wal_mode": True,
            "git_persistence": {
                "enabled": True,
                "commit_snapshots": True,
                "commit_database": True,
                "commit_author": "github-actions[bot] <github-actions[bot]@users.noreply.github.com>",
            },
        },
        "notifications": {
            "content_owner_email": "owner@example.com",
            "health_alert_email": "admin@example.com",
            "health_alert_conditions": {
                "error_rate_threshold": 0.30,
                "consecutive_failures_threshold": 3,
                "pipeline_timeout_minutes": 30,
            },
        },
        "normalisation": {"tool": "trafilatura"},
    }


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


def test_load_valid_config_from_repo_root():
    """The checked-in tripwire_config.yaml should pass validation."""
    cfg = load_config()
    assert isinstance(cfg, dict)
    assert cfg["pipeline"]["observation_mode"] is True
    assert cfg["pipeline"]["llm_model"] == "gpt-4o"


def test_load_valid_config_from_path(tmp_path):
    cfg_dict = _minimal_valid_config()
    cfg_file = _write_config(tmp_path / "config.yaml", cfg_dict)
    cfg = load_config(cfg_file)
    assert cfg["pipeline"]["max_retries"] == 3


def test_snapshot_config_is_valid_json(tmp_path):
    cfg_dict = _minimal_valid_config()
    cfg_file = _write_config(tmp_path / "config.yaml", cfg_dict)
    cfg = load_config(cfg_file)
    snap = snapshot_config(cfg)
    parsed = json.loads(snap)
    assert parsed["pipeline"]["llm_model"] == "gpt-4o"


def test_snapshot_does_not_mutate_original(tmp_path):
    cfg_dict = _minimal_valid_config()
    cfg_file = _write_config(tmp_path / "config.yaml", cfg_dict)
    cfg = load_config(cfg_file)
    original_model = cfg["pipeline"]["llm_model"]
    snap = snapshot_config(cfg)
    # Mutate the snapshot result; original must not change.
    parsed = json.loads(snap)
    parsed["pipeline"]["llm_model"] = "mutated"
    assert cfg["pipeline"]["llm_model"] == original_model


def test_get_nested_value(tmp_path):
    cfg_dict = _minimal_valid_config()
    cfg_file = _write_config(tmp_path / "config.yaml", cfg_dict)
    cfg = load_config(cfg_file)
    assert get(cfg, "relevance_scoring", "rrf_k") == 60
    assert get(cfg, "semantic_scoring", "biencoder", "model") == "BAAI/bge-base-en-v1.5"
    assert get(cfg, "nonexistent", "key", default="fallback") == "fallback"


def test_get_returns_default_for_missing_key(tmp_path):
    cfg_dict = _minimal_valid_config()
    cfg_file = _write_config(tmp_path / "config.yaml", cfg_dict)
    cfg = load_config(cfg_file)
    assert get(cfg, "does", "not", "exist", default=42) == 42


def test_min_score_threshold_null_is_valid(tmp_path):
    cfg_dict = _minimal_valid_config()
    cfg_dict["relevance_scoring"]["min_score_threshold"] = None
    cfg_file = _write_config(tmp_path / "config.yaml", cfg_dict)
    cfg = load_config(cfg_file)
    assert cfg["relevance_scoring"]["min_score_threshold"] is None


def test_min_score_threshold_numeric_is_valid(tmp_path):
    cfg_dict = _minimal_valid_config()
    cfg_dict["relevance_scoring"]["min_score_threshold"] = 0.4
    cfg_file = _write_config(tmp_path / "config.yaml", cfg_dict)
    cfg = load_config(cfg_file)
    assert cfg["relevance_scoring"]["min_score_threshold"] == 0.4


# ---------------------------------------------------------------------------
# Error cases — file-level
# ---------------------------------------------------------------------------


def test_missing_config_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.yaml")


def test_invalid_yaml_raises(tmp_path):
    bad_file = tmp_path / "bad.yaml"
    bad_file.write_text(": this: is: not: valid: yaml: {\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Failed to parse"):
        load_config(bad_file)


def test_non_mapping_yaml_raises(tmp_path):
    bad_file = tmp_path / "list.yaml"
    bad_file.write_text("- one\n- two\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(bad_file)


# ---------------------------------------------------------------------------
# Validation — missing sections
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("section", [
    "pipeline", "relevance_scoring", "semantic_scoring", "graph", "storage", "notifications"
])
def test_missing_required_section_raises(tmp_path, section):
    cfg_dict = _minimal_valid_config()
    del cfg_dict[section]
    cfg_file = _write_config(tmp_path / "config.yaml", cfg_dict)
    with pytest.raises(ConfigError, match=section):
        load_config(cfg_file)


# ---------------------------------------------------------------------------
# Validation — pipeline section
# ---------------------------------------------------------------------------


def test_observation_mode_must_be_bool(tmp_path):
    cfg = _minimal_valid_config()
    cfg["pipeline"]["observation_mode"] = "yes"
    with pytest.raises(ConfigError, match="observation_mode"):
        load_config(_write_config(tmp_path / "c.yaml", cfg))


def test_max_retries_negative_raises(tmp_path):
    cfg = _minimal_valid_config()
    cfg["pipeline"]["max_retries"] = -1
    with pytest.raises(ConfigError, match="max_retries"):
        load_config(_write_config(tmp_path / "c.yaml", cfg))


def test_llm_temperature_out_of_range_raises(tmp_path):
    cfg = _minimal_valid_config()
    cfg["pipeline"]["llm_temperature"] = 3.0
    with pytest.raises(ConfigError, match="llm_temperature"):
        load_config(_write_config(tmp_path / "c.yaml", cfg))


def test_empty_llm_model_raises(tmp_path):
    cfg = _minimal_valid_config()
    cfg["pipeline"]["llm_model"] = ""
    with pytest.raises(ConfigError, match="llm_model"):
        load_config(_write_config(tmp_path / "c.yaml", cfg))


# ---------------------------------------------------------------------------
# Validation — semantic scoring section
# ---------------------------------------------------------------------------


def test_unknown_biencoder_model_raises(tmp_path):
    cfg = _minimal_valid_config()
    cfg["semantic_scoring"]["biencoder"]["model"] = "unknown-model"
    with pytest.raises(ConfigError, match="biencoder.model"):
        load_config(_write_config(tmp_path / "c.yaml", cfg))


def test_high_threshold_above_one_raises(tmp_path):
    cfg = _minimal_valid_config()
    cfg["semantic_scoring"]["biencoder"]["high_threshold"] = 1.5
    with pytest.raises(ConfigError, match="high_threshold"):
        load_config(_write_config(tmp_path / "c.yaml", cfg))


def test_unknown_crossencoder_model_raises(tmp_path):
    cfg = _minimal_valid_config()
    cfg["semantic_scoring"]["crossencoder"]["model"] = "bad-reranker"
    with pytest.raises(ConfigError, match="crossencoder.model"):
        load_config(_write_config(tmp_path / "c.yaml", cfg))


# ---------------------------------------------------------------------------
# Validation — graph section
# ---------------------------------------------------------------------------


def test_graph_decay_zero_raises(tmp_path):
    cfg = _minimal_valid_config()
    cfg["graph"]["decay_per_hop"] = 0.0
    with pytest.raises(ConfigError, match="decay_per_hop"):
        load_config(_write_config(tmp_path / "c.yaml", cfg))


def test_graph_max_hops_zero_raises(tmp_path):
    cfg = _minimal_valid_config()
    cfg["graph"]["max_hops"] = 0
    with pytest.raises(ConfigError, match="max_hops"):
        load_config(_write_config(tmp_path / "c.yaml", cfg))


# ---------------------------------------------------------------------------
# Validation — notifications section
# ---------------------------------------------------------------------------


def test_invalid_email_raises(tmp_path):
    cfg = _minimal_valid_config()
    cfg["notifications"]["content_owner_email"] = "not-an-email"
    with pytest.raises(ConfigError, match="content_owner_email"):
        load_config(_write_config(tmp_path / "c.yaml", cfg))
