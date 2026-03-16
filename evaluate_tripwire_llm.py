import json
import os
import tempfile
import csv
import difflib
from pathlib import Path
from collections import defaultdict
from markdownify import markdownify as md

# Production import
try:
    import tripwire_updated as tripwire
    from tripwire_updated import IPFR_CONTENT_ARCHIVE_DIR
except ImportError:
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
    predicted = {tripwire.canonical_chunk_id(cid) for cid in (stage3_suggested_chunk_ids or []) if cid}
    expected = {tripwire.canonical_chunk_id(cid) for cid in (llm_confirmed_chunk_ids or []) if cid}
    overlap = predicted & expected
    return {
        "chunk_precision": len(overlap) / len(predicted) if predicted else 0.0,
        "chunk_recall": len(overlap) / len(expected) if expected else 0.0,
        "predicted_chunk_count": len(predicted),
        "confirmed_chunk_count": len(expected),
        "overlap_chunk_count": len(overlap),
        "overlap_chunk_ids": sorted(overlap),
    }


def _dedupe(items):
    return list(dict.fromkeys([x for x in items if x]))


def _collect_stage3_chunk_predictions(analysis):
    page_to_chunks = {}
    all_chunk_ids = []
    for cand in analysis.get("threshold_passing_candidates", []) or []:
        udid = cand.get("udid")
        chunk_ids = [c for c in (cand.get("relevant_chunk_ids") or []) if c]
        page_to_chunks[udid] = chunk_ids
        all_chunk_ids.extend(chunk_ids)
    return page_to_chunks, _dedupe(all_chunk_ids)


def _summarise_suggestion_files(suggestion_files):
    statuses = []
    excerpts = []
    page_to_chunks = defaultdict(list)

    for s_file in suggestion_files or []:
        doc = json.loads(Path(s_file).read_text(encoding="utf-8"))
        statuses.append({
            "file": os.path.basename(s_file),
            "status": doc.get("status"),
        })
        for page in doc.get("pages", []) or []:
            udid = page.get("udid")
            for suggestion in page.get("confirmed_update_suggestions", []) or []:
                chunk_id = suggestion.get("chunk_id")
                page_to_chunks[udid].append(chunk_id)
                text = str(suggestion.get("proposed_replacement_text") or "").strip().replace("\n", " ")
                excerpts.append({
                    "udid": udid,
                    "chunk_id": chunk_id,
                    "status": suggestion.get("status"),
                    "excerpt": text[:180],
                })

    return {
        "statuses": statuses,
        "page_to_chunks": {k: _dedupe(v) for k, v in page_to_chunks.items()},
        "excerpts": excerpts,
    }


def _summarise_review_queue(queue_file):
    rows = []
    if not os.path.exists(queue_file):
        return {"row_count": 0, "rows": []}
    with open(queue_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    summarised = []
    for row in rows:
        diff_text = str(row.get("Relevant Diff Text") or "")
        hunk_headers = [h for h in HARDCODED_HUNKS if f"[{h['location_header']}]" in diff_text]
        summarised.append({
            "udid": row.get("UDID") or "",
            "chunk_id": row.get("Chunk ID") or "",
            "status": row.get("Suggestion Status") or "",
            "matched_hunk_headers": [h["location_header"] for h in hunk_headers],
            "relevant_diff_text": diff_text[:250],
        })
    return {
        "row_count": len(rows),
        "rows": summarised,
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



HTML_NOISE_OLD = """
<!doctype html>
<html>
  <body>
    <header>
      <nav>
        <a href="/home">Home</a>
        <a href="/contact">Contact us</a>
      </nav>
      <div class="breadcrumbs">Home > Guidance > Designs</div>
    </header>
    <main>
      <article>
        <h1>Design infringement</h1>
        <p>In Australia, a registered design provides designers protection for up to 10 years.</p>
        <p>Protection only covers the appearance of that product.</p>
      </article>
    </main>
    <aside>Share this page</aside>
    <footer>
      <p>Page 1 of 1</p>
      <p>Generated on: 01/01/2026 10:15am</p>
    </footer>
  </body>
</html>
"""

HTML_NOISE_ONLY_NEW = """
<!doctype html>
<html>
  <body>
    <header>
      <nav>
        <a href="/home">Home</a>
        <a href="/contact">Contact the team</a>
        <a href="/news">Latest news</a>
      </nav>
      <div class="breadcrumbs">Home > Guidance > Designs > Design infringement</div>
    </header>
    <main>
      <article>
        <h1>Design infringement</h1>
        <p>In Australia, a registered design provides designers protection for up to 10 years.</p>
        <p>Protection only covers the appearance of that product.</p>
      </article>
    </main>
    <aside>Share this page</aside>
    <footer>
      <p>Page 2 of 2</p>
      <p>Generated on: 15/03/2026 9:47am</p>
      <p>Last updated: 15 March 2026</p>
    </footer>
  </body>
</html>
"""

HTML_MIXED_NEW = """
<!doctype html>
<html>
  <body>
    <header>
      <nav>
        <a href="/home">Home</a>
        <a href="/contact">Contact the team</a>
        <a href="/news">Latest news</a>
      </nav>
      <div class="breadcrumbs">Home > Guidance > Designs > Design infringement</div>
    </header>
    <main>
      <article>
        <h1>Design infringement</h1>
        <p>In Australia, a design owner generally needs both registration and certification before they can enforce the design against another party.</p>
        <p>Protection generally covers the appearance of the specific product for which the design is registered, so using the same visual features on a different product may not amount to infringement.</p>
      </article>
    </main>
    <aside>Share this page</aside>
    <footer>
      <p>Page 2 of 2</p>
      <p>Generated on: 15/03/2026 9:47am</p>
      <p>Last updated: 15 March 2026</p>
    </footer>
  </body>
</html>
"""


def _render_html_like_tripwire(html_text):
    cleaned_html = tripwire.clean_html_content(html_text)
    return md(cleaned_html, heading_style="ATX").strip() + "\n"


def _write_diff_from_texts(old_text, new_text, diff_path):
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    diff_lines = list(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile="old_rendered.md",
        tofile="new_rendered.md",
        lineterm="",
        n=3,
    ))
    diff_text = "\n".join(diff_lines) + ("\n" if diff_lines else "")
    Path(diff_path).write_text(diff_text, encoding="utf-8")
    return diff_text


def run_noise_filter_eval():
    print("\n=== HTML noise filtering evaluation ===")
    print("TAGS_TO_EXCLUDE:", tripwire.TAGS_TO_EXCLUDE)

    old_rendered = _render_html_like_tripwire(HTML_NOISE_OLD)
    noise_only_rendered = _render_html_like_tripwire(HTML_NOISE_ONLY_NEW)
    mixed_rendered = _render_html_like_tripwire(HTML_MIXED_NEW)

    print("\nRendered HTML after Tripwire cleaning")
    print("Old rendered excerpt:", old_rendered[:220].replace("\n", " "))
    print("Noise-only rendered excerpt:", noise_only_rendered[:220].replace("\n", " "))
    print("Mixed rendered excerpt:", mixed_rendered[:220].replace("\n", " "))

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        noise_diff_path = tmp / "noise_only_html.diff"
        noise_diff_text = _write_diff_from_texts(old_rendered, noise_only_rendered, noise_diff_path)
        noise_analysis = tripwire.calculate_similarity(str(noise_diff_path), source_priority=TEST_PRIORITY)
        noise_hunks = tripwire.parse_diff_hunks(str(noise_diff_path))

        mixed_diff_path = tmp / "mixed_html.diff"
        mixed_diff_text = _write_diff_from_texts(old_rendered, mixed_rendered, mixed_diff_path)
        mixed_analysis = tripwire.calculate_similarity(str(mixed_diff_path), source_priority=TEST_PRIORITY)
        mixed_hunks = tripwire.parse_diff_hunks(str(mixed_diff_path))

        print("\nNoise-only scenario")
        print("Raw diff has content:", bool(noise_diff_text.strip()))
        print("Parsed hunks:", len(noise_hunks))
        print("Noise classification:", [
            {
                "hunk_index": h.get("hunk_index"),
                "is_noise": tripwire._is_administrative_noise(h.get("change_context", "")),
                "change_context": h.get("change_context", "")[:140],
            }
            for h in noise_hunks
        ])
        print("Similarity status:", noise_analysis.get("status"))
        print("Should handover:", noise_analysis.get("should_handover"))
        print("Candidate count:", noise_analysis.get("candidate_count"))
        print("Decision reason:", noise_analysis.get("handover_decision_reason"))

        print("\nMixed HTML scenario")
        print("Raw diff has content:", bool(mixed_diff_text.strip()))
        print("Parsed hunks:", len(mixed_hunks))
        print("Noise classification:", [
            {
                "hunk_index": h.get("hunk_index"),
                "is_noise": tripwire._is_administrative_noise(h.get("change_context", "")),
                "change_context": h.get("change_context", "")[:140],
            }
            for h in mixed_hunks
        ])
        print("Similarity status:", mixed_analysis.get("status"))
        print("Should handover:", mixed_analysis.get("should_handover"))
        print("Candidate count:", mixed_analysis.get("candidate_count"))
        print("Retrieved pages:", [c.get("udid") for c in mixed_analysis.get("threshold_passing_candidates", [])])
        print("Retrieved chunk candidates by page:")
        for cand in mixed_analysis.get("threshold_passing_candidates", []) or []:
            print(f"  {cand.get('udid')}: {cand.get('relevant_chunk_ids')}")

        if noise_analysis.get("candidate_count", 0) == 0 and not noise_analysis.get("should_handover"):
            print("Noise-only result: PASS")
        else:
            print("Noise-only result: FAIL")

        if mixed_analysis.get("candidate_count", 0) > 0 and mixed_analysis.get("should_handover"):
            print("Mixed-content result: PASS")
        else:
            print("Mixed-content result: FAIL")


def run_eval():
    tripwire.IPFR_CONTENT_ARCHIVE_DIR = REPO_ARCHIVE_DIR

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        diff_path = tmp / "eval.diff"
        diff_path.write_text(_build_multihunk_diff_text(), encoding="utf-8")

        analysis = tripwire.calculate_similarity(str(diff_path), source_priority=TEST_PRIORITY)

        print("\nStage 3 retrieval")
        predicted_udids = [c.get("udid") for c in analysis.get("threshold_passing_candidates", [])]
        print("Retrieved pages:", predicted_udids)
        stage3_page_chunks, stage3_chunk_ids = _collect_stage3_chunk_predictions(analysis)
        print("Retrieved chunk candidates by page:")
        for udid, chunk_ids in stage3_page_chunks.items():
            print(f"  {udid}: {chunk_ids}")

        packets = tripwire.generate_handover_packets(
            TEST_SOURCE_NAME, TEST_PRIORITY, TEST_VERSION, str(diff_path), analysis, "eval"
        )

        verification_files = tripwire.run_llm_verification_for_packets(packets, prefer_test_files=True)
        outcomes = tripwire.summarise_verification_files(verification_files)

        print("\nStage 4 verification")
        print("Verified pages:", outcomes["verified_udids"])
        print("Per-candidate verification summary:")
        for item in outcomes["per_candidate_summary"]:
            print(
                f"  {item['udid']} | pass1={item['pass1_decision']} | "
                f"pass1_chunks={item['pass1_chunks']} | pass2_chunks={item['pass2_chunks']} | "
                f"additional_review={item['additional_review_chunks']}"
            )
        print("Pass 1 confirmed chunks:", outcomes["pass1_confirmed_chunk_ids"])
        print("Pass 2 confirmed chunks:", outcomes["confirmed_update_chunk_ids_pass2"])
        print("Additional review chunks:", outcomes["additional_review_chunk_ids"])

        suggestion_files = tripwire.run_llm_update_suggestions_for_verification_files(
            verification_files, prefer_test_files=True
        )

        queue_file = str(Path.cwd() / "update_review_queue.csv")
        tripwire.write_update_review_queue_csv_from_suggestion_files(suggestion_files, output_path=queue_file)

        suggestion_summary = _summarise_suggestion_files(suggestion_files)
        queue_summary = _summarise_review_queue(queue_file)

        print("\nStage 5 update suggestions")
        print("Suggestion files:", [os.path.basename(p) for p in suggestion_files])
        print("Update suggestion statuses:", suggestion_summary["statuses"])
        print("Suggested update chunks by page:", suggestion_summary["page_to_chunks"])
        print("Short excerpts of suggested updates:")
        for item in suggestion_summary["excerpts"]:
            print(f"  {item['udid']} | {item['chunk_id']} | {item['status']} | {item['excerpt']}")

        print("\nReview queue summary")
        print("Row count:", queue_summary["row_count"])
        for row in queue_summary["rows"]:
            print(
                f"  {row['udid']} | {row['chunk_id']} | {row['status']} | "
                f"hunks={row['matched_hunk_headers']}"
            )

        metrics = compute_metrics(predicted_udids, outcomes["verified_udids"], EXPECTED_IMPACTED_UDIDS)
        chunk_metrics = compute_chunk_metrics(stage3_chunk_ids, outcomes["confirmed_update_chunk_ids_pass2"])

        print("\nEvaluation metrics")
        for k, v in metrics.items():
            print(f"{k}: {v:.3f}")
        for k, v in chunk_metrics.items():
            if isinstance(v, float):
                print(f"{k}: {v:.3f}")
            else:
                print(f"{k}: {v}")

        if not predicted_udids:
            print("\nEND STATE: RETRIEVAL MISS")
        elif not outcomes["verified_udids"]:
            print("\nEND STATE: LLM VERIFICATION MISS")
        elif not outcomes["confirmed_update_chunk_ids_pass2"]:
            print("\nEND STATE: PASS 2 CONFIRMATION MISS")
        elif not suggestion_files:
            print("\nEND STATE: UPDATE SUGGESTION MISS")
        elif any(status.get("status") == "Partial Suggestion Generated" for status in suggestion_summary["statuses"]):
            print("\nEND STATE: PARTIAL UPDATE SUGGESTION")
        else:
            print("\nEND STATE: UPDATE SUGGESTION GENERATED")

        print(f"\nReview queue file: {queue_file}")


if __name__ == "__main__":
    run_eval()
    run_noise_filter_eval()
