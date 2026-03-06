import os
import json
import csv
import numpy as np
import pytest
from pathlib import Path
from unittest.mock import patch

import tripwire
import evaluate_tripwire_llm


@pytest.fixture
def mock_embeddings_file(tmp_path):
    """Creates a dummy semantic embeddings file in the correct flat-list format."""
    file_path = tmp_path / "Semantic_Embeddings_Output.json"
    data = [
        {
            "UDID": "udid-1",
            "Chunk_ID": "U1_C1",
            "Chunk_Text": "The penalty for late fees is $250,000.",
            "Headline_Alt": "Fees and penalties",
            "Chunk_Embedding": [0.1] * 1536
        }
    ]
    file_path.write_text(json.dumps(data))
    return file_path


@pytest.fixture
def sample_diff(tmp_path):
    """Creates a sample unified diff file for parsing tests."""
    diff_path = tmp_path / "test_update.diff"
    content = (
        "--- old.txt\n"
        "+++ new.txt\n"
        "@@ -1,1 +1,1 @@\n"
        "-The fee is $100.\n"
        "+The Penalty is $250,000.\n"
    )
    diff_path.write_text(content)
    return diff_path


def test_parse_diff_hunks_logic(sample_diff):
    """Verifies diff parser using the correct 'added_lines' key."""
    hunks = tripwire.parse_diff_hunks(str(sample_diff))
    assert len(hunks) == 1
    assert any("Penalty" in line for line in hunks[0]['added_lines'])


def test_power_words_detection():
    """Verifies triggers using 'strong_count' and 'score' keys."""
    text = "A penalty of $250,000 is mandatory."
    results = tripwire.detect_power_words(text)
    assert results['strong_count'] > 0
    assert results['score'] > 0.10


def test_calculate_similarity_structure(mock_embeddings_file, sample_diff):
    """Tests the full analysis pipeline return keys (updated schema)."""
    with patch('tripwire._embed_texts') as mock_embed:
        mock_embed.return_value = np.array([[0.1] * 1536])

        with patch('tripwire.SEMANTIC_EMBEDDINGS_FILE', str(mock_embeddings_file)):
            result = tripwire.calculate_similarity(str(sample_diff), source_priority="High")

            assert result['status'] == 'success'
            assert 'page_final_score' in result
            assert 'candidate_count' in result

            # New: change_hunks should carry structured fields for packet output
            assert 'change_hunks' in result
            assert isinstance(result['change_hunks'], list)
            assert 'removed' in result['change_hunks'][0]
            assert 'added' in result['change_hunks'][0]


def test_generate_handover_packets_data_integrity(tmp_path):
    """Tests packet generation matches revised packet schema (template-based)."""
    temp_handover = tmp_path / "handover"
    temp_handover.mkdir()

    with patch('tripwire.HANDOVER_DIR', str(temp_handover)):
        analysis = {
            "status": "success",
            "page_final_score": 0.75,
            "page_base_similarity": 0.70,
            "primary_udid": "udid-1",
            "primary_chunk_id": "U1_C1",
            "candidate_min_score": tripwire.CANDIDATE_MIN_SCORE,
            "handover_decision_reason": "High priority source: handover triggered when threshold-passing candidates exist",
            "power_words": {"found": [], "power_words_found": [], "count": 0},
            "change_hunks": [
                {
                    "hunk_index": 1,
                    "hunk_header": "@@ -1,1 +1,1 @@",
                    "removed": ["- The fee is $100."],
                    "added": ["+ The Penalty is $250,000."],
                    "hunk_text": "The fee is $100. The Penalty is $250,000.",
                    "is_noise": False,
                    "power_words_found": []
                }
            ],
            "threshold_passing_candidates": [
                {
                    "udid": "udid-1",
                    "candidate_rank": 1,
                    "page_final_score": 0.75,
                    "page_base_similarity": 0.70,
                    "relevant_chunk_ids": ["U1_C1"],
                    "best_chunk_id": "U1_C1",
                    "best_headline": "Fees and penalties",
                    "matched_hunk_indices": [1]
                }
            ]
        }

        paths = tripwire.generate_handover_packets(
            source_name="Test Source",
            priority="High",
            version_id="v1",
            diff_file="test.diff",
            analysis=analysis,
            timestamp="2026-03-04T09:15:12Z"
        )

        assert len(paths) == 1
        with open(paths[0], 'r', encoding='utf-8') as f:
            packet = json.load(f)

        # Revised schema keys
        assert "audit_summary" in packet
        assert "source_change_details" in packet
        assert "llm_verification_targets" in packet

        # Required additions
        assert "primary_candidate_explanation" in packet["audit_summary"]
        assert packet["audit_summary"]["primary_candidate_explanation"]["best_chunk_id"] == "U1_C1"

        targets = packet["llm_verification_targets"]
        assert len(targets) == 1
        assert targets[0]["udid"] == "udid-1"
        assert "page_final_score" in targets[0]
        assert "evidence_resolution" in targets[0]
        assert targets[0]["evidence_resolution"]["requires_resolution"] is True

        # Hunk cleaner should remove +/-
        hunks = packet["source_change_details"]["hunks"]
        assert hunks[0]["removed"][0].startswith("-") is False
        assert hunks[0]["added"][0].startswith("+") is False


def test_handover_batching_limit(tmp_path):
    """Ensures large candidate lists are split into multiple files (revised schema)."""
    temp_handover = tmp_path / "batches"
    temp_handover.mkdir()

    with patch('tripwire.HANDOVER_DIR', str(temp_handover)):
        with patch('tripwire.MAX_CANDIDATES_PER_PACKET', 2):
            analysis = {
                "status": "success",
                "page_final_score": 0.8,
                "page_base_similarity": 0.8,
                "primary_udid": "U0",
                "primary_chunk_id": "U0_C0",
                "candidate_min_score": tripwire.CANDIDATE_MIN_SCORE,
                "power_words": {"found": [], "power_words_found": [], "count": 0},
                "change_hunks": [],
                "handover_decision_reason": "High priority source: handover triggered when threshold-passing candidates exist",
                "threshold_passing_candidates": [
                    {
                        "udid": f"U{i}",
                        "candidate_rank": i + 1,
                        "page_final_score": 0.8,
                        "page_base_similarity": 0.8,
                        "best_chunk_id": f"U{i}_C1",
                        "relevant_chunk_ids": [f"U{i}_C1"],
                        "matched_hunk_indices": []
                    }
                    for i in range(5)
                ]
            }

            paths = tripwire.generate_handover_packets(
                source_name="Big Bill",
                priority="High",
                version_id="v1",
                diff_file="big.diff",
                analysis=analysis,
                timestamp="2026-03-04T09:15:12Z"
            )

            # 5 candidates / 2 per packet = 3 packets
            assert len(paths) == 3

            # Validate batching metadata in one packet
            with open(paths[0], 'r', encoding='utf-8') as f:
                packet0 = json.load(f)
            batching = packet0["audit_summary"]["batching"]
            assert batching["candidate_batch_count"] == 3
            assert batching["candidates_in_this_packet"] == 2


def test_resolve_ipfr_content_files_for_known_test_pages(tmp_path):
    """Validates UDID->file resolution using self-contained ipfr_content_archive fixtures."""
    archive = tmp_path / "ipfr_content_archive"
    archive.mkdir()

    # Create minimal files matching Tripwire's prototype filename patterns.
    (archive / "101-1 - How to avoid infringing others' intellectual property.md").write_text(
        "<!-- section_id: s-101-1 -->\n# Title\nText", encoding="utf-8"
    )
    (archive / "101-1_how-to-avoid-infringing-others-intellectual-property.json").write_text(
        "{\"@type\":\"WebPage\"}", encoding="utf-8"
    )

    (archive / "101-2 - Design infringement.md").write_text(
        "<!-- section_id: s-101-2 -->\n# Title\nText", encoding="utf-8"
    )
    (archive / "101-2_design-infringement.json").write_text(
        "{\"@type\":\"WebPage\"}", encoding="utf-8"
    )

    with patch("tripwire.IPFR_CONTENT_ARCHIVE_DIR", str(archive)):
        for udid in ["101-1", "101-2"]:
            resolved = tripwire.resolve_ipfr_content_files(udid)
            assert resolved["markdown_path"] is not None
            assert resolved["jsonld_path"] is not None
            assert Path(resolved["markdown_path"]).exists()
            assert Path(resolved["jsonld_path"]).exists()


def test_build_llm_verification_prompt_returns_two_pass_summary():
    """Ensures the compatibility wrapper still identifies the packet and candidates."""
    packet = {
        "packet_id": "handover_TEST_101-1_batch_01_of_01",
        "source_change_details": {
            "source": {"name": "Test", "monitoring_priority": "High"},
            "diff_file": "x.diff",
            "version_id": "v1",
            "hunks": [{"hunk_id": 1, "location_header": "@@ -1 +1 @@", "removed": ["old"], "added": ["new"]}]
        }
    }

    candidates_with_content = [{"udid": "101-1"}]

    prompt = tripwire._build_llm_verification_prompt(packet, candidates_with_content)
    assert "two-pass verification" in prompt
    assert packet["packet_id"] in prompt


def test_audit_log_headers_are_upgraded_and_overlap_metrics_written(tmp_path):
    """Ensures audit_log.csv headers are upgraded and overlap/precision/recall can be written."""
    audit_path = tmp_path / "audit_log.csv"

    # Simulate an older audit log schema
    old_headers = [
        'Timestamp', 'Source_Name', 'Priority', 'Status', 'Change_Detected',
        'Version_ID', 'Diff_File', 'Similarity_Score', 'Power_Words',
        'Matched_UDID', 'Matched_Chunk_ID', 'Outcome', 'Reason'
    ]
    with open(audit_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=old_headers)
        w.writeheader()
        w.writerow({
            "Timestamp": "2026-03-04T00:00:00",
            "Source_Name": "S",
            "Priority": "High",
            "Status": "Success",
            "Change_Detected": "Yes",
            "Version_ID": "v1",
            "Diff_File": "d.diff",
            "Similarity_Score": "0.8",
            "Power_Words": "",
            "Matched_UDID": "",
            "Matched_Chunk_ID": "",
            "Outcome": "handover",
            "Reason": "x"
        })

    with patch("tripwire.AUDIT_LOG", str(audit_path)):
        tripwire.ensure_audit_log_headers()

        # Verify new headers exist
        with open(audit_path, "r", encoding="utf-8") as f:
            headers = next(csv.reader(f))
        assert "AI Verification Run" in headers
        assert "Human Review Needed" in headers
        assert "AI vs Similarity Precision" in headers
        assert "AI vs Similarity Recall" in headers

        # Write overlap metrics by updating the row
        metrics = tripwire._compute_overlap_metrics(["101-1", "101-2"], ["101-2"])
        updated = tripwire.update_audit_row_by_key(
            source_name="S",
            version_id="v1",
            diff_file="d.diff",
            updates={
                "AI Verification Run": "Yes",
                "AI Decision": "Impact Confirmed",
                "AI Verified Impact Pages": ";".join(metrics["ver_set"]),
                "AI vs Similarity Overlap Score": f"{metrics['overlap']:.3f}",
                "AI vs Similarity Precision": f"{metrics['precision']:.3f}",
                "AI vs Similarity Recall": f"{metrics['recall']:.3f}",
            }
        )
        assert updated is True

        # Confirm values were written
        with open(audit_path, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["AI Verification Run"] == "Yes"
        assert rows[0]["AI vs Similarity Overlap Score"] == "0.500"
        assert rows[0]["AI vs Similarity Precision"] == "0.500"
        assert rows[0]["AI vs Similarity Recall"] == "1.000"

def test_end_to_end_llm_verification_pipeline(tmp_path):
    """
    Simulates a full Tripwire run:
    diff → similarity → handover → LLM verification → audit log update
    """

    # temp directories
    handover_dir = tmp_path / "handover_packets"
    verify_dir = tmp_path / "llm_verification_results"
    archive_dir = tmp_path / "ipfr_content_archive"

    handover_dir.mkdir()
    verify_dir.mkdir()
    archive_dir.mkdir()

    # --- create minimal IPFR test pages ---
    (archive_dir / "101-2 - Design infringement_test.md").write_text(
        "<!-- section_id: design-infringement-example -->\n"
        "# Design infringement\n"
        "Design infringement occurs when someone copies a registered design.",
        encoding="utf-8"
    )

    (archive_dir / "101-2_design-infringement_test.json").write_text(
        '{"@type":"WebPage","name":"Design infringement"}',
        encoding="utf-8"
    )

    # --- create semantic embedding fixture ---
    embeddings_file = tmp_path / "Semantic_Embeddings_Output.json"
    embeddings_file.write_text(json.dumps([
        {
            "UDID": "101-2",
            "Chunk_ID": "101-2-C01",
            "Chunk_Text": "Design infringement occurs when someone copies a registered design.",
            "Headline_Alt": "Design infringement",
            "Chunk_Embedding": [0.1] * 1536
        }
    ]))

    # --- create diff that should match the page ---
    diff_file = tmp_path / "design_update.diff"
    diff_file.write_text(
        "--- old\n"
        "+++ new\n"
        "@@ -1 +1 @@\n"
        "-Design infringement occurs when a design is copied.\n"
        "+Design infringement occurs when someone copies a registered design.\n"
    )

    # --- temp audit log ---
    audit_log = tmp_path / "audit_log.csv"

    with open(audit_log, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "Timestamp","Source_Name","Priority","Status","Change_Detected",
            "Version_ID","Diff_File","Similarity_Score","Power_Words",
            "Matched_UDID","Matched_Chunk_ID","Outcome","Reason"
        ])
        writer.writeheader()
        writer.writerow({
            "Timestamp":"2026-03-04",
            "Source_Name":"TestSource",
            "Priority":"High",
            "Status":"Success",
            "Change_Detected":"Yes",
            "Version_ID":"v1",
            "Diff_File":str(diff_file),
            "Similarity_Score":"",
            "Power_Words":"",
            "Matched_UDID":"",
            "Matched_Chunk_ID":"",
            "Outcome":"",
            "Reason":""
        })

    # --- mock embeddings ---
    with patch("tripwire._embed_texts") as mock_embed:
        mock_embed.return_value = np.array([[0.1]*1536])

        # --- mock LLM response ---
        with patch("tripwire._call_llm_json") as mock_llm:

            def fake_llm(prompt: str):
                if '"decision": "impact|no_impact|uncertain"' in prompt:
                    return {
                        "decision": "impact",
                        "udid": "101-2",
                        "chunk_id": "101-2-C01",
                        "confidence": "high",
                        "reason": "Source change modifies infringement definition.",
                        "evidence_quote": "copies a registered design"
                    }
                return {
                    "confirmed_update_chunk_ids": ["101-2-C01"],
                    "rejected_stage3_chunk_ids": [],
                    "additional_chunks_to_review": [],
                    "notes": ""
                }

            mock_llm.side_effect = fake_llm

            with patch("tripwire.AUDIT_LOG", str(audit_log)), \
                 patch("tripwire.HANDOVER_DIR", str(handover_dir)), \
                 patch("tripwire.LLM_VERIFY_DIR", str(verify_dir)), \
                 patch("tripwire.SEMANTIC_EMBEDDINGS_FILE", str(embeddings_file)), \
                 patch("tripwire.IPFR_CONTENT_ARCHIVE_DIR", str(archive_dir)):

                tripwire._semantic_cache = None

                analysis = tripwire.calculate_similarity(str(diff_file), "High")

                packets = tripwire.generate_handover_packets(
                    source_name="TestSource",
                    priority="High",
                    version_id="v1",
                    diff_file=str(diff_file),
                    analysis=analysis,
                    timestamp="2026-03-04T10:00:00Z"
                )

                assert len(packets) == 1

                tripwire.run_llm_verification_for_packets(packets)

    # --- verify audit log updated ---
    with open(audit_log, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    row = rows[0]

    assert row["AI Verification Run"] == "Yes"
    assert row["AI Decision"] == "Impact Confirmed"
    assert "101-2" in row["AI Verified Impact Pages"]


def test_eval_generated_diff_is_materially_different():
    removed = (
        "Design infringement can occur when someone uses a design that is identical or "
        "similar to a registered design without obtaining permission from the owner."
    )
    added = evaluate_tripwire_llm._build_materially_different_added_line(removed)

    assert added != removed
    assert "only when" in added.lower()
    assert "identical to the registered design" in added.lower()


def test_extract_chunk_window_returns_exact_markdown_chunk_only():
    markdown = """
<!-- chunk_id: 101-2_01 -->
### What is design infringement?

Exact target chunk text.

<!-- chunk_id: 101-2_02 -->
### What do registered designs protect?

This should not be included.
""".strip()

    chunks = tripwire.parse_markdown_chunks(markdown)
    window = tripwire.extract_chunk_window(chunks, "101-2_01")

    assert window["before"] == ""
    assert window["after"] == ""
    assert "Exact target chunk text." in window["current"]
    assert "This should not be included." not in window["current"]


def test_verify_handover_packet_impact_takes_precedence_over_missing_candidates(tmp_path):
    handover_dir = tmp_path / "handover"
    verify_dir = tmp_path / "verify"
    archive_dir = tmp_path / "archive"

    handover_dir.mkdir()
    verify_dir.mkdir()
    archive_dir.mkdir()

    # Existing page for the first candidate
    (archive_dir / "101-2 - Design infringement_test.md").write_text(
        """
<!-- chunk_id: 101-2_01 -->
### What is design infringement?

Design infringement can occur when someone uses a design that is identical or similar to a registered design.
""".strip(),
        encoding="utf-8"
    )
    (archive_dir / "101-2_design-infringement_test.json").write_text(
        '{"@type":"WebPage","name":"Design infringement"}',
        encoding="utf-8"
    )

    packet = {
        "packet_id": "handover_eval_101-2_batch_01_of_01",
        "source_change_details": {
            "source": {"name": "TestSource", "monitoring_priority": "High"},
            "diff_file": "design_update.diff",
            "version_id": "v1",
            "hunks": [
                {
                    "hunk_id": 1,
                    "location_header": "@@ -1 +1 @@",
                    "removed": ["Design infringement can occur when someone uses a design that is identical or similar to a registered design."],
                    "added": ["Design infringement can occur only when someone uses a design that is identical to the registered design."]
                }
            ]
        },
        "llm_verification_targets": [
            {
                "candidate_rank": 1,
                "udid": "101-2",
                "page_final_score": 0.9,
                "best_chunk_id": "101-2_01",
                "matched_hunk_indices": [1],
                "relevant_chunk_ids": ["101-2_01"]
            },
            {
                "candidate_rank": 2,
                "udid": "999-9",
                "page_final_score": 0.7,
                "best_chunk_id": "999-9_01",
                "matched_hunk_indices": [1],
                "relevant_chunk_ids": ["999-9_01"]
            }
        ]
    }

    packet_path = handover_dir / "packet.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")

    def fake_llm(prompt: str):
        if '"decision": "impact|no_impact|uncertain"' in prompt:
            return {
                "decision": "impact",
                "udid": "101-2",
                "chunk_id": "101-2_01",
                "confidence": "high",
                "reason": "Source change modifies the legal threshold in the matching chunk.",
                "evidence_quote": "identical or similar to a registered design"
            }
        return {
            "confirmed_update_chunk_ids": ["101-2_01"],
            "rejected_stage3_chunk_ids": [],
            "additional_chunks_to_review": [],
            "notes": ""
        }

    with patch("tripwire.IPFR_CONTENT_ARCHIVE_DIR", str(archive_dir)), \
         patch("tripwire.LLM_VERIFY_DIR", str(verify_dir)), \
         patch("tripwire._call_llm_json", side_effect=fake_llm):

        result_path = tripwire.verify_handover_packet_with_llm(
            str(packet_path),
            prefer_test_files=True
        )

    doc = json.loads(Path(result_path).read_text(encoding="utf-8"))
    assert doc["llm_result"]["overall_decision"] == "impact"
    assert doc["llm_result"]["impacted_pages"][0]["udid"] == "101-2"
