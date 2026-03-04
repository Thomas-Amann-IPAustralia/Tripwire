import json
import csv
import tempfile
from pathlib import Path
import tripwire

"""
Tripwire LLM Evaluation Script

Purpose
-------
Evaluate:

1. Similarity scoring effectiveness (retrieval): Did similarity scoring include the correct page in candidates?
2. LLM verification effectiveness (verifier): Given the candidates, did the LLM correctly identify impacted pages?
3. End-to-end pipeline behaviour

This script runs outside pytest to avoid flaky CI results.
"""


# ---- CONFIG ----

TEST_SOURCE_NAME = "LLM_EVAL_TEST"
TEST_PRIORITY = "High"
TEST_VERSION = "eval_v1"

# Expected ground truth
EXPECTED_IMPACTED_UDIDS = {"101-2"}


# ---- HELPER ----

def compute_metrics(predicted, verified, expected):

    predicted = set(predicted)
    verified = set(verified)
    expected = set(expected)

    retrieval_recall = len(predicted & expected) / len(expected) if expected else 0
    retrieval_precision = len(predicted & expected) / len(predicted) if predicted else 0

    verifier_recall = len(verified & expected) / len(expected) if expected else 0
    verifier_precision = len(verified & expected) / len(verified) if verified else 0

    return {
        "retrieval_precision": retrieval_precision,
        "retrieval_recall": retrieval_recall,
        "verifier_precision": verifier_precision,
        "verifier_recall": verifier_recall
    }


# ---- MAIN ----

def run_eval():

    with tempfile.TemporaryDirectory() as tmp:

        tmp = Path(tmp)

        diff_file = tmp / "design_change.diff"
        audit_log = tmp / "audit_log.csv"

        # ---- create diff ----

        diff_file.write_text(
            "--- old\n"
            "+++ new\n"
            "@@ -1 +1 @@\n"
            "-Design infringement occurs when a design is copied.\n"
            "+Design infringement occurs when someone copies a registered design.\n"
        )

        # ---- create audit log ----

        with open(audit_log, "w", newline="", encoding="utf-8") as f:

            writer = csv.DictWriter(f, fieldnames=tripwire.AUDIT_HEADERS)
            writer.writeheader()

            writer.writerow({
                "Timestamp": "eval",
                "Source_Name": TEST_SOURCE_NAME,
                "Priority": TEST_PRIORITY,
                "Status": "Success",
                "Change_Detected": "Yes",
                "Version_ID": TEST_VERSION,
                "Diff_File": str(diff_file)
            })

        # patch runtime paths

        tripwire.AUDIT_LOG = str(audit_log)

        # ---- RUN SIMILARITY ----

        analysis = tripwire.calculate_similarity(str(diff_file), TEST_PRIORITY)

        print("\nSimilarity results")
        print("------------------")

        candidates = analysis.get("threshold_passing_candidates", [])

        predicted_udids = [c["udid"] for c in candidates]

        print("Candidate pages:", predicted_udids)

        # ---- GENERATE PACKET ----

        packets = tripwire.generate_handover_packets(
            source_name=TEST_SOURCE_NAME,
            priority=TEST_PRIORITY,
            version_id=TEST_VERSION,
            diff_file=str(diff_file),
            analysis=analysis,
            timestamp="eval"
        )

        print("\nPackets generated:", len(packets))

        # ---- RUN LLM VERIFICATION ----

        results = tripwire.run_llm_verification_for_packets(packets)

        print("Verification files:", results)

        verified_udids = []

        if results:

            with open(results[0]) as f:
                doc = json.load(f)

            llm = doc["llm_result"]

            verified_udids = [p["udid"] for p in llm.get("impacted_pages", [])]

            print("\nLLM decision:", llm["overall_decision"])
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
