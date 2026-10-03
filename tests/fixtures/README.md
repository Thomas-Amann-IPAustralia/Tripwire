# Test Fixtures

Manually curated snapshot files for Stage 2 / Stage 3 threshold testing.

## Webpage Fixtures

| File | Purpose |
|---|---|
| `webpage_base.txt` | Baseline snapshot of a Trade Marks Act summary page |
| `webpage_identical.txt` | Byte-for-byte identical to base — hash match, no change |
| `webpage_cosmetic_change.txt` | Normalises to identical text — cosmetic only |
| `webpage_numerical_change.txt` | Exam period 12→6 months, fee $250→$500: **high** significance |
| `webpage_modal_verb_change.txt` | "must" → "shall" and "may" → "shall": **high** significance |
| `webpage_date_change.txt` | New commencement date section added: **high** significance |
| `webpage_cross_reference_change.txt` | New Act cross-reference added: **high** significance |
| `webpage_new_section.txt` | Entire new section 4A inserted: **standard** or **high** significance |
| `webpage_deletion.txt` | Section 4 (Well-Known Trade Marks) deleted: meaningful change |
| `webpage_standard_change.txt` | Editorial rewording in section 1, no legal-significance markers |
| `webpage_high_significance_combined.txt` | Multiple significance signals: dates, numbers, modal verbs, cross-refs |
| `webpage_too_short.txt` | Simulates a CAPTCHA/error page (< 200 chars) |

## RSS Fixtures

| File | Purpose |
|---|---|
| `rss_snapshot_base.json` | Stored RSS state (2 items) — used as the "previous" snapshot |
| `rss_feed_new_item.xml` | Current feed with a third new item added |
| `rss_feed_mutated_item.xml` | Current feed with item-001 content mutated (fee correction) |
