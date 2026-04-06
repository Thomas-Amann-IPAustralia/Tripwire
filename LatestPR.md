## Summary

This PR introduces Phase 2 of the Tripwire influencer source pipeline, implementing change detection (Stage 2), diff generation (Stage 3), content validation, and comprehensive test coverage. These modules enable the system to determine whether scraped content has meaningfully changed and to produce precise diffs for downstream processing.

## Key Changes

### New Core Modules

- **`src/scraper.py`**: Web scraping with trafilatura-based HTML extraction and text normalisation. Provides `scrape_url()`, `extract_plain_text()`, `normalise_text()`, and `compute_sha256()` utilities. Handles CAPTCHA detection and raises appropriate error types for retry logic.

- **`src/validation.py`**: Content validation with four checks: minimum length (200 chars), CAPTCHA detection, optional structural markers, and dramatic size changes (outside 30%–300% of previous). Provides both hard validation (`validate_scraped_content()`) that raises `PermanentError` and soft validation (`validate_content()`) that returns warnings.

- **`src/stage2_change_detection.py`**: Three-pass change detection system:
  - **Pass 1**: SHA-256 hash comparison (stops if match).
  - **Pass 2**: Word-level unified diff (stops if empty after normalisation).
  - **Pass 3**: Significance fingerprinting—extracts defined terms, numerical values, dates, cross-references, and modal verbs from changed lines to tag as `high` or `standard` significance.
  - Applies only to webpage sources; FRL and RSS bypass this stage.

- **`src/stage3_diff.py`**: Diff generation with source-type routing:
  - **Webpage**: Unified diff of old vs new snapshot.
  - **FRL**: Retrieves change explainer from FRL OData API; falls back to webpage diff if unavailable.
  - **RSS**: Extracts new and mutated items from feed state.
  - Manages snapshot rotation (keeps up to 6 previous versions by default).
  - Normalises diffs (decodes HTML entities, collapses whitespace, applies NFC).

### Test Suite

- **`tests/test_change_detection.py`**: 822 lines of comprehensive pytest tests covering:
  - Text normalisation, SHA-256 hashing, HTML extraction.
  - Scraping with mocked sessions, HTTP error handling, CAPTCHA detection.
  - Content validation (length, CAPTCHA, structural markers, size changes).
  - Change detection (hash matching, cosmetic vs significant changes, fingerprinting).
  - Diff generation (webpage, FRL, RSS workflows).
  - Snapshot rotation and file management.

### Test Fixtures

- **`tests/fixtures/`**: Curated snapshot files for threshold testing:
  - `webpage_base.txt`, `webpage_identical.txt`, `webpage_cosmetic_change.txt`
  - `webpage_numerical_change.txt`, `webpage_modal_verb_change.txt`, `webpage_cross_reference_change.txt`
  - `webpage_high_significance_combined.txt`, `webpage_standard_change.txt`
  - `webpage_deletion.txt`, `webpage_too_short.txt`, `webpage_date_change.txt`, `webpage_new_section.txt`
  - RSS feed fixtures: `rss_feed_new_item.xml`, `rss_feed_mutated_item.xml`, `rss_snapshot_base.json`
  - `README.md` documenting fixture purposes.

### Documentation Updates

- **`LatestPR.md`**: Updated to reflect Phase 2 additions and overall pipeline architecture.

## Notable Implementation Details

- **Error handling**: Consistent use of `RetryableError` (transient failures) and `PermanentError` (permanent failures) across scraper and validation modules.
- **Normalisation**: Text normalisation is applied consistently across stages (NFC, whitespace collapse, HTML entity decoding) but preserves case and punctuation for downstream NER/YAKE processing.
- **Snapshot management**: Automatic rotation of old snapshots with configurable retention (default 6 versions); snapshots are committed to Git by the pipeline

https://claude.ai/code/session_01GXuZk7JzcjJhDqj7pqK44U
