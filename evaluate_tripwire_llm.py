import json
import tempfile
from pathlib import Path
import re

import tripwire

"""
Tripwire LLM Evaluation Script (Stage 3 + Stage 4)

Runs in GitHub Actions (not pytest) to avoid flaky CI.

Evaluates:
1) Similarity scoring (retrieval): did the expected page appear in threshold_passing_candidates?
2) LLM verification (verifier): given the candidate pages, did the LLM confirm impact on the expected page?
"""

TEST_SOURCE_NAME = "LLM_EVAL_TEST"
TEST_PRIORITY = "High"
TEST_VERSION = "eval_v1"

# Ground truth for this eval: the diff we generate is crafted from the 101-2 test markdown,
# so 101-2 should be verified as impacted.
EXPECTED_IMPACTED_UDIDS = {"101-2"}


def compute_metrics(predicted, verified, expected):
    predicted = set(predicted)
    verified = set(verified)
    expected = set(expected)

    retrieval_recall = len(predicted & expected) / len(expected) if expected else 0.0
    retrieval_precision = len(predicted & expected) / len(predicted) if predicted else 0.0

    verifier_recall = len(verified & expected) / len(expected) if expected else 0.0
    verifier_precision = len(verified & expected) / len(verified) if verified else 0.0

    return {
        "retrieval_precision": retrieval_precision,
        "retrieval_recall": retrieval_recall,
        "verifier_precision": verifier_precision,
        "verifier_recall": verifier_recall,
    }


def _extract_sentence_for_diff(markdown_text: str) -> str:
    # Find a stable sentence early in the 101-2 test markdown to use as diff content.
    m = re.search(r"(Design infringement can occur[^.]{20,400}\.)", markdown_text)
    if m:
        return m.group(1).strip()
    # fallback: first non-empty paragraph line
    for line in markdown_text.splitlines():
        line = line.strip()
        if line and not line.startswith("---") and not line.startswith("udid:"):
            return line[:240]
    return "Design infringement can occur when someone uses a design without permission."


def run_eval():
    # Ensure Stage 4 can find the test fixtures in the repo
    tripwire.IPFR_CONTENT_ARCHIVE_DIR = "ipfr_content_archive"

    # Prefer *_test.* fixtures
    prefer_test_files = True

    # Read the 101-2 markdown so the diff is guaranteed to be verifiable by exact text match.
    resolved = tripwire.resolve_ipfr_content_files("101-2", prefer_test_files=prefer_test_files)
    md_path = resolved.get("markdown_path")
    if not md_path:
        raise RuntimeError("Could not resolve 101-2 markdown fixture in ipfr_content_archive")

    md_text = Path(md_path).read_text(encoding="utf-8")
    removed = _extract_sentence_for_diff(md_text)
    added = removed.replace("registered design", "registered design")  # no-op if already present
    if "registered design" not in added:
        added = added.replace("a design", "a registered design", 1)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        diff_file = tmp / "design_change.diff"

        diff_file.write_text(
            "--- old\n"
            "+++ new\n"
            "@@ -1 +1 @@\n"
            f"-{removed}\n"
            f"+{added}\n",
            encoding="utf-8"
        )

        # ---- RUN SIMILARITY (Stage 3) ----
        analysis = tripwire.calculate_similarity(str(diff_file), source_priority=TEST_PRIORITY)

        candidates = analysis.get("threshold_passing_candidates", []) or []
        predicted_udids = [c.get("udid") for c in candidates if isinstance(c, dict)]

        print("\nSimilarity results")
        print("------------------")
        print("Candidate pages:", predicted_udids)
        print("Packets generated:", 1 if analysis.get("should_handover") else 0)

        # Force handover packet generation for eval (prototype)
        packets = tripwire.generate_handover_packets(
            source_name=TEST_SOURCE_NAME,
            priority=TEST_PRIORITY,
            version_id=TEST_VERSION,
            diff_file=str(diff_file),
            analysis=analysis,
            timestamp="eval"
        )

        # ---- RUN LLM VERIFICATION (Stage 4) ----
        verification_files = tripwire.run_llm_verification_for_packets(
            packets,
            prefer_test_files=prefer_test_files,
            top_n_candidates=None  # prototype: eventually pass all candidates
        )

        print("Verification files:", verification_files)

        verified_udids = []
        llm_decision = "uncertain"

        if verification_files:
            doc = json.loads(Path(verification_files[0]).read_text(encoding="utf-8"))
            llm = doc.get("llm_result", {}) or {}
            llm_decision = llm.get("overall_decision", "uncertain")
            verified_udids = [p.get("udid") for p in (llm.get("impacted_pages", []) or []) if isinstance(p, dict)]

        print("LLM decision:", llm_decision)
        print("Verified pages:", verified_udids)

        # ---- METRICS ----
        metrics = compute_metrics(predicted_udids, verified_udids, EXPECTED_IMPACTED_UDIDS)

        print("\nEvaluation metrics")
        print("------------------")
        for k, v in metrics.items():
            print(f"{k}: {v:.3f}")

        # ---- INTERPRETATION ----
        if EXPECTED_IMPACTED_UDIDS - set(predicted_udids):
            print("\nFailure type: RETRIEVAL MISS")
        elif EXPECTED_IMPACTED_UDIDS - set(verified_udids):
            print("\nFailure type: LLM VERIFICATION MISS")
        else:
            print("\nPipeline result: SUCCESS")


if __name__ == "__main__":
    run_eval()
