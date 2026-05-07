"""
tests/test_change_detection.py

Tests for Phase 2 modules:
  - src/scraper.py
  - src/validation.py
  - src/stage2_change_detection.py
  - src/stage3_diff.py

All tests use pytest's tmp_path fixture and monkeypatch; no network calls.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures: paths to test data files
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# src/scraper.py tests
# ---------------------------------------------------------------------------


class TestNormaliseText:
    def test_collapses_whitespace(self):
        from src.scraper import normalise_text
        assert normalise_text("foo   bar") == "foo bar"

    def test_replaces_nbsp(self):
        from src.scraper import normalise_text
        assert normalise_text("foo\xa0bar") == "foo bar"

    def test_collapses_triple_newlines(self):
        from src.scraper import normalise_text
        result = normalise_text("para1\n\n\n\npara2")
        assert "\n\n\n" not in result
        assert "para1" in result
        assert "para2" in result

    def test_preserves_case(self):
        from src.scraper import normalise_text
        text = "Trade Marks Act 1995"
        assert normalise_text(text) == text

    def test_preserves_punctuation(self):
        from src.scraper import normalise_text
        text = "Section 44; must, shall, may."
        result = normalise_text(text)
        assert ";" in result
        assert "." in result

    def test_nfc_normalisation(self):
        from src.scraper import normalise_text
        import unicodedata
        # NFD form of 'é' (e + combining acute accent)
        nfd = "e\u0301"
        nfc = unicodedata.normalize("NFC", nfd)
        result = normalise_text(nfd)
        assert result == nfc

    def test_strips_leading_trailing_whitespace(self):
        from src.scraper import normalise_text
        assert normalise_text("  hello  ") == "hello"


class TestComputeSha256:
    def test_known_hash(self):
        from src.scraper import compute_sha256
        text = "hello"
        expected = hashlib.sha256("hello".encode()).hexdigest()
        assert compute_sha256(text) == expected

    def test_identical_texts_same_hash(self):
        from src.scraper import compute_sha256
        assert compute_sha256("foo") == compute_sha256("foo")

    def test_different_texts_different_hash(self):
        from src.scraper import compute_sha256
        assert compute_sha256("foo") != compute_sha256("bar")


class TestExtractPlainText:
    def test_strips_html_tags_fallback(self, monkeypatch):
        """When trafilatura is unavailable, should still extract text."""
        monkeypatch.setitem(__import__("sys").modules, "trafilatura", None)
        from importlib import reload
        import src.scraper as scraper_mod
        # Use the _strip_html_basic path by patching trafilatura import.
        html = "<html><body><p>Hello World</p></body></html>"
        # Call extract_plain_text directly; it will use the fallback.
        with patch("src.scraper.normalise_text", side_effect=lambda x: x):
            result = scraper_mod._strip_html_basic(html)
        assert "Hello World" in result

    def test_extract_plain_text_returns_string(self):
        from src.scraper import extract_plain_text
        html = "<html><body><p>Some content here for testing.</p></body></html>"
        result = extract_plain_text(html)
        assert isinstance(result, str)
        assert len(result) > 0


class TestScrapeUrl:
    def _make_session(self, text: str, status_code: int = 200):
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = status_code
        resp.text = text
        session.get.return_value = resp
        return session

    def test_successful_scrape(self):
        from src.scraper import scrape_url
        html = "<html><body><p>" + "A" * 300 + "</p></body></html>"
        session = self._make_session(html)
        with patch("src.scraper.extract_plain_text", return_value="A" * 300):
            result = scrape_url("https://example.com", session)
        assert isinstance(result, str)

    def test_http_404_raises_permanent_error(self):
        from src.scraper import scrape_url
        from src.errors import PermanentError
        session = self._make_session("Not Found", status_code=404)
        with pytest.raises(PermanentError):
            scrape_url("https://example.com/missing", session)

    def test_http_503_raises_retryable_error(self):
        from src.scraper import scrape_url
        from src.errors import RetryableError
        session = self._make_session("Service Unavailable", status_code=503)
        with pytest.raises(RetryableError):
            scrape_url("https://example.com", session)

    def test_captcha_content_raises_permanent_error(self):
        from src.scraper import scrape_url
        from src.errors import PermanentError
        session = self._make_session("Please verify you are human to continue.", status_code=200)
        with patch("src.scraper.extract_plain_text",
                   return_value="Please verify you are human to continue."):
            with pytest.raises(PermanentError):
                scrape_url("https://example.com", session)

    def test_connection_error_raises_retryable_error(self):
        from src.scraper import scrape_url
        from src.errors import RetryableError
        session = MagicMock()
        session.get.side_effect = ConnectionError("timed out")
        with pytest.raises(RetryableError):
            scrape_url("https://example.com", session)


# ---------------------------------------------------------------------------
# src/validation.py tests
# ---------------------------------------------------------------------------


class TestValidateContent:
    def test_valid_content_no_warnings(self):
        from src.validation import validate_content
        content = "A" * 300
        warnings = validate_content(content, "https://example.com")
        assert warnings == []

    def test_too_short_returns_warning(self):
        from src.validation import validate_content
        warnings = validate_content("short", "https://example.com")
        assert any("too short" in w.lower() for w in warnings)

    def test_captcha_phrase_returns_warning(self):
        from src.validation import validate_content
        content = "Please enable javascript to continue." + "x" * 300
        warnings = validate_content(content, "https://example.com")
        assert any("captcha" in w.lower() or "bot" in w.lower() for w in warnings)

    def test_dramatic_shrinkage_returns_warning(self):
        from src.validation import validate_content
        warnings = validate_content(
            "x" * 100, "https://example.com",
            previous_length=1000
        )
        assert any("shrink" in w.lower() for w in warnings)

    def test_dramatic_growth_returns_warning(self):
        from src.validation import validate_content
        warnings = validate_content(
            "x" * 5000, "https://example.com",
            previous_length=100
        )
        assert any("growth" in w.lower() for w in warnings)

    def test_moderate_size_change_no_warning(self):
        from src.validation import validate_content
        warnings = validate_content(
            "x" * 400, "https://example.com",
            previous_length=300
        )
        assert not any("growth" in w.lower() or "shrink" in w.lower() for w in warnings)


class TestValidateScrapedContent:
    def test_raises_on_too_short(self):
        from src.validation import validate_scraped_content
        from src.errors import PermanentError
        with pytest.raises(PermanentError):
            validate_scraped_content("short", "https://example.com")

    def test_raises_on_captcha(self):
        from src.validation import validate_scraped_content
        from src.errors import PermanentError
        content = "Captcha required. " + "x" * 300
        with pytest.raises(PermanentError):
            validate_scraped_content(content, "https://example.com")

    def test_raises_on_dramatic_size_change(self):
        from src.validation import validate_scraped_content
        from src.errors import PermanentError
        with pytest.raises(PermanentError):
            validate_scraped_content(
                "x" * 200, "https://example.com",
                previous_length=10000
            )

    def test_passes_valid_content(self):
        from src.validation import validate_scraped_content
        content = "The Trade Marks Act 1995 governs trade mark registration in Australia. " * 10
        warnings = validate_scraped_content(content, "https://example.com")
        assert warnings == []


# ---------------------------------------------------------------------------
# src/stage2_change_detection.py tests
# ---------------------------------------------------------------------------


class TestDetectChange:
    def _base_text(self) -> str:
        return _read_fixture("webpage_base.txt")

    def test_frl_source_skipped(self):
        from src.stage2_change_detection import detect_change
        result = detect_change(
            "src_frl", "frl",
            new_text="anything", previous_text="old", previous_hash="oldhash"
        )
        assert result.decision == "skipped"
        assert result.should_proceed

    def test_rss_source_skipped(self):
        from src.stage2_change_detection import detect_change
        result = detect_change(
            "src_rss", "rss",
            new_text="anything", previous_text="old", previous_hash="oldhash"
        )
        assert result.decision == "skipped"
        assert result.should_proceed

    def test_first_run_no_previous(self):
        from src.stage2_change_detection import detect_change
        result = detect_change(
            "src_web", "webpage",
            new_text=self._base_text(),
            previous_text=None,
            previous_hash=None,
        )
        assert result.decision == "significant"
        assert result.should_proceed

    def test_identical_content_hash_match(self):
        from src.stage2_change_detection import detect_change
        from src.scraper import compute_sha256
        text = self._base_text()
        result = detect_change(
            "src_web", "webpage",
            new_text=text,
            previous_text=text,
            previous_hash=compute_sha256(text),
        )
        assert result.decision == "no_change"
        assert result.hash_matched
        assert not result.should_proceed

    def test_identical_fixture_no_change(self):
        """webpage_identical.txt is byte-for-byte the same as base."""
        from src.stage2_change_detection import detect_change
        from src.scraper import compute_sha256
        base = _read_fixture("webpage_base.txt")
        identical = _read_fixture("webpage_identical.txt")
        result = detect_change(
            "src_web", "webpage",
            new_text=identical,
            previous_text=base,
            previous_hash=compute_sha256(base),
        )
        assert result.decision == "no_change"

    def test_numerical_change_high_significance(self):
        from src.stage2_change_detection import detect_change
        from src.scraper import compute_sha256
        base = _read_fixture("webpage_base.txt")
        changed = _read_fixture("webpage_numerical_change.txt")
        result = detect_change(
            "src_web", "webpage",
            new_text=changed,
            previous_text=base,
            previous_hash=compute_sha256(base),
            fingerprint_enabled=True,
        )
        assert result.decision == "significant"
        assert result.significance == "high"
        assert result.diff_size > 0

    def test_modal_verb_change_high_significance(self):
        from src.stage2_change_detection import detect_change
        from src.scraper import compute_sha256
        base = _read_fixture("webpage_base.txt")
        changed = _read_fixture("webpage_modal_verb_change.txt")
        result = detect_change(
            "src_web", "webpage",
            new_text=changed,
            previous_text=base,
            previous_hash=compute_sha256(base),
            fingerprint_enabled=True,
        )
        assert result.significance == "high"
        assert result.fingerprint.get("modal_verbs")

    def test_date_change_high_significance(self):
        from src.stage2_change_detection import detect_change
        from src.scraper import compute_sha256
        base = _read_fixture("webpage_base.txt")
        changed = _read_fixture("webpage_date_change.txt")
        result = detect_change(
            "src_web", "webpage",
            new_text=changed,
            previous_text=base,
            previous_hash=compute_sha256(base),
            fingerprint_enabled=True,
        )
        assert result.significance == "high"

    def test_cross_reference_change_high_significance(self):
        from src.stage2_change_detection import detect_change
        from src.scraper import compute_sha256
        base = _read_fixture("webpage_base.txt")
        changed = _read_fixture("webpage_cross_reference_change.txt")
        result = detect_change(
            "src_web", "webpage",
            new_text=changed,
            previous_text=base,
            previous_hash=compute_sha256(base),
            fingerprint_enabled=True,
        )
        assert result.significance == "high"

    def test_standard_change_no_fingerprint_match(self):
        from src.stage2_change_detection import detect_change
        from src.scraper import compute_sha256
        base = _read_fixture("webpage_base.txt")
        changed = _read_fixture("webpage_standard_change.txt")
        result = detect_change(
            "src_web", "webpage",
            new_text=changed,
            previous_text=base,
            previous_hash=compute_sha256(base),
            fingerprint_enabled=True,
        )
        assert result.decision == "significant"
        # Standard editorial change — significance may be standard.
        assert result.significance in ("high", "standard")

    def test_deletion_detected(self):
        from src.stage2_change_detection import detect_change
        from src.scraper import compute_sha256
        base = _read_fixture("webpage_base.txt")
        changed = _read_fixture("webpage_deletion.txt")
        result = detect_change(
            "src_web", "webpage",
            new_text=changed,
            previous_text=base,
            previous_hash=compute_sha256(base),
        )
        assert result.decision == "significant"
        assert result.diff_size > 0

    def test_fingerprint_disabled_returns_standard(self):
        from src.stage2_change_detection import detect_change
        from src.scraper import compute_sha256
        base = _read_fixture("webpage_base.txt")
        changed = _read_fixture("webpage_numerical_change.txt")
        result = detect_change(
            "src_web", "webpage",
            new_text=changed,
            previous_text=base,
            previous_hash=compute_sha256(base),
            fingerprint_enabled=False,
        )
        assert result.significance == "standard"
        assert result.fingerprint == {}

    def test_combined_high_significance(self):
        from src.stage2_change_detection import detect_change
        from src.scraper import compute_sha256
        base = _read_fixture("webpage_base.txt")
        changed = _read_fixture("webpage_high_significance_combined.txt")
        result = detect_change(
            "src_web", "webpage",
            new_text=changed,
            previous_text=base,
            previous_hash=compute_sha256(base),
            fingerprint_enabled=True,
        )
        assert result.significance == "high"
        # Should have multiple fingerprint categories populated.
        populated = [k for k, v in result.fingerprint.items() if v]
        assert len(populated) >= 2

    def test_to_dict_structure(self):
        from src.stage2_change_detection import detect_change
        from src.scraper import compute_sha256
        base = _read_fixture("webpage_base.txt")
        result = detect_change(
            "src_web", "webpage",
            new_text=base, previous_text=None, previous_hash=None
        )
        d = result.to_dict()
        assert "source_id" in d
        assert "decision" in d
        assert "significance" in d
        assert "diff_size" in d


class TestComputeDiff:
    def test_empty_diff_identical_texts(self):
        from src.stage2_change_detection import compute_diff
        text = "Line one\nLine two\n"
        lines = compute_diff(text, text)
        changed = [l for l in lines if (l.startswith("+") or l.startswith("-"))
                   and not l.startswith("+++") and not l.startswith("---")]
        assert changed == []

    def test_added_line_detected(self):
        from src.stage2_change_detection import compute_diff
        old = "Line one\nLine two\n"
        new = "Line one\nLine two\nLine three\n"
        lines = compute_diff(old, new)
        added = [l for l in lines if l.startswith("+") and not l.startswith("+++")]
        assert any("Line three" in l for l in added)

    def test_removed_line_detected(self):
        from src.stage2_change_detection import compute_diff
        old = "Line one\nLine two\nLine three\n"
        new = "Line one\nLine three\n"
        lines = compute_diff(old, new)
        removed = [l for l in lines if l.startswith("-") and not l.startswith("---")]
        assert any("Line two" in l for l in removed)


# ---------------------------------------------------------------------------
# src/stage3_diff.py tests
# ---------------------------------------------------------------------------


class TestRotateSnapshots:
    def test_rotates_current_to_v1(self, tmp_path):
        from src.stage3_diff import _rotate_snapshots
        snap_dir = tmp_path / "source1"
        snap_dir.mkdir()
        current = snap_dir / "source1.txt"
        current.write_text("version current", encoding="utf-8")
        _rotate_snapshots(snap_dir, "source1", 6)
        assert not current.exists()
        v1 = snap_dir / "source1.v1.txt"
        assert v1.exists()
        assert v1.read_text() == "version current"

    def test_shifts_existing_versions(self, tmp_path):
        from src.stage3_diff import _rotate_snapshots
        snap_dir = tmp_path / "src2"
        snap_dir.mkdir()
        (snap_dir / "src2.txt").write_text("new", encoding="utf-8")
        (snap_dir / "src2.v1.txt").write_text("v1_content", encoding="utf-8")
        (snap_dir / "src2.v2.txt").write_text("v2_content", encoding="utf-8")
        _rotate_snapshots(snap_dir, "src2", 6)
        assert (snap_dir / "src2.v1.txt").read_text() == "new"
        assert (snap_dir / "src2.v2.txt").read_text() == "v1_content"
        assert (snap_dir / "src2.v3.txt").read_text() == "v2_content"

    def test_prunes_beyond_retention_limit(self, tmp_path):
        from src.stage3_diff import _rotate_snapshots
        snap_dir = tmp_path / "src3"
        snap_dir.mkdir()
        (snap_dir / "src3.txt").write_text("new", encoding="utf-8")
        # Create 6 existing versions — v6 should be pruned after rotation.
        for i in range(1, 7):
            (snap_dir / f"src3.v{i}.txt").write_text(f"v{i}", encoding="utf-8")
        _rotate_snapshots(snap_dir, "src3", 6)
        # v6 should not exist after rotation (pruned).
        assert not (snap_dir / "src3.v7.txt").exists()

    def test_no_rotation_if_no_current(self, tmp_path):
        from src.stage3_diff import _rotate_snapshots
        snap_dir = tmp_path / "src4"
        snap_dir.mkdir()
        # No current snapshot — should not raise.
        _rotate_snapshots(snap_dir, "src4", 6)


class TestLoadPreviousSnapshot:
    def test_returns_none_when_absent(self, tmp_path):
        from src.stage3_diff import load_previous_snapshot
        result = load_previous_snapshot("unknown_src", snapshot_dir=tmp_path)
        assert result is None

    def test_returns_content_when_present(self, tmp_path):
        from src.stage3_diff import load_previous_snapshot
        snap_dir = tmp_path / "src5"
        snap_dir.mkdir()
        (snap_dir / "src5.txt").write_text("hello snapshot", encoding="utf-8")
        result = load_previous_snapshot("src5", snapshot_dir=tmp_path)
        assert result == "hello snapshot"


class TestLoadPreviousHash:
    def test_returns_none_when_absent(self, tmp_path):
        from src.stage3_diff import load_previous_hash
        assert load_previous_hash("missing", snapshot_dir=tmp_path) is None

    def test_returns_sha256_of_content(self, tmp_path):
        from src.stage3_diff import load_previous_hash
        from src.scraper import compute_sha256
        snap_dir = tmp_path / "src6"
        snap_dir.mkdir()
        content = "test content for hashing"
        (snap_dir / "src6.txt").write_text(content, encoding="utf-8")
        result = load_previous_hash("src6", snapshot_dir=tmp_path)
        assert result == compute_sha256(content)


class TestGenerateWebpageDiff:
    def test_first_run_creates_snapshot(self, tmp_path):
        from src.stage3_diff import generate_diff
        source = {"source_id": "s1", "source_type": "webpage", "url": "https://example.com"}
        new_text = _read_fixture("webpage_base.txt")
        result = generate_diff(
            source=source,
            new_text=new_text,
            previous_text=None,
            diff_lines=[],
            snapshot_dir=tmp_path,
        )
        assert result.diff_type == "first_run"
        snap = tmp_path / "s1" / "s1.txt"
        assert snap.exists()
        assert snap.read_text(encoding="utf-8") == new_text

    def test_second_run_rotates_and_writes_diff(self, tmp_path):
        from src.stage3_diff import generate_diff, _rotate_snapshots
        from src.stage2_change_detection import compute_diff

        source = {"source_id": "s2", "source_type": "webpage", "url": "https://example.com"}
        base = _read_fixture("webpage_base.txt")
        changed = _read_fixture("webpage_numerical_change.txt")

        # Create initial snapshot.
        snap_dir = tmp_path / "s2"
        snap_dir.mkdir()
        (snap_dir / "s2.txt").write_text(base, encoding="utf-8")

        diff_lines = compute_diff(base, changed)
        result = generate_diff(
            source=source,
            new_text=changed,
            previous_text=base,
            diff_lines=diff_lines,
            snapshot_dir=tmp_path,
        )
        assert result.diff_type == "unified_diff"
        assert result.diff_size_chars > 0
        # New snapshot should contain the updated text.
        assert (snap_dir / "s2.txt").read_text(encoding="utf-8") == changed
        # Previous should have been rotated to v1.
        assert (snap_dir / "s2.v1.txt").exists()

    def test_diff_file_written(self, tmp_path):
        from src.stage3_diff import generate_diff
        from src.stage2_change_detection import compute_diff

        source = {"source_id": "s3", "source_type": "webpage", "url": "https://example.com"}
        base = _read_fixture("webpage_base.txt")
        changed = _read_fixture("webpage_date_change.txt")
        snap_dir = tmp_path / "s3"
        snap_dir.mkdir()
        (snap_dir / "s3.txt").write_text(base, encoding="utf-8")

        diff_lines = compute_diff(base, changed)
        result = generate_diff(
            source=source,
            new_text=changed,
            previous_text=base,
            diff_lines=diff_lines,
            snapshot_dir=tmp_path,
        )
        assert result.diff_path is not None
        assert Path(result.diff_path).exists()

    def test_normalised_diff_is_string(self, tmp_path):
        from src.stage3_diff import generate_diff
        source = {"source_id": "s4", "source_type": "webpage", "url": "https://example.com"}
        result = generate_diff(
            source=source,
            new_text=_read_fixture("webpage_base.txt"),
            previous_text=None,
            diff_lines=[],
            snapshot_dir=tmp_path,
        )
        assert isinstance(result.normalised_diff, str)


class TestGenerateRssDiff:
    def _make_session(self, xml_text: str):
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = xml_text
        resp.raise_for_status = MagicMock()
        session.get.return_value = resp
        return session

    def test_new_item_detected(self, tmp_path):
        from src.stage3_diff import generate_diff

        # Write base snapshot.
        source_id = "rss_src"
        snap_dir = tmp_path / source_id
        snap_dir.mkdir()
        base_snapshot = json.loads(_read_fixture("rss_snapshot_base.json"))
        url = "https://www.ipaustralia.gov.au/rss"
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:12]
        (snap_dir / f"rss_{url_hash}.json").write_text(
            json.dumps(base_snapshot), encoding="utf-8"
        )

        xml = _read_fixture("rss_feed_new_item.xml")
        source = {"source_id": source_id, "source_type": "rss", "url": url}
        session = self._make_session(xml)

        result = generate_diff(
            source=source,
            new_text="",
            previous_text=None,
            diff_lines=[],
            snapshot_dir=tmp_path,
            session=session,
        )
        assert result.diff_type == "rss_items"
        assert len(result.rss_new_items) == 1
        assert result.rss_new_items[0]["title"] == "Designs System Modernisation"
        assert result.normalised_diff != ""

    def test_mutated_item_detected(self, tmp_path):
        from src.stage3_diff import generate_diff

        source_id = "rss_src2"
        snap_dir = tmp_path / source_id
        snap_dir.mkdir()
        base_snapshot = json.loads(_read_fixture("rss_snapshot_base.json"))
        url = "https://www.ipaustralia.gov.au/rss2"
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:12]
        (snap_dir / f"rss_{url_hash}.json").write_text(
            json.dumps(base_snapshot), encoding="utf-8"
        )

        xml = _read_fixture("rss_feed_mutated_item.xml")
        source = {"source_id": source_id, "source_type": "rss", "url": url}
        session = self._make_session(xml)

        result = generate_diff(
            source=source,
            new_text="",
            previous_text=None,
            diff_lines=[],
            snapshot_dir=tmp_path,
            session=session,
        )
        assert len(result.rss_mutated_items) == 1
        assert "item-001" in result.rss_mutated_items[0]["guid"]

    def test_no_changes_empty_diff(self, tmp_path):
        from src.stage3_diff import generate_diff

        source_id = "rss_unchanged"
        snap_dir = tmp_path / source_id
        snap_dir.mkdir()
        base_snapshot = json.loads(_read_fixture("rss_snapshot_base.json"))
        url = "https://www.ipaustralia.gov.au/rss3"
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:12]
        (snap_dir / f"rss_{url_hash}.json").write_text(
            json.dumps(base_snapshot), encoding="utf-8"
        )

        # Feed with same items as base snapshot — no changes.
        xml = _read_fixture("rss_feed_new_item.xml")
        # Override snapshot to include all three items from the new feed.
        full_snapshot = {
            "https://www.ipaustralia.gov.au/news/item-001": base_snapshot["https://www.ipaustralia.gov.au/news/item-001"],
            "https://www.ipaustralia.gov.au/news/item-002": base_snapshot["https://www.ipaustralia.gov.au/news/item-002"],
            "https://www.ipaustralia.gov.au/news/item-003": {
                "title": "Designs System Modernisation",
                "description": "IP Australia announces a modernised Designs examination system commencing 1 March 2026.",
                "pubDate": "Wed, 10 Sep 2025 11:00:00 +1000",
                "link": "https://www.ipaustralia.gov.au/news/item-003",
                "content_encoded": "IP Australia announces a modernised Designs examination system commencing 1 March 2026. The new system will reduce examination timelines from 12 months to 6 months.",
            },
        }
        (snap_dir / f"rss_{url_hash}.json").write_text(
            json.dumps(full_snapshot), encoding="utf-8"
        )

        source = {"source_id": source_id, "source_type": "rss", "url": url}
        session = self._make_session(xml)
        result = generate_diff(
            source=source,
            new_text="",
            previous_text=None,
            diff_lines=[],
            snapshot_dir=tmp_path,
            session=session,
        )
        assert result.rss_new_items == []
        assert result.rss_mutated_items == []

    def test_first_run_no_snapshot(self, tmp_path):
        from src.stage3_diff import generate_diff

        source_id = "rss_first"
        url = "https://www.ipaustralia.gov.au/rss_first"
        source = {"source_id": source_id, "source_type": "rss", "url": url}
        xml = _read_fixture("rss_feed_new_item.xml")
        session = self._make_session(xml)
        result = generate_diff(
            source=source,
            new_text="",
            previous_text=None,
            diff_lines=[],
            snapshot_dir=tmp_path,
            session=session,
        )
        # On first run all items are "new".
        assert len(result.rss_new_items) == 3


class TestDiffResultToDict:
    def test_to_dict_contains_expected_keys(self, tmp_path):
        from src.stage3_diff import generate_diff
        source = {"source_id": "td1", "source_type": "webpage", "url": "https://example.com"}
        result = generate_diff(
            source=source,
            new_text=_read_fixture("webpage_base.txt"),
            previous_text=None,
            diff_lines=[],
            snapshot_dir=tmp_path,
        )
        d = result.to_dict()
        for key in ("source_id", "source_type", "diff_type", "diff_size_chars",
                    "normalised_size_chars", "warnings"):
            assert key in d


class TestNormaliseDiffText:
    def test_html_entities_decoded(self):
        from src.stage3_diff import _normalise_diff_text
        result = _normalise_diff_text("&amp; &lt;foo&gt;")
        assert "&amp;" not in result
        assert "& <foo>" in result

    def test_whitespace_collapsed(self):
        from src.stage3_diff import _normalise_diff_text
        result = _normalise_diff_text("foo   bar")
        assert "foo bar" in result
