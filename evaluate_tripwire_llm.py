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
2) LLM verification (verifier): given the Top-N candidate pages, did the LLM confirm impact on the expected page?
3) Chunk confirmation (Pass 2): did the LLM confirm Stage 3 suggested chunks?
"""

TEST_SOURCE_NAME = "LLM_EVAL_TEST"
TEST_PRIORITY = "High"
TEST_VERSION = "eval_v1"

# Ground truth for this eval: the diff we generate is crafted from the 101-2 test markdown,
# so 101-2 should be verified as impacted.
EXPECTED_IMPACTED_UDIDS = {"101-2"}


def compute_metrics(predicted, verified, expected):
    predicted = set(predicted or [])
    verified = set(verified or [])
    expected = set(expected or [])

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


def compute_chunk_metrics(stage3_suggested_chunk_ids, llm_confirmed_chunk_ids):
    stage3 = set(stage3_suggested_chunk_ids or [])
    confirmed = set(llm_confirmed_chunk_ids or [])
    overlap = stage3 & confirmed

    precision = len(overlap) / len(stage3) if stage3 else 0.0
    recall = len(overlap) / len(confirmed) if confirmed else 0.0

    return {
        "chunk_precision": precision,
        "chunk_recall": recall,
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
    tripwire.IPFR_CONTENT_ARCHIVE_DIR = "IPFR_content_archive"

    # Prefer *_test.* fixtures
    prefer_test_files = True

    # Read the 101-2 markdown so the diff is guaranteed to be verifiable by exact text match.
    resolved = tripwire.resolve_ipfr_content_files("101-2", prefer_test_files=prefer_test_files)
    md_path = resolved.get("markdown_path")
    if not md_path:
        raise RuntimeError("Could not resolve 101-2 markdown fixture in IPFR_content_archive")

    md_text = Path(md_path).read_text(encoding="utf-8")
    removed = _extract_sentence_for_diff(md_text)

    # Ensure the added line is meaningfully different but still likely to appear in-page.
    added = removed
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
        predicted_udids = [c.get("udid") for c in candidates if isinstance(c, dict) and c.get("udid")]

        # Stage 3 suggested chunk IDs
        predicted_chunk_ids = []
        for c in candidates:
            if isinstance(c, dict):
                predicted_chunk_ids.extend(c.get("relevant_chunk_ids", []) or [])

        # De-dupe while preserving order (for readability)
        seen = set()
        predicted_chunk_ids = [x for x in predicted_chunk_ids if x and not (x in seen or seen.add(x))]

        print("\nSimilarity results")
        print("------------------")
        print("Candidate pages:", predicted_udids)
        print("Stage 3 suggested chunk IDs:", predicted_chunk_ids)
        print("Tripwire TOP_N_VERIFICATION_CANDIDATES:", getattr(tripwire, "TOP_N_VERIFICATION_CANDIDATES", None))

        # Force handover packet generation for eval (prototype)
        packets = tripwire.generate_handover_packets(
            source_name=TEST_SOURCE_NAME,
            priority=TEST_PRIORITY,
            version_id=TEST_VERSION,
            diff_file=str(diff_file),
            analysis=analysis,
            timestamp="eval"
        )

        print("Packets generated:", len(packets))

        # ---- RUN LLM VERIFICATION (Stage 4) ----
        # Prototype mode: verify Top-N candidates only.
        verification_files = tripwire.run_llm_verification_for_packets(
            packets,
            prefer_test_files=prefer_test_files,
            top_n_candidates=getattr(tripwire, "TOP_N_VERIFICATION_CANDIDATES", None)
        )

        print("Verification files:", verification_files)

        verified_udids = []
        verified_chunk_ids_pass1 = []
        confirmed_update_chunk_ids_pass2 = []
        additional_chunks_to_review = []
        llm_decision = "uncertain"

        if verification_files:
            doc = json.loads(Path(verification_files[0]).read_text(encoding="utf-8"))
            llm = doc.get("llm_result", {}) or {}

            llm_decision = llm.get("overall_decision", "uncertain")

            impacted_pages = llm.get("impacted_pages", []) or []
            verified_udids = [p.get("udid") for p in impacted_pages if isinstance(p, dict) and p.get("udid")]

            verified_chunk_ids_pass1 = [
                p.get("chunk_id") for p in impacted_pages
                if isinstance(p, dict) and p.get("chunk_id")
            ]

            confirmed_update_chunk_ids_pass2 = llm.get("confirmed_update_chunk_ids", []) or []
            additional_chunks_to_review = llm.get("additional_chunks_to_review", []) or []

        print("LLM decision:", llm_decision)
        print("Verified pages:", verified_udids)
        print("Verified chunks (Pass 1):", verified_chunk_ids_pass1)
        print("Confirmed update chunks (Pass 2):", confirmed_update_chunk_ids_pass2)
        print("Additional chunks to review (Pass 2):", additional_chunks_to_review)

        # ---- METRICS ----
        metrics = compute_metrics(predicted_udids, verified_udids, EXPECTED_IMPACTED_UDIDS)
        chunk_metrics = compute_chunk_metrics(predicted_chunk_ids, confirmed_update_chunk_ids_pass2)

        print("\nEvaluation metrics")
        print("------------------")
        for k, v in metrics.items():
            print(f"{k}: {v:.3f}")

        print("\nChunk evaluation metrics")
        print("------------------------")
        for k, v in chunk_metrics.items():
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
