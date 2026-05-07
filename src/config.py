"""
src/config.py

Load, validate, and snapshot the tripwire_config.yaml configuration file.
Validation runs at the start of every pipeline run; any failure exits early
with a clear message before any sources are processed.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent
_DEFAULT_CONFIG_PATH = _REPO_ROOT / "tripwire_config.yaml"

# Model names accepted by the system (extend as new models are supported).
_KNOWN_BIENCODER_MODELS = {"BAAI/bge-base-en-v1.5"}
_KNOWN_CROSSENCODER_MODELS = {"Alibaba-NLP/gte-reranker-modernbert-base"}


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load tripwire_config.yaml and return the validated config dict.

    Parameters
    ----------
    config_path:
        Path to the YAML file. Defaults to ``tripwire_config.yaml`` in the
        repository root.

    Returns
    -------
    dict
        Fully-validated configuration.

    Raises
    ------
    ConfigError
        If the file is missing, unparseable, or fails validation.
    """
    path = Path(config_path) if config_path is not None else _DEFAULT_CONFIG_PATH

    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse config file: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("Config file must be a YAML mapping at the top level.")

    _validate(raw)
    return raw


def snapshot_config(config: dict[str, Any]) -> str:
    """Return a compact JSON string of the config for embedding in run logs."""
    return json.dumps(copy.deepcopy(config), sort_keys=True)


def get(config: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Safely retrieve a nested value using dot-style key traversal.

    Example
    -------
    >>> get(cfg, "relevance_scoring", "rrf_k", default=60)
    60
    """
    node = config
    for key in keys:
        if not isinstance(node, dict):
            return default
        node = node.get(key, default)
        if node is default:
            return default
    return node


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class ConfigError(ValueError):
    """Raised when the configuration file fails to load or validate."""


def _validate(cfg: dict[str, Any]) -> None:
    """Validate the configuration dict in-place (raises ConfigError on failure)."""
    _require_section(cfg, "pipeline")
    _require_section(cfg, "relevance_scoring")
    _require_section(cfg, "semantic_scoring")
    _require_section(cfg, "graph")
    _require_section(cfg, "storage")
    _require_section(cfg, "notifications")

    _validate_pipeline(cfg["pipeline"])
    _validate_relevance_scoring(cfg["relevance_scoring"])
    _validate_semantic_scoring(cfg["semantic_scoring"])
    _validate_graph(cfg["graph"])
    _validate_storage(cfg["storage"])
    _validate_notifications(cfg["notifications"])


def _require_section(cfg: dict, section: str) -> None:
    if section not in cfg:
        raise ConfigError(f"Missing required config section: '{section}'")
    if not isinstance(cfg[section], dict):
        raise ConfigError(f"Config section '{section}' must be a mapping.")


def _validate_pipeline(p: dict) -> None:
    _require_keys(p, "pipeline", ["observation_mode", "max_retries",
                                   "retry_base_delay_seconds", "llm_temperature",
                                   "llm_model"])

    if not isinstance(p["observation_mode"], bool):
        raise ConfigError("pipeline.observation_mode must be a boolean.")
    if not isinstance(p["max_retries"], int) or p["max_retries"] < 0:
        raise ConfigError("pipeline.max_retries must be a non-negative integer.")
    if not _is_positive_number(p["retry_base_delay_seconds"]):
        raise ConfigError("pipeline.retry_base_delay_seconds must be a positive number.")
    temp = p["llm_temperature"]
    if not isinstance(temp, (int, float)) or not (0.0 <= temp <= 2.0):
        raise ConfigError("pipeline.llm_temperature must be a float in [0.0, 2.0].")
    if not isinstance(p["llm_model"], str) or not p["llm_model"].strip():
        raise ConfigError("pipeline.llm_model must be a non-empty string.")


def _validate_relevance_scoring(rs: dict) -> None:
    _require_keys(rs, "relevance_scoring", ["rrf_k", "rrf_weight_bm25",
                                             "rrf_weight_semantic", "top_n_candidates",
                                             "source_importance_floor"])

    if not isinstance(rs["rrf_k"], int) or rs["rrf_k"] <= 0:
        raise ConfigError("relevance_scoring.rrf_k must be a positive integer.")

    w_bm25 = rs["rrf_weight_bm25"]
    w_sem = rs["rrf_weight_semantic"]
    if not _is_non_negative_number(w_bm25):
        raise ConfigError("relevance_scoring.rrf_weight_bm25 must be a non-negative number.")
    if not _is_non_negative_number(w_sem):
        raise ConfigError("relevance_scoring.rrf_weight_semantic must be a non-negative number.")

    if not isinstance(rs["top_n_candidates"], int) or rs["top_n_candidates"] <= 0:
        raise ConfigError("relevance_scoring.top_n_candidates must be a positive integer.")

    floor = rs["source_importance_floor"]
    if not isinstance(floor, (int, float)) or not (0.0 <= floor <= 1.0):
        raise ConfigError("relevance_scoring.source_importance_floor must be in [0.0, 1.0].")

    threshold = rs.get("min_score_threshold")
    if threshold is not None:
        if not _is_non_negative_number(threshold):
            raise ConfigError("relevance_scoring.min_score_threshold must be null or a non-negative number.")


def _validate_semantic_scoring(ss: dict) -> None:
    _require_section(ss, "biencoder")
    _require_section(ss, "crossencoder")

    bi = ss["biencoder"]
    _require_keys(bi, "semantic_scoring.biencoder",
                  ["model", "high_threshold", "low_medium_threshold", "low_medium_min_chunks"])
    if bi["model"] not in _KNOWN_BIENCODER_MODELS:
        raise ConfigError(
            f"semantic_scoring.biencoder.model '{bi['model']}' is not recognised. "
            f"Known models: {sorted(_KNOWN_BIENCODER_MODELS)}"
        )
    _require_threshold_float(bi, "semantic_scoring.biencoder", "high_threshold")
    _require_threshold_float(bi, "semantic_scoring.biencoder", "low_medium_threshold")
    if not isinstance(bi["low_medium_min_chunks"], int) or bi["low_medium_min_chunks"] <= 0:
        raise ConfigError("semantic_scoring.biencoder.low_medium_min_chunks must be a positive integer.")

    ce = ss["crossencoder"]
    _require_keys(ce, "semantic_scoring.crossencoder",
                  ["model", "threshold", "max_context_tokens"])
    if ce["model"] not in _KNOWN_CROSSENCODER_MODELS:
        raise ConfigError(
            f"semantic_scoring.crossencoder.model '{ce['model']}' is not recognised. "
            f"Known models: {sorted(_KNOWN_CROSSENCODER_MODELS)}"
        )
    _require_threshold_float(ce, "semantic_scoring.crossencoder", "threshold")
    if not isinstance(ce["max_context_tokens"], int) or ce["max_context_tokens"] <= 0:
        raise ConfigError("semantic_scoring.crossencoder.max_context_tokens must be a positive integer.")


def _validate_graph(g: dict) -> None:
    _require_keys(g, "graph", ["enabled", "max_hops", "decay_per_hop", "propagation_threshold"])

    if not isinstance(g["enabled"], bool):
        raise ConfigError("graph.enabled must be a boolean.")
    if not isinstance(g["max_hops"], int) or g["max_hops"] < 1:
        raise ConfigError("graph.max_hops must be a positive integer.")
    decay = g["decay_per_hop"]
    if not isinstance(decay, (int, float)) or not (0.0 < decay <= 1.0):
        raise ConfigError("graph.decay_per_hop must be a float in (0.0, 1.0].")
    floor = g["propagation_threshold"]
    if not isinstance(floor, (int, float)) or not (0.0 <= floor < 1.0):
        raise ConfigError("graph.propagation_threshold must be a float in [0.0, 1.0).")


def _validate_storage(s: dict) -> None:
    _require_keys(s, "storage", ["content_versions_retained", "sqlite_wal_mode"])
    if not isinstance(s["content_versions_retained"], int) or s["content_versions_retained"] < 1:
        raise ConfigError("storage.content_versions_retained must be a positive integer.")
    if not isinstance(s["sqlite_wal_mode"], bool):
        raise ConfigError("storage.sqlite_wal_mode must be a boolean.")


def _validate_notifications(n: dict) -> None:
    _require_keys(n, "notifications", ["content_owner_email", "health_alert_email"])
    for key in ("content_owner_email", "health_alert_email"):
        if not isinstance(n[key], str) or "@" not in n[key]:
            raise ConfigError(f"notifications.{key} must be a valid email address string.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_keys(d: dict, section: str, keys: list[str]) -> None:
    missing = [k for k in keys if k not in d]
    if missing:
        raise ConfigError(
            f"Config section '{section}' is missing required keys: {missing}"
        )


def _is_positive_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and v > 0


def _is_non_negative_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and v >= 0


def _require_threshold_float(d: dict, section: str, key: str) -> None:
    v = d[key]
    if not isinstance(v, (int, float)) or not (0.0 <= v <= 1.0):
        raise ConfigError(f"{section}.{key} must be a float in [0.0, 1.0].")
