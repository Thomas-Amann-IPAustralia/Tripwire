import json
import os
import re
import tempfile
from pathlib import Path

import tripwire
from tripwire import IPFR_CONTENT_ARCHIVE_DIR

print("API KEY PRESENT:", bool(os.getenv("OPENAI_API_KEY")))

"""
Tripwire LLM Evaluation Script (Prototype end-to-end)

Runs in GitHub Actions (not pytest) to avoid flaky CI.

Evaluates the intended prototype chain:
1) Stage 3 retrieval
2) Stage 4 LLM verification
3) Stage 5 LLM content update suggestions

This scenario uses a hardcoded multi-hunk diff designed to impact both:
- 101-1 How to avoid infringing others' intellectual property
- 101-2 Design infringement
"""

TEST_SOURCE_NAME = "LLM_EVAL_TEST"
TEST_PRIORITY = "High"
TEST_VERSION = "eval_v2_end_to_end"
EXPECTED_IMPACTED_UDIDS = {"101-1", "101-2"}
REPO_ARCHIVE_DIR = str(Path(IPFR_CONTENT_ARCHIVE_DIR))

# Hardcoded multi-hunk diff deliberately crafted to hit both test pages.
HARDCODED_HUNKS = [
    {
        "label": "design certification before enforcement",
        "removed": (
            "In Australia, a registered design provides designers protection for up to 10 years."
        ),
        "added": (
            "In Australia, a design owner generally needs both registration and certification before they can enforce the design against another party."
        ),
    },
    {
        "label": "searching before launch",
        "removed": (
            "One of the most effective ways to avoid IP infringement is to check for existing rights before committing to a new venture."
        ),
        "added": (
            "One of the most effective ways to avoid IP infringement is to search relevant registers and key market signals before launching a new product, brand, service, or design."
        ),
    },
    {
        "label": "exact design on different product may not infringe",
        "removed": (
            "Protection only covers the appearance of that product."
        ),
        "added": (
            "Protection generally covers the appearance of the specific product for which the design is registered, so using the same visual features on a different product may not amount to infringement."
        ),
    },
]


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

    match = re.fullmatch(r"(\d+-\d+)[_-](\d+)", value)
    if match:
        return f"{match.group(1)}-{match.group(2).zfill(2)}"

    return value


def compute_chunk_metrics(stage3_suggested_chunk_ids, llm_confirmed_chunk_ids):
    predicted = {
        _normalise_chunk_id(chunk_id)
        for chunk_id in (stage3_suggested_chunk_ids or [])
        if _normalise_chunk_id(chunk_id)
    }
    expected = {
        _normalise_chunk_id(chunk_id)
        for chunk_id in (llm_confirmed_chunk_ids or [])
        if _normalise_chunk_id(chunk_id)
    }
    overlap = predicted & expected

    precision = len(overlap) / len(predicted) if predicted else 0.0
    recall = len(overlap) / len(expected) if expected else 0.0

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

        for candidate in doc.get("per_candidate", []) or []:
            if not isinstance(candidate, dict):
                continue

            pass2_result = candidate.get("pass2_result")
            if not isinstance(pass2_result, dict):
                pass2_result = {}

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


def _parse_stage5_outputs(suggestion_files):
    statuses = []
    excerpts = []
    suggested_chunk_pairs = []

    for suggestion_file in suggestion_files or []:
        doc = json.loads(Path(suggestion_file).read_text(encoding="utf-8"))
        statuses.append({
            "file": suggestion_file,
            "status": doc.get("status", "unknown"),
        })

        for page in doc.get("pages", []) or []:
            udid = page.get("udid")
            for item in page.get("confirmed_update_suggestions", []) or []:
                chunk_id = item.get("chunk_id")
                status = item.get("status")
                replacement = (item.get("proposed_replacement_text") or "").strip()
                if udid and chunk_id:
                    suggested_chunk_pairs.append(f"{udid}:{chunk_id}")
                if replacement:
                    excerpt = re.sub(r"\s+", " ", replacement)[:220]
                    excerpts.append({
                        "file": suggestion_file,
                        "udid": udid,
                        "chunk_id": chunk_id,
                        "status": status,
                        "excerpt": excerpt,
                    })

    return {
        "statuses": statuses,
        "excerpts": excerpts,
        "suggested_chunk_pairs": _dedupe_preserve_order(suggested_chunk_pairs),
    }


def _determine_end_state(predicted_udids, verified_udids, confirmed_update_chunk_ids_pass2, stage5_statuses):
    predicted_set = set(predicted_udids or [])
    verified_set = set(verified_udids or [])
    expected_set = set(EXPECTED_IMPACTED_UDIDS)
    stage5_status_values = [row.get("status") for row in (stage5_statuses or [])]

    if expected_set - predicted_set:
        return "RETRIEVAL MISS"
    if expected_set - verified_set:
        return "LLM VERIFICATION MISS"
    if not confirmed_update_chunk_ids_pass2:
        return "PASS 2 CONFIRMATION MISS"
    if not stage5_status_values:
        return "UPDATE SUGGESTION MISS"
    if any(status == "Partial Suggestion Generated" for status in stage5_status_values):
        return "PARTIAL UPDATE SUGGESTION"
    if not any(status == "Suggestion Generated" for status in stage5_status_values):
        return "UPDATE SUGGESTION MISS"
    return "END-TO-END PROTOTYPE SUCCESS"


def _build_multihunk_diff_text():
    lines = ["--- old", "+++ new"]
    for index, hunk in enumerate(HARDCODED_HUNKS, start=1):
        lines.extend([
            f"@@ -{index},1 +{index},1 @@ {hunk['label']}",
            f"-{hunk['removed']}",
            f"+{hunk['added']}",
        ])
    return "\n".join(lines) + "\n"


def run_eval():
    tripwire.IPFR_CONTENT_ARCHIVE_DIR = REPO_ARCHIVE_DIR

    prefer_test_files = True
    top_n = getattr(tripwire, "TOP_N_VERIFICATION_CANDIDATES", None)

    # Fail early if the expected repo archive fixtures cannot be resolved.
    missing_pages = []
    for udid in sorted(EXPECTED_IMPACTED_UDIDS):
        resolved = tripwire.resolve_ipfr_content_files(udid, prefer_test_files=prefer_test_files)
        if not resolved.get("markdown_path") or not resolved.get("jsonld_path"):
            missing_pages.append({"udid": udid, **resolved})
    if missing_pages:
        raise RuntimeError(
            "Could not resolve required test fixtures under "
            f"{REPO_ARCHIVE_DIR}: {missing_pages}"
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        diff_file = tmp / "multi_page_multi_hunk_eval.diff"
        diff_file.write_text(_build_multihunk_diff_text(), encoding="utf-8")

        analysis = tripwire.calculate_similarity(str(diff_file), source_priority=TEST_PRIORITY)

        candidates = analysis.get("threshold_passing_candidates", []) or []
        predicted_udids = [c.get("udid") for c in candidates if isinstance(c, dict) and c.get("udid")]

        predicted_chunk_ids = []
        for candidate in candidates:
            if isinstance(candidate, dict):
                predicted_chunk_ids.extend(candidate.get("relevant_chunk_ids", []) or [])
        predicted_chunk_ids = _dedupe_preserve_order(predicted_chunk_ids)

        print("\nPrototype configuration")
        print("-----------------------")
        print("Repo archive path:", tripwire.IPFR_CONTENT_ARCHIVE_DIR)
        print("Expected impacted pages:", sorted(EXPECTED_IMPACTED_UDIDS))
        print("Top-N verification candidates:", top_n)
        print("Diff hunks:", len(HARDCODED_HUNKS))

        print("\nStage 3 retrieval")
        print("-----------------")
        print("Retrieved pages:", predicted_udids)
        print("Stage 3 suggested chunk IDs:", predicted_chunk_ids)

        packets = tripwire.generate_handover_packets(
            source_name=TEST_SOURCE_NAME,
            priority=TEST_PRIORITY,
            version_id=TEST_VERSION,
            diff_file=str(diff_file),
            analysis=analysis,
            timestamp="eval",
        )
        print("Handover packets:", packets)

        verification_files = tripwire.run_llm_verification_for_packets(
            packets,
            prefer_test_files=prefer_test_files,
            top_n_candidates=top_n,
        )
        print("\nStage 4 verification")
        print("--------------------")
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
        print("Confirmed Pass 2 chunks:", confirmed_update_chunk_ids_pass2)
        print("Additional review chunks:", additional_chunks_to_review)

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

        suggestion_files = tripwire.run_llm_update_suggestions_for_verification_files(
            verification_files,
            prefer_test_files=prefer_test_files,
        )
        print("\nStage 5 update suggestions")
        print("--------------------------")
        print("Update suggestion files:", suggestion_files)

        stage5 = _parse_stage5_outputs(suggestion_files)
        print("Update suggestion statuses:", stage5["statuses"])
        print("Suggested update chunks:", stage5["suggested_chunk_pairs"])
        print("Short excerpts of suggested updates:")
        if stage5["excerpts"]:
            for row in stage5["excerpts"]:
                print(
                    " -",
                    {
                        "file": row["file"],
                        "udid": row["udid"],
                        "chunk_id": row["chunk_id"],
                        "status": row["status"],
                        "excerpt": row["excerpt"],
                    },
                )
        else:
            print(" - []")

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

        end_state = _determine_end_state(
            predicted_udids=predicted_udids,
            verified_udids=verified_udids,
            confirmed_update_chunk_ids_pass2=confirmed_update_chunk_ids_pass2,
            stage5_statuses=stage5["statuses"],
        )
        print(f"\nFinal status: {end_state}")


if __name__ == "__main__":
    run_eval()
