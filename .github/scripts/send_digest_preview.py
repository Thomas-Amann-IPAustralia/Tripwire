"""One-off: send a PREVIEW of the weekly digest as it would have gone out on
Monday 9 November 2026 (covering the runs from 2 to 8 November).

Rebuilt from real data: the run log and scores in ipfr.sqlite (pipeline_runs,
pages), the LLM verdicts in "data/LLM Reports/", and the change documents
from git history. Read-only: nothing is written to the database.

Usage: python .github/scripts/send_digest_preview.py [--dry-run]
"""

from __future__ import annotations

import csv
import glob
import json
import os
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config import load_config  # noqa: E402
from src.stage9_notification import _build_mime, _send_via_smtp  # noqa: E402
from src.weekly_digest import _recipients, compose_digest, run_stats  # noqa: E402

WINDOW_START = "2026-11-01T22:30"   # Mon 2 Nov 09:30 AEDT
WINDOW_END = "2026-11-08T22:30"     # Mon 9 Nov 09:30 AEDT
BANNER = ("PREVIEW — a re-creation of the weekly digest that would have been sent on "
          "Monday 9 November 2026 at 9:30am, sent so you can see the format. The 4 "
          "verdicts are real (run 2026-11-05-504); the feedback links work and are "
          "recorded against that run.")


def _git_text(path: str) -> str:
    """Latest committed version of a (possibly since-deleted) file."""
    try:
        sha = subprocess.run(["git", "log", "--all", "-1", "--format=%H", "--", path],
                             capture_output=True, text=True, check=True).stdout.strip()
        if not sha:
            return ""
        return subprocess.run(["git", "show", f"{sha}:{path}"], capture_output=True,
                              text=True, check=True).stdout
    except subprocess.CalledProcessError:
        return ""


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    config = load_config("tripwire_config.yaml")
    conn = sqlite3.connect("file:data/ipfr_corpus/ipfr.sqlite?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    registry = {r["source_id"]: r for r in
                csv.DictReader(open("data/influencer_sources/source_registry.csv"))}
    rows = conn.execute(
        "SELECT * FROM pipeline_runs WHERE timestamp >= ? AND timestamp < ? "
        "ORDER BY timestamp", (WINDOW_START, WINDOW_END)).fetchall()
    run_ids = list(dict.fromkeys(r["run_id"] for r in rows))
    print(f"Runs in window: {run_ids}")

    meta_rows = conn.execute("SELECT page_id, title, url FROM pages").fetchall()
    meta = {r["page_id"]: {"page_id": r["page_id"], "title": r["title"], "url": r["url"]}
            for r in csv.DictReader(open("data/ipfr_corpus/sitemap.csv"))}
    meta.update({m["page_id"]: {"page_id": m["page_id"], "title": m["title"], "url": m["url"]}
                 for m in meta_rows if m["title"]})

    payloads = []
    for run_id in run_ids:
        run_rows = [dict(r) for r in rows if r["run_id"] == run_id]
        for r in run_rows:
            r["details"] = json.loads(r["details"] or "{}")
        reports = [json.load(open(f)) for f in
                   sorted(glob.glob(f"data/LLM Reports/{run_id}_*.json"))]

        flagged = []
        for rep in reports:
            if rep["verdict"] not in ("CHANGE_REQUIRED", "UNCERTAIN"):
                continue
            pid = rep["ipfr_page_id"]
            triggers = []
            for r in run_rows:
                if pid not in json.loads(r.get("triggered_pages") or "[]"):
                    continue
                st = r["details"].get("stages", {})
                s4 = next((c for c in st.get("relevance", {}).get("top_candidates", [])
                           if c["page_id"] == pid), {})
                s5 = next((p for p in st.get("biencoder", {}).get("pages", [])
                           if p["page_id"] == pid), {})
                s6 = next((p for p in st.get("crossencoder", {}).get("pages", [])
                           if p["page_id"] == pid), {})
                diff_path = st.get("diff", {}).get("diff_path") or ""
                if "data/" in diff_path:
                    diff_path = diff_path[diff_path.index("data/"):]
                reg = registry.get(r["source_id"], {})
                triggers.append({
                    "source_id": r["source_id"], "source_url": r["source_url"],
                    "source_importance": float(reg.get("importance") or 0.5),
                    "source_type": r["source_type"],
                    "diff_text": _git_text(diff_path) if diff_path else "",
                    "significance": st.get("change_detection", {}).get("significance", "standard"),
                    "stage4_final_score": float(s4.get("final_score", 0.0)),
                    "stage4_rrf_score": 0.0, "stage4_bm25_rank": 0, "stage4_semantic_rank": 0,
                    "biencoder_max_chunk_score": float(s5.get("max_chunk_score", 0.0)),
                    "biencoder_chunks_above_threshold": int(s5.get("chunks_above_low_medium", 0)),
                    "crossencoder_score": float(s6.get("crossencoder_score", 0.0)),
                    "crossencoder_reranked_score": float(s6.get("reranked_score", 0.0)),
                    "crossencoder_final_score": float(s6.get("final_score", 0.0)),
                })
            a = {k: rep.get(k) for k in (
                "ipfr_page_id", "verdict", "confidence", "reasoning", "suggested_changes",
                "model", "prompt_tokens", "completion_tokens", "total_tokens", "retries",
                "schema_valid", "processing_time_seconds")}
            a["raw_response"] = ""
            flagged.append({"assessment": a,
                            "bundle": {"ipfr_page_id": pid, "triggers": triggers} if triggers else None,
                            "meta": meta.get(pid)})
            print(f"  {run_id} {pid}: {rep['verdict']}, {len(triggers)} trigger source(s)")

        rejected = []
        for r in run_rows:
            for p in r["details"].get("stages", {}).get("crossencoder", {}).get("pages", []):
                if p.get("decision") != "proceed":
                    rejected.append({"source_id": r["source_id"], "source_url": r["source_url"],
                                     "ipfr_page_id": p["page_id"],
                                     "rejection_stage": "crossencoder",
                                     "crossencoder_score": p.get("crossencoder_score"),
                                     "reranked_score": p.get("reranked_score")})

        payloads.append({
            "run_id": run_id, "flagged": flagged, "assessed_count": len(reports),
            "no_change_count": sum(1 for x in reports if x["verdict"] == "NO_CHANGE"),
            "rejected": rejected, "health_alerts": [], "stats": run_stats(run_rows),
        })

    feedback_email = os.environ.get("FEEDBACK_EMAIL") or "tripwire-feedback@gmail.com"
    subject, text, html, flagged_n = compose_digest(
        payloads, date(2026, 11, 2), date(2026, 11, 8), feedback_email)
    subject = f"[PREVIEW] {subject}"
    text = f"{BANNER}\n\n{text}"
    html = html.replace(
        "</style></head><body>",
        "</style></head><body><p style=\"background:#fff3cd;border:1px solid #e0c36a;"
        f"padding:10px;border-radius:4px;\"><strong>{BANNER}</strong></p>", 1)
    html = html.replace("<h1>Tripwire weekly digest", "<h1>[PREVIEW] Tripwire weekly digest", 1)

    recipients = _recipients(config)
    print(f"Subject: {subject}\nTo: {recipients}\nFlagged pages: {flagged_n}")
    if dry_run:
        print(text[:6000])
        return 0

    sender = feedback_email
    msg = _build_mime(subject, text, html, sender, ", ".join(recipients), sender)
    for r in recipients:
        _send_via_smtp(msg, host="smtp.gmail.com", port=587,
                       user=os.environ["SMTP_USER"], password=os.environ["SMTP_PASSWORD"],
                       sender=sender, recipient=r)
    print("SENT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
