"""Tests for src/email_html.py — Outlook-safe email rendering helpers."""

from src import email_html as eh


FLAT_DIFF = (
    "--- previous+++ current@@ -20,6 +20,16 @@ Join our growing list "
    "+New Seller Success +Get support at no cost -Old line removed "
    "Context with a dash - inside and A+ Content @@ -80,6 +90,12 @@ More context"
)


def test_flattened_diff_is_split_into_lines():
    lines = eh._diff_lines(FLAT_DIFF)
    assert lines[0] == ("hunk", "Lines 20–35")
    assert ("ctx", "Join our growing list") in lines
    assert ("add", "New Seller Success") in lines
    assert ("add", "Get support at no cost") in lines
    # Prose " - " and "A+" are not treated as diff markers.
    assert ("del", "Old line removed Context with a dash - inside and A+ Content") in lines
    assert ("hunk", "Lines 90–101") in lines
    assert lines[-1] == ("ctx", "More context")


def test_multiline_diff_is_parsed_line_by_line():
    diff = "--- a\n+++ b\n@@ -1,2 +1,2 @@\n same\n-old\n+new"
    assert eh._diff_lines(diff) == [
        ("hunk", "Lines 1–2"), ("ctx", "same"), ("del", "old"), ("add", "new"),
    ]


def test_non_diff_text_renders_as_plain_box():
    out = eh.diff_block("Explanatory memorandum <summary> text")
    assert "&lt;summary&gt;" in out
    assert "&minus;" not in out


def test_diff_block_escapes_content():
    out = eh.diff_block("@@ -1 +1 @@ +<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_document_wraps_body_and_escapes_header():
    out = eh.document(title="T & U", preheader="pre", eyebrow="E", heading="H <1>",
                      subheading="S", body="<p>BODY</p>", footer="F")
    assert out.startswith("<!DOCTYPE html>")
    assert "<p>BODY</p>" in out
    assert "H &lt;1&gt;" in out
    assert "<title>T &amp; U</title>" in out


def test_stat_tiles_pads_last_row():
    out = eh.stat_tiles([("1", "a", None), ("2", "b", "red")], per_row=3)
    assert out.count('class="tw-stat"') == 3
