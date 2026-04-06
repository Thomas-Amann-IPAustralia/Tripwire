"""
ingestion/graph.py

Quasi-graph edge computation (Section 4.2 of the system plan).

Three edge sources:
  1. Embedding similarity (semantic) — cosine similarity between doc-level
     embeddings.  For each page, retain edges to the top-K most similar pages
     above a minimum similarity threshold.  Edge weight = cosine similarity.

  2. Entity overlap (conceptual) — Jaccard coefficient of named-entity sets
     between all pairs of pages.  Retain edges above a minimum Jaccard
     threshold.  Edge weight = Jaccard × scaling_factor.

  3. Internal links (structural) — DEFERRED: disabled in initial config.

Where multiple sources produce edges between the same pair, the maximum weight
across all sources is used (handled by db.upsert_graph_edge).
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from ingestion import db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def rebuild_graph(conn: Any, config: dict[str, Any]) -> dict[str, int]:
    """Recompute all quasi-graph edges and write them to the database.

    Clears existing edges of enabled types before recomputing.

    Returns
    -------
    dict
        Count of edges written per edge type.
    """
    from src.config import get

    graph_cfg = config.get("graph", {})
    edge_types = graph_cfg.get("edge_types", {})

    counts: dict[str, int] = {}

    # --- Embedding similarity ---
    emb_cfg = edge_types.get("embedding_similarity", {})
    if emb_cfg.get("enabled", True):
        db.clear_graph_edges(conn, edge_type="embedding_similarity")
        n = _build_embedding_edges(conn, emb_cfg)
        counts["embedding_similarity"] = n
        logger.info("Embedding similarity edges: %d written.", n)

    # --- Entity overlap ---
    ent_cfg = edge_types.get("entity_overlap", {})
    if ent_cfg.get("enabled", True):
        db.clear_graph_edges(conn, edge_type="entity_overlap")
        n = _build_entity_overlap_edges(conn, ent_cfg)
        counts["entity_overlap"] = n
        logger.info("Entity overlap edges: %d written.", n)

    # --- Internal links (deferred) ---
    link_cfg = edge_types.get("internal_links", {})
    if link_cfg.get("enabled", False):
        logger.warning("Internal link graph edges are not yet implemented — skipping.")

    conn.commit()
    return counts


# ---------------------------------------------------------------------------
# Embedding similarity edges
# ---------------------------------------------------------------------------


def _build_embedding_edges(conn: Any, cfg: dict[str, Any]) -> int:
    """Compute cosine similarities between all doc-level embeddings and store edges."""
    top_k: int = int(cfg.get("top_k", 5))
    min_sim: float = float(cfg.get("min_similarity", 0.40))
    weight_scale: float = float(cfg.get("weight", 1.0))

    rows = db.get_all_pages(conn)
    pages_with_embeddings = [
        (r["page_id"], r["doc_embedding"])
        for r in rows
        if r["doc_embedding"]
    ]

    if len(pages_with_embeddings) < 2:
        logger.info("Not enough pages with embeddings to compute similarity edges.")
        return 0

    page_ids = [p[0] for p in pages_with_embeddings]
    embeddings = np.stack([
        np.frombuffer(p[1], dtype=np.float32)
        for p in pages_with_embeddings
    ])

    # Normalise rows (in case stored embeddings aren't already unit-normed).
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    embeddings = embeddings / norms

    # Pairwise cosine similarity matrix.
    sim_matrix = embeddings @ embeddings.T

    edge_count = 0
    n = len(page_ids)

    for i in range(n):
        # Get top-K similarities for page_ids[i], excluding self (sim=1.0).
        sim_row = sim_matrix[i].copy()
        sim_row[i] = -1.0  # exclude self

        if top_k >= n - 1:
            candidate_indices = list(range(n))
            candidate_indices.remove(i)
        else:
            candidate_indices = list(np.argpartition(sim_row, -(top_k))[-top_k:])

        for j in candidate_indices:
            if j == i:
                continue
            sim = float(sim_row[j])
            if sim >= min_sim:
                weight = sim * weight_scale
                db.upsert_graph_edge(
                    conn,
                    source=page_ids[i],
                    target=page_ids[j],
                    edge_type="embedding_similarity",
                    weight=weight,
                )
                edge_count += 1

    return edge_count


# ---------------------------------------------------------------------------
# Entity overlap edges
# ---------------------------------------------------------------------------


def _build_entity_overlap_edges(conn: Any, cfg: dict[str, Any]) -> int:
    """Compute Jaccard entity-overlap edges between all pairs of pages."""
    min_jaccard: float = float(cfg.get("min_jaccard", 0.30))
    weight_scale: float = float(cfg.get("weight", 0.8))

    rows = db.get_all_pages(conn)
    page_ids = [r["page_id"] for r in rows]

    if len(page_ids) < 2:
        return 0

    # Build entity sets per page.
    entity_sets: dict[str, set[str]] = {}
    for pid in page_ids:
        entity_rows = db.get_entities_for_page(conn, pid)
        entity_sets[pid] = {r["entity_text"].lower() for r in entity_rows}

    edge_count = 0
    n = len(page_ids)

    for i in range(n):
        for j in range(i + 1, n):
            pid_a = page_ids[i]
            pid_b = page_ids[j]
            set_a = entity_sets[pid_a]
            set_b = entity_sets[pid_b]

            if not set_a or not set_b:
                continue

            jaccard = _jaccard(set_a, set_b)
            if jaccard >= min_jaccard:
                weight = jaccard * weight_scale
                db.upsert_graph_edge(conn, pid_a, pid_b, "entity_overlap", weight)
                db.upsert_graph_edge(conn, pid_b, pid_a, "entity_overlap", weight)
                edge_count += 2

    return edge_count


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _jaccard(a: set, b: set) -> float:
    """Compute the Jaccard coefficient of two sets."""
    union = len(a | b)
    if union == 0:
        return 0.0
    return len(a & b) / union
