# Tripwire

Autonomous monitoring system for tracking substantive changes in authoritative Intellectual Property sources—such as Australian legislation and WIPO feeds—to detect updates that may impact IP First Response (IPFR) content.

## Mission

Tripwire's primary mission is to autonomously monitor authoritative IP sources to detect updates that may impact IPFR content. When changes are detected, Tripwire:

1. Archives the new content for version control
2. Generates diffs highlighting substantive changes
3. Performs semantic similarity analysis against existing IPFR content
4. Creates handover packets for changes exceeding relevance threshold
5. Logs all activity for comprehensive audit trails

These handover packets serve as the trigger for downstream impact assessment, where an LLM will determine whether IPFR content actually needs updating and suggest specific changes to IP First Response webpages.

## Key Features

**Watches for Changes**
- Monitors Australian legislation, WIPO feeds, and IP authority websites automatically
- Filters out minor updates like timestamps or formatting changes
- Captures only substantive content changes (new sections, policy updates, etc.)
- Keeps a complete history of what changed and when

**Finds Relevant Matches**
- Compares changes against your existing IPFR content to find what might be affected to substantially filter out noise
- Scores each change based on how closely it relates to the IPFR content
- Only flags changes that are likely to matter

**Creates Review Packets**
- Bundles everything needed to assess impact in one place
- Shows what changed, which IPFR content it matches, and why it was flagged
- Ready for the next step: having an AI determine if your content needs updating
- All packets are downloadable from GitHub Actions or logged in the audit trail

## How It Works

**Current workflow:**
1. Monitors sources every 6 hours
2. Detects substantive changes and creates diffs
3. Compares changes to IPFR content (732 chunk embeddings)
4. Scores matches (0-1 scale, threshold is 0.45)
5. Generates handover packets for high-scoring matches
6. Archives everything and uploads packets to GitHub

**Next step (in development):**
An AI will review handover packets containing high relevance to determine if IPFR content actually needs updating and suggest specific changes.

## File Structure

```
├── tripwire.py                   # Main script
├── test_stage3.py                # Test suite
├── sources.json                  # Source configuration
├── Semantic_Embeddings_Output.json  # IPFR embeddings (37 MB, required)
├── content_archive/              # Current content (baseline for diffs)
├── diff_archive/                 # Generated diffs
├── handover_packets/             # Generated packets (Git-ignored)
├── audit_log.csv                 # Complete audit trail
├── .github/workflows/
│   ├── tripwire.yml              # Main workflow (every 6 hours)
│   └── test-stage3.yml           # Test workflow
└── test_fixtures/                # Test data
```

## Setup

1. **Add embeddings file**: Place `Semantic_Embeddings_Output.json` (37 MB) in repo root

2. **Set API key**: Settings → Secrets → Actions → Add `OPENAI_API_KEY`

3. **Configure sources**: Edit `sources.json` to specify what to monitor

4. **Commit and push**: Workflow runs automatically every 6 hours or trigger manually

## Workflows

**Main workflow** (`tripwire.yml`) runs every 6 hours:
- Checks all configured sources for changes
- Generates handover packets for relevant matches
- Commits archives and audit log back to repo
- Uploads packets as downloadable artifacts

Run manually: Actions → Tripwire Check → Run workflow

**Test workflow** (`test-stage3.yml`) runs tests against a single real diff file. Triggered manually only.

Run manually: Actions → Test Stage 3 → Run workflow

## Viewing Results

**In GitHub Actions** (after each run):
- Job summary shows a table of all handover packets (priority, score, matched content)
- Download artifacts for full JSON files

**In the repository:**
- `audit_log.csv` - Complete log with monitoring, analysis, and decision columns
- `diff_archive/` - All generated diffs
- `content_archive/` - Current content baselines

## Handover Packets

Each packet is a JSON file containing everything needed to assess whether IPFR content needs updating:

- **What changed:** The actual diff showing new/modified content
- **Where it came from:** Source name, date, file location
- **How it scored:** Similarity score, matched legal terms, why it was flagged
- **What it matches:** Which IPFR content (UDID and Chunk ID) is most similar
- **Context:** The matched IPFR content text for comparison

Packets are available as downloadable artifacts in GitHub Actions and logged in `audit_log.csv`. In the next development phase, an AI will consume these packets to determine if IPFR updates are actually needed.

## Configuration

**Similarity threshold** (`tripwire.py` line ~39):
```python
SIMILARITY_THRESHOLD = 0.45  # 0.40 = catch more, 0.50 = catch less
```

**Power words** (`tripwire.py` lines ~342-356): Add/remove legal terms that boost scores

**Schedule** (`.github/workflows/tripwire.yml` line 4):
```yaml
- cron: '0 */6 * * *'  # Every 6 hours (adjust as needed)
```

## Running Tests

**Full test suite:**
```bash
export OPENAI_API_KEY="sk-..."
python generate_mock_data.py  # Generate test fixtures first
pytest test_stage3.py -v
```

**Test with a real diff:**
```bash
python tripwire.py --test-stage3 diff_archive/20260216_184111_IP_Australia_What_are_trade_marks_.diff
```

**Via GitHub Actions:**
Actions → Test Stage 3 → Run workflow

## Troubleshooting

**No handover packets generated?** 
Check the logs for "Should generate handover: False". If the score is below 0.45, this is expected — the change wasn't relevant enough. Consider lowering the threshold or checking if the source is appropriate.

**"Semantic embeddings file not found"?**
Ensure `Semantic_Embeddings_Output.json` (37 MB) is in the repository root.

**OpenAI API errors?**
Verify the API key is set in GitHub Secrets and is valid:
```bash
curl https://api.openai.com/v1/models -H "Authorization: Bearer $OPENAI_API_KEY"
```

**Git push fails with 403?**
Ensure `.github/workflows/tripwire.yml` has `permissions: contents: write`

## How Scoring Works

Each change gets a score from 0 to 1:
- **Base score**: How similar the change is to IPFR content (AI comparison)
- **Boost**: Legal terms like "infringement" or "penalty" add +0.15 each
- **Final score**: Base + boost (threshold is 0.45)

Example: A Trade Marks Act change scores 0.38 on similarity but contains "must" and "penalty" → 0.38 + 0.30 = 0.68 → handover packet generated.

Why additive (not multiplicative)? The sources monitored are already IP/legislation-focused, so generic legal terms are meaningful signals. Better to surface a marginal match with enforcement language than suppress it.

## What's Next

**Current:** Tripwire detects changes and identifies which IPFR content might be affected, creating handover packets with all the context needed for review.

**In development:** AI-powered impact assessment that will:
- Review handover packets automatically
- Determine if IPFR content genuinely needs updating
- Generate specific suggested edits for affected webpages
- Optionally create draft updates for review

## Contributing

**Adding sources:** Edit `sources.json`. Supported types: `Legislation_OData`, `WebPage`, `RSS`

**Modifying analysis:** Key functions in `tripwire.py`:
- `detect_power_words()` - Legal term detection
- `calculate_final_score()` - Scoring logic
- `calculate_similarity()` - Main analysis pipeline
- `generate_handover_packet()` - JSON output

Run tests after changes: `pytest test_stage3.py -v`
