"""
tests/test_ingestion_enrich.py

Tests for the chunking + entity-canonicalisation improvements in
ingestion/enrich.py.  These tests intentionally exercise only the pure-Python
helpers (chunking, alias map, canonicalise_entity) — the embedding and spaCy
paths are lazy-loaded and require model artefacts we don't want to pull into
the test environment.
"""

from __future__ import annotations

from ingestion.enrich import (
    _build_alias_map,
    _coalesce_tail_chunks,
    _strip_leading_heading,
    canonicalise_entity,
    chunk_content,
)


# ---------------------------------------------------------------------------
# Chunking — tail coalescing
# ---------------------------------------------------------------------------


def test_coalesce_merges_short_tail_into_predecessor():
    chunks = [
        {"text": "first chunk body text", "chunk_index": 0, "section_heading": "S1"},
        {"text": "tail", "chunk_index": 1, "section_heading": "S1"},
    ]
    merged = _coalesce_tail_chunks(chunks, min_size=20, max_size=200)
    assert len(merged) == 1
    assert merged[0]["text"].endswith("tail")
    assert merged[0]["chunk_index"] == 0


def test_coalesce_respects_section_boundary():
    # Different section_heading → must NOT merge.
    chunks = [
        {"text": "section one body" * 5, "chunk_index": 0, "section_heading": "S1"},
        {"text": "tiny", "chunk_index": 1, "section_heading": "S2"},
    ]
    merged = _coalesce_tail_chunks(chunks, min_size=100, max_size=200)
    assert len(merged) == 2
    assert merged[1]["section_heading"] == "S2"


def test_coalesce_respects_ceiling():
    # Predecessor is already at max_size; must NOT merge if combined exceeds ceiling.
    chunks = [
        {"text": "A" * 200, "chunk_index": 0, "section_heading": "S1"},
        {"text": "B" * 5, "chunk_index": 1, "section_heading": "S1"},
    ]
    merged = _coalesce_tail_chunks(chunks, min_size=100, max_size=100)
    # Ceiling = 150 → 200 + 1 + 5 = 206 > 150, so must NOT merge.
    assert len(merged) == 2


def test_coalesce_renumbers_chunk_indices():
    chunks = [
        {"text": "one", "chunk_index": 0, "section_heading": "A"},
        {"text": "x", "chunk_index": 1, "section_heading": "A"},
        {"text": "two long body" * 3, "chunk_index": 2, "section_heading": "B"},
    ]
    merged = _coalesce_tail_chunks(chunks, min_size=5, max_size=200)
    assert [c["chunk_index"] for c in merged] == list(range(len(merged)))


# ---------------------------------------------------------------------------
# Chunking — leading-heading dedup
# ---------------------------------------------------------------------------


def test_strip_leading_heading_removes_duplicate_from_chunk_body():
    chunks = [
        {
            "text": "What is copyright infringement?\nCopyright infringement occurs when...",
            "chunk_index": 0,
            "section_heading": "What is copyright infringement?",
        }
    ]
    out = _strip_leading_heading(chunks)
    assert out[0]["text"].startswith("Copyright infringement occurs")
    assert "infringement?" not in out[0]["text"].split("\n", 1)[0]


def test_strip_leading_heading_noop_when_heading_absent_from_body():
    chunks = [
        {
            "text": "Some other first line\nRest of body.",
            "chunk_index": 0,
            "section_heading": "A Heading",
        }
    ]
    before = chunks[0]["text"]
    out = _strip_leading_heading(chunks)
    assert out[0]["text"] == before


def test_strip_leading_heading_preserves_body_if_remainder_empty():
    # Never produce an empty chunk just to remove a heading.
    chunks = [
        {
            "text": "Only Heading",
            "chunk_index": 0,
            "section_heading": "Only Heading",
        }
    ]
    out = _strip_leading_heading(chunks)
    assert out[0]["text"] == "Only Heading"


# ---------------------------------------------------------------------------
# Chunk content (integration of size + tail coalesce + heading dedup)
# ---------------------------------------------------------------------------


def test_chunk_content_uses_configured_size_and_min():
    text = "A" * 3000
    chunks = chunk_content(
        text, sections=[], chunk_size=1000, overlap=100,
        boundary_lookback=50, chunk_min_size=100,
    )
    assert all(len(c["text"]) <= 1500 for c in chunks)  # within 1.5x ceiling
    assert all(len(c["text"]) >= 100 or i == len(chunks) - 1 for i, c in enumerate(chunks))


def test_chunk_content_fallback_for_sectionless_text():
    text = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
    chunks = chunk_content(text, sections=[], chunk_size=1400, overlap=200)
    assert len(chunks) == 1
    assert chunks[0]["section_heading"] is None


# ---------------------------------------------------------------------------
# Entity canonicalisation
# ---------------------------------------------------------------------------


def test_canonicalise_entity_strips_trailing_possessive():
    assert canonicalise_entity("IP Australia's") == "IP Australia"
    assert canonicalise_entity("IP Australia\u2019s") == "IP Australia"
    assert canonicalise_entity("Claude's") == "Claude"


def test_canonicalise_entity_collapses_whitespace():
    assert canonicalise_entity("  IP   Australia   ") == "IP Australia"


def test_canonicalise_entity_applies_alias_map():
    alias_map = {
        "ipta": "Institute of Patent and Trade Mark Attorneys Australia",
        "ip australia first response": "IP First Response",
    }
    assert canonicalise_entity("IPTA", alias_map) == (
        "Institute of Patent and Trade Mark Attorneys Australia"
    )
    assert canonicalise_entity("IP Australia First Response", alias_map) == "IP First Response"


def test_canonicalise_entity_alias_lookup_is_case_insensitive():
    alias_map = {"acme corp": "ACME Corporation"}
    assert canonicalise_entity("ACME CORP", alias_map) == "ACME Corporation"


def test_build_alias_map_accepts_dict_form():
    out = _build_alias_map({"IPTA": "Institute of Patent..."})
    assert out == {"ipta": "Institute of Patent..."}


def test_build_alias_map_accepts_list_form():
    out = _build_alias_map([
        {"canonical": "IP First Response", "aliases": ["IPFR", "IP Australia First Response"]},
    ])
    assert out["ipfr"] == "IP First Response"
    assert out["ip australia first response"] == "IP First Response"
    # Canonical itself is also registered for self-mapping.
    assert out["ip first response"] == "IP First Response"


def test_build_alias_map_empty_when_none():
    assert _build_alias_map(None) == {}
    assert _build_alias_map([]) == {}
