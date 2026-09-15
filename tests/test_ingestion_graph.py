"""
tests/test_ingestion_graph.py

Tests for the internal-link quasi-graph edges (plan task 5.5).

Covers the page_links DB helpers and ingestion.graph._build_internal_link_edges /
rebuild_graph resolution of stored link URLs into directed "internal_link" edges.
No network is used — pages and links are inserted directly.
"""

from __future__ import annotations

import pytest

from ingestion import db, graph


BASE = "https://ipfirstresponse.ipaustralia.gov.au"


@pytest.fixture
def conn(tmp_path):
    c = db.init_db(tmp_path / "ipfr.sqlite")
    yield c
    c.close()


def _add_page(c, page_id, path, status="active"):
    db.upsert_page(c, {
        "page_id": page_id,
        "url": f"{BASE}{path}",
        "title": f"Title {page_id}",
        "content": "x" * 600,
        "version_hash": page_id,
        "status": status,
    })


# ---------------------------------------------------------------------------
# page_links DB helpers
# ---------------------------------------------------------------------------


def test_replace_page_links_roundtrip(conn):
    _add_page(conn, "A0001", "/a")
    db.replace_page_links(conn, "A0001", [f"{BASE}/b", f"{BASE}/c"])
    rows = db.get_page_links_for_page(conn, "A0001")
    assert [r["target_url"] for r in rows] == [f"{BASE}/b", f"{BASE}/c"]


def test_replace_page_links_overwrites_previous(conn):
    _add_page(conn, "A0001", "/a")
    db.replace_page_links(conn, "A0001", [f"{BASE}/b"])
    db.replace_page_links(conn, "A0001", [f"{BASE}/c", f"{BASE}/d"])
    rows = db.get_page_links_for_page(conn, "A0001")
    assert [r["target_url"] for r in rows] == [f"{BASE}/c", f"{BASE}/d"]


def test_replace_page_links_dedupes_and_ignores_empty(conn):
    _add_page(conn, "A0001", "/a")
    db.replace_page_links(conn, "A0001", [f"{BASE}/b", f"{BASE}/b", "", None])
    rows = db.get_page_links_for_page(conn, "A0001")
    assert [r["target_url"] for r in rows] == [f"{BASE}/b"]


# ---------------------------------------------------------------------------
# Internal-link edge building
# ---------------------------------------------------------------------------


def _link_cfg(enabled=True, weight=0.6, **extra):
    internal = {"enabled": enabled, "weight": weight}
    internal.update(extra)
    return {"graph": {"edge_types": {
        "embedding_similarity": {"enabled": False},
        "entity_overlap": {"enabled": False},
        "internal_links": internal,
    }}}


def test_internal_link_edges_are_directed(conn):
    _add_page(conn, "A0001", "/a")
    _add_page(conn, "B0002", "/b")
    db.replace_page_links(conn, "A0001", [f"{BASE}/b"])

    counts = graph.rebuild_graph(conn, _link_cfg())

    assert counts["internal_link"] == 1
    edges = db.get_all_edges(conn)
    assert len(edges) == 1
    e = edges[0]
    assert (e["source_page_id"], e["target_page_id"], e["edge_type"]) == \
        ("A0001", "B0002", "internal_link")
    assert e["weight"] == pytest.approx(0.6)
    # No reverse edge unless B links back.
    assert db.get_edges_for_page(conn, "B0002") == []


def test_internal_link_resolution_tolerates_url_variants(conn):
    _add_page(conn, "A0001", "/a")
    _add_page(conn, "B0002", "/b")
    # Trailing slash + fragment + case should still resolve to B0002.
    db.replace_page_links(conn, "A0001", [
        "HTTPS://IPFirstResponse.IPAustralia.gov.au/b/#see-also",
    ])
    graph.rebuild_graph(conn, _link_cfg())
    edges = db.get_all_edges(conn)
    assert [(e["source_page_id"], e["target_page_id"]) for e in edges] == [("A0001", "B0002")]


def test_internal_link_skips_unknown_stub_and_self_targets(conn):
    _add_page(conn, "A0001", "/a")
    _add_page(conn, "B0002", "/b")
    _add_page(conn, "S0003", "/s", status="stub")
    db.replace_page_links(conn, "A0001", [
        f"{BASE}/a",          # self-link — skipped
        f"{BASE}/s",          # stub target — skipped
        f"{BASE}/missing",    # not in corpus — skipped
        f"{BASE}/b",          # valid
    ])
    graph.rebuild_graph(conn, _link_cfg())
    edges = db.get_all_edges(conn)
    assert [(e["source_page_id"], e["target_page_id"]) for e in edges] == [("A0001", "B0002")]


def test_internal_link_edges_rebuild_is_idempotent(conn):
    _add_page(conn, "A0001", "/a")
    _add_page(conn, "B0002", "/b")
    db.replace_page_links(conn, "A0001", [f"{BASE}/b"])
    graph.rebuild_graph(conn, _link_cfg())
    graph.rebuild_graph(conn, _link_cfg())
    assert len(db.get_all_edges(conn)) == 1


def test_internal_links_disabled_writes_no_edges(conn):
    _add_page(conn, "A0001", "/a")
    _add_page(conn, "B0002", "/b")
    db.replace_page_links(conn, "A0001", [f"{BASE}/b"])
    counts = graph.rebuild_graph(conn, _link_cfg(enabled=False))
    assert "internal_link" not in counts
    assert db.get_all_edges(conn) == []


def test_nav_link_frequency_filter_drops_ubiquitous_targets(conn):
    # 6 content pages all link to a shared "footer" page; each also has one
    # genuine content link. The footer (linked by 100% of pages) is dropped by
    # the >50% document-frequency filter; the content links survive.
    ids = [f"P{i:04d}" for i in range(6)]
    for i, pid in enumerate(ids):
        _add_page(conn, pid, f"/p{i}")
    _add_page(conn, "FOOT0", "/privacy")
    _add_page(conn, "CONT0", "/content")
    # Every page links to the footer (df = 6/8 = 75%); only two pages link to
    # the content page (df = 2/8 = 25%).
    for pid in ids:
        links = [f"{BASE}/privacy"]
        if pid in ("P0000", "P0001"):
            links.append(f"{BASE}/content")
        db.replace_page_links(conn, pid, links)

    counts = graph.rebuild_graph(conn, _link_cfg(nav_link_df_threshold=0.5, min_pages=5))

    targets = {e["target_page_id"] for e in db.get_all_edges(conn)}
    assert "FOOT0" not in targets          # nav/footer target filtered out
    assert "CONT0" in targets              # genuine content link kept
    # 2 edges to CONT0, 0 to FOOT0.
    assert counts["internal_link"] == 2


def test_nav_link_filter_disabled_below_min_pages(conn):
    # With fewer than min_pages, the filter stays off so bootstrap corpora keep
    # every edge even if a target is technically linked by everyone.
    _add_page(conn, "P0001", "/a")
    _add_page(conn, "P0002", "/b")
    _add_page(conn, "FOOT0", "/privacy")
    db.replace_page_links(conn, "P0001", [f"{BASE}/privacy"])
    db.replace_page_links(conn, "P0002", [f"{BASE}/privacy"])

    counts = graph.rebuild_graph(conn, _link_cfg(nav_link_df_threshold=0.5, min_pages=5))
    assert counts["internal_link"] == 2
    assert {e["target_page_id"] for e in db.get_all_edges(conn)} == {"FOOT0"}


def test_internal_link_coexists_with_max_weight_merge(conn):
    # An embedding edge and an internal link between the same ordered pair keep
    # the larger weight (db.upsert_graph_edge MAX semantics) but remain distinct
    # rows because edge_type is part of the key.
    _add_page(conn, "A0001", "/a")
    _add_page(conn, "B0002", "/b")
    db.upsert_graph_edge(conn, "A0001", "B0002", "internal_link", 0.6)
    db.upsert_graph_edge(conn, "A0001", "B0002", "embedding_similarity", 0.9)
    rows = db.get_edges_for_page(conn, "A0001")
    by_type = {r["edge_type"]: r["weight"] for r in rows}
    assert by_type["internal_link"] == pytest.approx(0.6)
    assert by_type["embedding_similarity"] == pytest.approx(0.9)
