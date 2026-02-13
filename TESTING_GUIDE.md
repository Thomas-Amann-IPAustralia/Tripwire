# Stage 3 Phase 2: Testing Guide

This guide explains how to test the Stage 3 relevance gate implementation.

## What's Implemented

**Phase 2 includes:**
- ✓ Diff parsing (extract additions/removals)
- ✓ Power word detection (must, shall, $, days, etc.)
- ✓ Semantic embedding generation
- ✓ Similarity calculation with mock data
- ✓ Weighted scoring (75% semantic, 25% power words)
- ✓ Threshold logic and filtering

**Phase 3 still TODO:**
- Load Tom's real spreadsheet (`260120_SQLiteStructure.xlsx`)
- Source relationship boosting from `influences` sheet
- Generate JSON handover packets
- Integration with Stage 2 pipeline

---

## Running Tests

### Step 1: Generate Mock Data

The tests need pre-generated embeddings for mock IPFR content.

```bash
# Install dependencies if not already done
pip install -r requirements.txt

# Generate mock semantic data (takes ~1 minute first time)
python generate_mock_data.py
```

This creates `test_fixtures/mock_semantic_data.pkl` with embeddings for 4 mock website chunks.

### Step 2: Run All Tests

```bash
# Run full test suite
pytest test_stage3.py -v

# Run specific test class
pytest test_stage3.py::TestPowerWordDetection -v

# Run single test
pytest test_stage3.py::TestEndToEnd::test_high_relevance_trademark_match -v
```

### Step 3: Manual Testing with Real Diffs

Test against actual diff files from your Stage 2 runs:

```bash
# Test with a real diff from diff_archive/
python tripwire.py --test-stage3 diff_archive/20260208_064622_ABC_News_World.diff
```

---

## Test Coverage

### Test Fixtures Created

1. **high_relevance_trademark.diff**
   - Content: Trademark infringement penalties
   - Power words: $150,000, must, 30 days
   - Expected: Should match IPFR-001, pass threshold

2. **power_words_heavy.diff**
   - Content: Patent filing requirements
   - Power words: must, shall, penalty, mandatory, Archives Act 1983
   - Expected: Power words boost marginal match

3. **noise_only.diff**
   - Content: Just timestamp change
   - Power words: None
   - Expected: Filtered, below threshold

4. **unrelated_content.diff**
   - Content: Sports and weather news
   - Power words: None
   - Expected: Low similarity, filtered

### Mock Semantic Data

Four mock IPFR website chunks:

- **IPFR-001**: Trademark infringement info
- **IPFR-002**: Patent application deadlines
- **IPFR-003**: Design registration
- **IPFR-099**: Unrelated weather content (control)

---

## Expected Test Results

All tests should pass with output like:

```
test_stage3.py::TestDiffParsing::test_extract_additions_and_removals PASSED
test_stage3.py::TestPowerWordDetection::test_detect_multiple_power_words PASSED
test_stage3.py::TestScoringLogic::test_calculate_final_score_weighting PASSED
test_stage3.py::TestEndToEnd::test_high_relevance_trademark_match PASSED
...
======================== X passed in Y.XXs ========================
```

---

## Understanding the Scoring

### Example 1: High Semantic Match

```
Diff: "Trademark infringement penalties up to $150,000"
Matches: IPFR-001 (trademark content)

Base similarity: 0.82
Power words: 2 found ($ amount, penalty)
Power score: 0.30

Final score: (0.82 * 0.75) + (0.30 * 0.25) = 0.615 + 0.075 = 0.69
Threshold: 0.70
Result: ✗ FILTERED (just below threshold)

BUT if power words boost it:
With 3 power words: 0.45 power score
Final: (0.82 * 0.75) + (0.45 * 0.25) = 0.615 + 0.1125 = 0.73
Result: ✓ HANDOVER GENERATED
```

### Example 2: Power Word Rescue

```
Diff: "Must submit within 30 days, shall face $5000 penalty"
Matches: IPFR-002 (patent deadlines)

Base similarity: 0.65 (moderate match)
Power words: 5 found (must, shall, 30 days, penalty, $)
Power score: 0.75 (5 * 0.15)

Final score: (0.65 * 0.75) + (0.75 * 0.25) = 0.4875 + 0.1875 = 0.675
Result: ✗ Still below 0.70, but close

With 7 power words: 1.0 power score (capped)
Final: (0.65 * 0.75) + (1.0 * 0.25) = 0.4875 + 0.25 = 0.7375
Result: ✓ HANDOVER GENERATED
```

---

## Troubleshooting

### "Mock data not found"
```bash
python generate_mock_data.py
```

### "Model failed to load"
The model `intfloat/multilingual-e5-small` downloads automatically (~80MB) on first run. If GitHub Actions times out, run locally first to cache it.

### "Similarity scores too low"
This is expected with mock data. Real data from Tom's spreadsheet will have better matches. The tests verify the **logic** works, not that scores are perfect.

### Tests fail on GitHub Actions
Make sure `generate_mock_data.py` runs in the workflow before `pytest`:

```yaml
- name: Generate Mock Data
  run: python generate_mock_data.py

- name: Run Tests
  run: pytest test_stage3.py -v
```

---

## Next: Phase 3

Once all tests pass locally and on GitHub Actions:

1. Get Tom's `260120_SQLiteStructure.xlsx` file
2. Verify it has `semantic` sheet with `Chunk_Embedding` column
3. Verify it has `influences` sheet with source relationships
4. Implement real spreadsheet loading in `calculate_similarity()`
5. Implement handover packet JSON generation
6. Add new tests for Phase 3 features

---

## Current Test Status

Run tests and check here:

```bash
pytest test_stage3.py -v --tb=short
```

- [ ] All tests passing locally?
- [ ] All tests passing on GitHub Actions?
- [ ] Ready to proceed to Phase 3?
