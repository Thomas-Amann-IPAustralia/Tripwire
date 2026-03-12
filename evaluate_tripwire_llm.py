import json
import os
import re
import tempfile
import csv
from pathlib import Path

# Production import
import tripwire
from tripwire import IPFR_CONTENT_ARCHIVE_DIR

print("API KEY PRESENT:", bool(os.getenv("OPENAI_API_KEY")))

TEST_SOURCE_NAME = "LLM_EVAL_TEST"
TEST_PRIORITY = "High"
TEST_VERSION = "eval_v2_end_to_end"
EXPECTED_IMPACTED_UDIDS = {"101-1", "101-2"}
REPO_ARCHIVE_DIR = str(Path(IPFR_CONTENT_ARCHIVE_DIR))

# Hardcoded multi-hunk diff
HARDCODED_HUNKS = [
    {
        "hunk_id": 1,
        "location_header": "design certification before enforcement",
        "removed": ["In Australia, a registered design provides designers protection for up to 10 years."],
        "added": ["In Australia, a design owner generally needs both registration and certification before they can enforce the design against another party."],
    },
    {
        "hunk_id": 2,
        "location_header": "searching before launch",
        "removed": ["One of the most effective ways to avoid IP infringement is to check for existing rights before committing to a new venture."],
        "added": ["One of the most effective ways to avoid IP infringement is to search relevant registers and key market signals before launching a new product, brand, service, or design."],
    },
    {
        "hunk_id": 3,
        "location_header": "exact design on different product may not infringe",
        "removed": ["Protection only covers the appearance of that product."],
        "added": ["Protection generally covers the appearance of the specific product for which the design is registered, so using the same visual features on a different product may not amount to infringement."],
    },
]

def compute_metrics(predicted, verified, expected):
    predicted = set(predicted or [])
    verified = set(verified or [])
    expected = set(expected or [])
    return {
        "retrieval_precision": len(predicted & expected) / len(predicted) if predicted else 0.0,
        "retrieval_recall": len(predicted & expected) / len(expected) if expected else 0.0,
        "verifier_precision": len(verified & expected) / len(verified) if verified else 0.0,
        "verifier_recall": len(verified & expected) / len(expected) if expected else 0.0,
    }

def compute_chunk_metrics(stage3_suggested_chunk_ids, llm_confirmed_chunk_ids):
    # Replicating live run: Use production canonical_chunk_id
    predicted = {tripwire.canonical_chunk_id(cid) for cid in (stage3_suggested_chunk_ids or []) if cid}
    expected = {tripwire.canonical_chunk_id(cid) for cid in (llm_confirmed_chunk_ids or []) if cid}
    overlap = predicted & expected
    return {
        "chunk_precision": len(overlap) / len(predicted) if predicted else 0.0,
        "chunk_recall": len(overlap) / len(expected) if expected else 0.0,
    }

def _collect_verification_outcomes(verification_files):
    # This logic now mirrors Stage 4 verification processing
    verified_udids = []
    confirmed_update_chunk_ids_pass2 = []
    
    for v_file in verification_files or []:
        doc = json.loads(Path(v_file).read_text(encoding="utf-8"))
        impacted = (doc.get("llm_result") or {}).get("impacted_pages") or []
        for page in impacted:
            verified_udids.append(page.get("udid"))
            
        for cand in doc.get("per_candidate", []):
            pass2 = cand.get("pass2_result") or {}
            # Use production helper for chunk extraction
            confirmed_ids = tripwire._extract_confirmed_update_chunk_ids(pass2, cand.get("best_chunk_id"))
            confirmed_update_chunk_ids_pass2.extend(confirmed_ids)
            
    return {
        "verified_udids": list(dict.fromkeys(verified_udids)),
        "confirmed_update_chunk_ids_pass2": list(dict.fromkeys(confirmed_update_chunk_ids_pass2)),
    }

def _build_multihunk_diff_text():
    lines = ["--- old", "+++ new"]
    for hunk in HARDCODED_HUNKS:
        lines.extend([
            f"@@ -1,1 +1,1 @@ {hunk['location_header']}",
            f"-{hunk['removed'][0]}",
            f"+{hunk['added'][0]}",
        ])
    return "\n".join(lines) + "\n"

def run_eval():
    tripwire.IPFR_CONTENT_ARCHIVE_DIR = REPO_ARCHIVE_DIR
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        diff_path = tmp / "eval.diff"
        diff_path.write_text(_build_multihunk_diff_text(), encoding="utf-8")

        # Live run replication: Standard pipeline call
        analysis = tripwire.calculate_similarity(str(diff_path), source_priority=TEST_PRIORITY)
        
        # Diagnostic prints (requested to remain)
        print("\nStage 3 retrieval")
        predicted_udids = [c.get("udid") for c in analysis.get("threshold_passing_candidates", [])]
        print("Retrieved pages:", predicted_udids)

        packets = tripwire.generate_handover_packets(
            TEST_SOURCE_NAME, TEST_PRIORITY, TEST_VERSION, str(diff_path), analysis, "eval"
        )
        
        verification_files = tripwire.run_llm_verification_for_packets(packets, prefer_test_files=True)
        outcomes = _collect_verification_outcomes(verification_files)
        
        suggestion_files = tripwire.run_llm_update_suggestions_for_verification_files(verification_files, prefer_test_files=True)
        
        # NEW: Replicate the live CSV generation
        queue_file = "update_review_queue.csv"
        tripwire.write_update_review_queue_csv_from_suggestion_files(suggestion_files, output_path=queue_file)
        
        # Final Verification
        metrics = compute_metrics(predicted_udids, outcomes["verified_udids"], EXPECTED_IMPACTED_UDIDS)
        print("\nEvaluation metrics")
        for k, v in metrics.items(): print(f"{k}: {v:.3f}")

if __name__ == "__main__":
    run_eval()
