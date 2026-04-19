"""
tests/test_ingestion_scrape_quality.py

Tests for the data-quality helpers added to ingestion/scrape_ipfr.py:
boilerplate detection + stripping, stub detection, section re-indexing, and
the heuristic section fallback.
"""

from __future__ import annotations

from ingestion.scrape_ipfr import (
    _heuristic_sections,
    _reindex_sections,
    detect_frequent_lines,
    is_stub_page,
    strip_boilerplate,
)


# ---------------------------------------------------------------------------
# Frequent-line / boilerplate detection
# ---------------------------------------------------------------------------


def test_detect_frequent_lines_flags_cross_document_repeats():
    docs = [
        "Skip to main content\nTitle A\nBody of A.",
        "Skip to main content\nTitle B\nBody of B.",
        "Skip to main content\nTitle C\nBody of C.",
        "Skip to main content\nTitle D\nBody of D.",
        "Skip to main content\nTitle E\nBody of E.",
    ]
    frequent = detect_frequent_lines(docs, frequency_threshold=0.7, min_documents=3)
    assert "Skip to main content" in frequent
    assert "Title A" not in frequent
    assert "Body of A." not in frequent


def test_detect_frequent_lines_respects_min_documents():
    docs = ["Skip to main content\nA.", "Skip to main content\nB."]
    frequent = detect_frequent_lines(docs, frequency_threshold=0.7, min_documents=3)
    assert frequent == set()


def test_detect_frequent_lines_ignores_empty_lines():
    docs = ["\n\nreal line 1\n\n"] * 5
    frequent = detect_frequent_lines(docs, frequency_threshold=0.7, min_documents=3)
    assert "" not in frequent
    assert "real line 1" in frequent


# ---------------------------------------------------------------------------
# Boilerplate stripping + section re-indexing
# ---------------------------------------------------------------------------


def test_strip_boilerplate_removes_frequent_and_blocklisted_lines():
    content = (
        "Skip to main content\n"
        "Menu\n"
        "What is copyright?\n"
        "Copyright protects creative works.\n"
        "Menu\n"
        "See also\n"
        "Related links."
    )
    sections = [
        {"heading_text": "What is copyright?", "heading_level": 2,
         "char_start": 22, "char_end": 40},
        {"heading_text": "See also", "heading_level": 2,
         "char_start": 81, "char_end": 89},
    ]
    frequent_lines = {"Menu"}
    blocklist = ["Skip to main content"]

    stripped, adjusted, bytes_stripped = strip_boilerplate(
        content, sections, blocklist=blocklist, frequent_lines=frequent_lines,
    )
    assert "Skip to main content" not in stripped
    assert "Menu" not in stripped.split("\n")
    assert "What is copyright?" in stripped
    assert bytes_stripped > 0

    # Sections must be re-anchored to the stripped text.
    offsets = {s["heading_text"]: s["char_start"] for s in adjusted}
    assert stripped[offsets["What is copyright?"]:].startswith("What is copyright?")
    assert stripped[offsets["See also"]:].startswith("See also")


def test_strip_boilerplate_drops_sections_whose_headings_vanished():
    content = "Chrome line\nReal heading\nContent body."
    sections = [
        {"heading_text": "Chrome line", "heading_level": 2,
         "char_start": 0, "char_end": 11},
        {"heading_text": "Real heading", "heading_level": 2,
         "char_start": 12, "char_end": 24},
    ]
    stripped, adjusted, _ = strip_boilerplate(
        content, sections, frequent_lines={"Chrome line"},
    )
    assert len(adjusted) == 1
    assert adjusted[0]["heading_text"] == "Real heading"


def test_strip_boilerplate_inline_blocklist_phrase_removed():
    content = "Header Skip to main content inside line\nBody."
    stripped, _, bytes_stripped = strip_boilerplate(
        content, [], blocklist=["Skip to main content"],
    )
    assert "Skip to main content" not in stripped
    assert "Header" in stripped
    assert "inside line" in stripped
    assert bytes_stripped > 0


def test_reindex_sections_handles_repeated_headings():
    content = "Section\nbody one\nSection\nbody two"
    sections = [
        {"heading_text": "Section", "heading_level": 2, "char_start": 0, "char_end": 7},
        {"heading_text": "Section", "heading_level": 2, "char_start": 17, "char_end": 24},
    ]
    adjusted = _reindex_sections(content, sections)
    assert len(adjusted) == 2
    assert adjusted[0]["char_start"] < adjusted[1]["char_start"]


# ---------------------------------------------------------------------------
# Stub detection
# ---------------------------------------------------------------------------


def test_is_stub_page_flags_short_content():
    assert is_stub_page("tiny", min_length=200)
    assert not is_stub_page("x" * 1000, min_length=200)


def test_is_stub_page_flags_stub_phrases():
    content = "x" * 5000 + " This page is coming soon and we will update it."
    assert is_stub_page(content, min_length=100,
                        stub_phrases=["This page is coming soon"])


def test_is_stub_page_respects_case_insensitive_match():
    content = "x" * 1000 + "\nCOMING APRIL 2026"
    assert is_stub_page(content, min_length=100, stub_phrases=["Coming April 2026"])


# ---------------------------------------------------------------------------
# Heuristic section extraction (fallback when trafilatura XML is absent)
# ---------------------------------------------------------------------------


def test_heuristic_sections_finds_question_headings():
    text = (
        "What is it?\n"
        "Intellectual property is a type of property.\n"
        "Who's involved?\n"
        "The rights holder and the infringer."
    )
    out = _heuristic_sections(text)
    headings = [s["heading_text"] for s in out]
    assert "What is it?" in headings
    assert "Who's involved?" in headings


def test_heuristic_sections_ignores_prose_lines():
    text = "A regular sentence that should not be a heading.\nAnother prose line."
    out = _heuristic_sections(text)
    assert out == []


def test_heuristic_sections_preserves_char_offsets():
    text = "What is it?\nBody content here."
    out = _heuristic_sections(text)
    assert out
    head = out[0]
    assert text[head["char_start"]:head["char_end"]] == "What is it?"
