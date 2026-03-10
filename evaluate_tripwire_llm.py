import json
import os
import re
import tempfile
from pathlib import Path

import tripwire

print("API KEY PRESENT:", bool(os.getenv("OPENAI_API_KEY")))

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


HARDCODED_REMOVED_LINE = (
    "Design infringement can occur when someone uses a design that is identical or similar "
    "to a registered design without obtaining permission from the owner."
)

HARDCODED_ADDED_LINE = (
    "Design infringement can occur only when someone uses a design that is identical "
    "to the registered design without obtaining permission from the owner."
)


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


def _normalise_chunk_id(chunk_id):
    if not chunk_id or not isinstance(chunk_id, str):
        return None

    value = chunk_id.strip()
    if not value:
        return None

    # Normalise common prototype variants such as:
    #   101-2_01  -> 101-2-01
    #   101-2-01  -> 101-2-01
    match = re.fullmatch(r"(\d+-\d+)[_-](\d+)", value)
    if match:
        return f"{match.group(1)}-{match.group(2).zfill(2)}"

    return value


def compute_chunk_metrics(stage3_suggested_chunk_ids, llm_confirmed_chunk_ids):
    stage3 = {
        _normalise_chunk_id(chunk_id)
        for chunk_id in (stage3_suggested_chunk_ids or [])
        if _normalise_chunk_id(chunk_id)
    }
    confirmed = {
        _normalise_chunk_id(chunk_id)
        for chunk_id in (llm_confirmed_chunk_ids or [])
        if _normalise_chunk_id(chunk_id)
    }
    overlap = stage3 & confirmed

    precision = len(overlap) / len(stage3) if stage3 else 0.0
    recall = len(overlap) / len(confirmed) if confirmed else 0.0

    return {
        "chunk_precision": precision,
        "chunk_recall": recall,
    }


def _dedupe_preserve_order(values):
    seen = set()
    out = []
    for value in values or []:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _collect_verification_outcomes(verification_files):
    verified_udids = []
    verified_chunk_ids_pass1 = []
    confirmed_update_chunk_ids_pass2 = []
    additional_chunks_to_review = []
    llm_decisions = []
    per_candidate_debug = []

    for verification_file in verification_files or []:
        doc = json.loads(Path(verification_file).read_text(encoding="utf-8"))
        llm = doc.get("llm_result", {}) or {}
        llm_decisions.append(llm.get("overall_decision", "uncertain"))

        impacted_pages = llm.get("impacted_pages", []) or []
        for page in impacted_pages:
            if not isinstance(page, dict):
                continue
            udid = page.get("udid")
            chunk_id = page.get("chunk_id")
            if udid:
                verified_udids.append(udid)
            if chunk_id:
                verified_chunk_ids_pass1.append(chunk_id)

        for candidate in llm.get("per_candidate", []) or []:
            if not isinstance(candidate, dict):
                continue
            pass2_result = candidate.get("pass2_result", {}) or {}
            confirmed_update_chunk_ids_pass2.extend(
                pass2_result.get("confirmed_update_chunk_ids", []) or []
            )
            additional_chunks_to_review.extend(
                pass2_result.get("additional_chunks_to_review", []) or []
            )
            per_candidate_debug.append(
                {
                    "file": verification_file,
                    "udid": candidate.get("udid"),
                    "candidate_rank": candidate.get("candidate_rank"),
                    "best_chunk_id": candidate.get("best_chunk_id"),
                    "pass1_decision": (candidate.get("pass1_result", {}) or {}).get("decision"),
                    "pass2_confirmed_update_chunk_ids": pass2_result.get("confirmed_update_chunk_ids", []) or [],
                }
            )

    verified_udids = _dedupe_preserve_order(verified_udids)
    verified_chunk_ids_pass1 = _dedupe_preserve_order(verified_chunk_ids_pass1)
    confirmed_update_chunk_ids_pass2 = _dedupe_preserve_order(confirmed_update_chunk_ids_pass2)
    additional_chunks_to_review = _dedupe_preserve_order(additional_chunks_to_review)

    overall_decision = "uncertain"
    if "impact" in llm_decisions:
        overall_decision = "impact"
    elif llm_decisions and all(decision == "no_impact" for decision in llm_decisions):
        overall_decision = "no_impact"

    return {
        "overall_decision": overall_decision,
        "verified_udids": verified_udids,
        "verified_chunk_ids_pass1": verified_chunk_ids_pass1,
        "confirmed_update_chunk_ids_pass2": confirmed_update_chunk_ids_pass2,
        "additional_chunks_to_review": additional_chunks_to_review,
        "per_candidate_debug": per_candidate_debug,
    }


def run_eval():
    # Ensure Stage 4 can find the test fixtures in the repo
    tripwire.IPFR_CONTENT_ARCHIVE_DIR = "IPFR_content_archive"

    # Prefer *_test.* fixtures
    prefer_test_files = True

    # Resolve the 101-2 markdown fixture so Stage 4 can still verify against the
    # page content in IPFR_content_archive, but use a hardcoded diff payload so the
    # evaluation is deterministic and never depends on sentence extraction.
    resolved = tripwire.resolve_ipfr_content_files("101-2", prefer_test_files=prefer_test_files)
    md_path = resolved.get("markdown_path")
    if not md_path:
        raise RuntimeError("Could not resolve 101-2 markdown fixture in IPFR_content_archive")

    removed = HARDCODED_REMOVED_LINE
    added = HARDCODED_ADDED_LINE

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        diff_file = tmp / "design_change.diff"

        diff_file.write_text(
            "--- old\n"
            "+++ new\n"
            "@@ -1 +1 @@\n"
            f"-{removed}\n"
            f"+{added}\n",
            encoding="utf-8",
        )

        # ---- RUN SIMILARITY (Stage 3) ----
        analysis = tripwire.calculate_similarity(str(diff_file), source_priority=TEST_PRIORITY)

        candidates = analysis.get("threshold_passing_candidates", []) or []
        predicted_udids = [c.get("udid") for c in candidates if isinstance(c, dict) and c.get("udid")]

        # Stage 3 suggested chunk IDs
        predicted_chunk_ids = []
        for candidate in candidates:
            if isinstance(candidate, dict):
                predicted_chunk_ids.extend(candidate.get("relevant_chunk_ids", []) or [])

        predicted_chunk_ids = _dedupe_preserve_order(predicted_chunk_ids)

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
            timestamp="eval",
        )

        print("Packets generated:", len(packets))

        # ---- RUN LLM VERIFICATION (Stage 4) ----
        # Prototype mode: verify Top-N candidates only.
        verification_files = tripwire.run_llm_verification_for_packets(
            packets,
            prefer_test_files=prefer_test_files,
            top_n_candidates=getattr(tripwire, "TOP_N_VERIFICATION_CANDIDATES", None),
        )

        print("Verification files:", verification_files)

        verification_outcomes = _collect_verification_outcomes(verification_files)
        llm_decision = verification_outcomes["overall_decision"]
        verified_udids = verification_outcomes["verified_udids"]
        verified_chunk_ids_pass1 = verification_outcomes["verified_chunk_ids_pass1"]
        confirmed_update_chunk_ids_pass2 = verification_outcomes["confirmed_update_chunk_ids_pass2"]
        additional_chunks_to_review = verification_outcomes["additional_chunks_to_review"]

        print("LLM decision:", llm_decision)
        print("Verified pages:", verified_udids)
        print("Verified chunks (Pass 1):", verified_chunk_ids_pass1)
        print("Confirmed update chunks (Pass 2):", confirmed_update_chunk_ids_pass2)
        print("Additional chunks to review (Pass 2):", additional_chunks_to_review)

        if verification_outcomes["per_candidate_debug"]:
            print("Per-candidate verification summary:")
            for row in verification_outcomes["per_candidate_debug"]:
                print(
                    " -",
                    {
                        "file": row["file"],
                        "udid": row["udid"],
                        "candidate_rank": row["candidate_rank"],
                        "best_chunk_id": row["best_chunk_id"],
                        "pass1_decision": row["pass1_decision"],
                        "pass2_confirmed_update_chunk_ids": row["pass2_confirmed_update_chunk_ids"],
                    },
                )

        # ---- METRICS ----
        metrics = compute_metrics(predicted_udids, verified_udids, EXPECTED_IMPACTED_UDIDS)
        chunk_metrics = compute_chunk_metrics(predicted_chunk_ids, confirmed_update_chunk_ids_pass2)

        print("\nEvaluation metrics")
        print("------------------")
        for key, value in metrics.items():
            print(f"{key}: {value:.3f}")

        print("\nChunk evaluation metrics")
        print("------------------------")
        for key, value in chunk_metrics.items():
            print(f"{key}: {value:.3f}")

        # ---- INTERPRETATION ----
        if EXPECTED_IMPACTED_UDIDS - set(predicted_udids):
            print("\nFailure type: RETRIEVAL MISS")
        elif EXPECTED_IMPACTED_UDIDS - set(verified_udids):
            print("\nFailure type: LLM VERIFICATION MISS")
        else:
            print("\nPipeline result: SUCCESS")


if __name__ == "__main__":
    run_eval()
