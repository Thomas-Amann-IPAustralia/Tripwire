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

  3. Internal links (structural) — directed edges from a page to every other
     IPFR page it hyperlinks to.  Link targets are captured at scrape time
     (see scrape_ipfr.extract_internal_links), persisted in the page_links
     table, and resolved to page_ids here against the pages table.  Edge weight
     is a configurable constant (default 0.6).

Where multiple sources produce edges between the same pair, the maximum weight
across all sources is used (handled by db.upsert_graph_edge).
"""

from __future__ import annotations

import logging
from typing import Any

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

    # --- Internal links (structural) ---
    link_cfg = edge_types.get("internal_links", {})
    if link_cfg.get("enabled", False):
        db.clear_graph_edges(conn, edge_type="internal_link")
        n = _build_internal_link_edges(conn, link_cfg)
        counts["internal_link"] = n
        logger.info("Internal link edges: %d written.", n)

    conn.commit()
    return counts


# ---------------------------------------------------------------------------
# Embedding similarity edges
# ---------------------------------------------------------------------------


def _build_embedding_edges(conn: Any, cfg: dict[str, Any]) -> int:
    """Compute cosine similarities between all doc-level embeddings and store edges."""
    try:
        import numpy as np
    except ImportError:
        logger.warning(
            "numpy not installed. Embedding similarity edges will be skipped. "
            "Install with: pip install numpy"
        )
        return 0

    top_k: int = int(cfg.get("top_k", 5))
    min_sim: float = float(cfg.get("min_similarity", 0.40))
    weight_scale: float = float(cfg.get("weight", 1.0))

    rows = db.get_all_pages(conn)
    pages_with_embeddings = [
        (r["page_id"], r["doc_embedding"])
        for r in rows
        if r["doc_embedding"] and _is_active(r)
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
    page_ids = [r["page_id"] for r in rows if _is_active(r)]

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
# Internal link edges
# ---------------------------------------------------------------------------


def _build_internal_link_edges(conn: Any, cfg: dict[str, Any]) -> int:
    """Build directed edges from each page to the IPFR pages it links to.

    Outbound link targets were captured at scrape time and stored as normalised
    URLs in the ``page_links`` table.  Here we resolve those URLs to page_ids
    against the active corpus and emit one directed edge (source → target) per
    resolved link.  Links to stub/duplicate pages, to pages outside the corpus,
    and self-links are dropped.  Edge weight is the configured constant.

    Site-wide navigation/footer links (Privacy, Disclaimers, the navigator tool,
    …) appear on nearly every page and would otherwise swamp the graph with
    high-degree hubs that carry no topical signal.  These are removed with a
    corpus-wide document-frequency filter, mirroring the boilerplate-line and
    keyphrase-IDF filters used elsewhere in ingestion: a target linked from more
    than ``nav_link_df_threshold`` of the active corpus is treated as chrome and
    dropped.  The filter only engages once the corpus has at least ``min_pages``
    pages so small/bootstrap corpora are left intact.
    """
    from ingestion.scrape_ipfr import normalise_url

    weight: float = float(cfg.get("weight", 0.6))
    df_threshold: float = float(cfg.get("nav_link_df_threshold", 0.5))
    min_pages: int = int(cfg.get("min_pages", 5))

    rows = db.get_all_pages(conn)
    active_ids = {r["page_id"] for r in rows if _is_active(r)}

    # Map every active page's canonical URL to its page_id so link targets
    # (also normalised at extraction time) resolve regardless of cosmetic
    # differences.  normalise_url is idempotent, so re-normalising is safe.
    url_to_id: dict[str, str] = {}
    for r in rows:
        if r["page_id"] in active_ids and r["url"]:
            url_to_id[normalise_url(r["url"])] = r["page_id"]

    # First pass: resolve every link to a (source, target) pair and count each
    # target's document frequency (distinct linking pages).
    resolved: list[tuple[str, str]] = []
    target_sources: dict[str, set[str]] = {}
    for source_id in active_ids:
        for link_row in db.get_page_links_for_page(conn, source_id):
            target_id = url_to_id.get(normalise_url(link_row["target_url"]))
            if target_id is None or target_id == source_id:
                continue
            resolved.append((source_id, target_id))
            target_sources.setdefault(target_id, set()).add(source_id)

    # Identify nav/footer targets by document frequency.
    n_active = len(active_ids)
    nav_targets: set[str] = set()
    if n_active >= min_pages:
        cutoff = df_threshold * n_active
        nav_targets = {t for t, srcs in target_sources.items() if len(srcs) > cutoff}
        if nav_targets:
            logger.info(
                "Internal links: dropping %d site-wide nav/footer target(s) "
                "linked from >%.0f%% of %d pages: %s",
                len(nav_targets), df_threshold * 100, n_active,
                ", ".join(sorted(nav_targets)),
            )

    # Second pass: emit edges for the remaining (content) links.
    edge_count = 0
    for source_id, target_id in resolved:
        if target_id in nav_targets:
            continue
        db.upsert_graph_edge(
            conn,
            source=source_id,
            target=target_id,
            edge_type="internal_link",
            weight=weight,
        )
        edge_count += 1

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


def _is_active(row: Any) -> bool:
    """Return True if *row* represents an active page (not stub / duplicate).

    Tolerates rows from older schemas that pre-date the status column.
    """
    try:
        status = row["status"]
    except (IndexError, KeyError):
        return True
    return status == "active" or status is None
