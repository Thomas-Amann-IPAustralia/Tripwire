import os
import json
import csv
import numpy as np
import pytest
from pathlib import Path
from unittest.mock import patch

import tripwire


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


def test_build_llm_verification_prompt_includes_section_id_marker():
    """Ensures the prompt contains the section_id marker string for deterministic navigation."""
    packet = {
        "packet_id": "handover_TEST_101-1_batch_01_of_01",
        "source_change_details": {
            "source": {"name": "Test", "monitoring_priority": "High"},
            "diff_file": "x.diff",
            "version_id": "v1",
            "hunks": [{"hunk_id": 1, "location_header": "@@ -1 +1 @@", "removed": ["old"], "added": ["new"]}]
        },
        "llm_verification_targets": [
            {"candidate_rank": 1, "udid": "101-1", "page_final_score": 0.9, "best_chunk_id": "101-1-C01", "matched_hunk_indices": [1]}
        ]
    }

    candidates_with_content = [{
        "udid": "101-1",
        "candidate_rank": 1,
        "page_final_score": 0.9,
        "best_chunk_id": "101-1-C01",
        "matched_hunk_indices": [1],
        "resolved_files": {"udid": "101-1", "markdown_path": "ipfr_content_archive/101-1.md", "jsonld_path": "ipfr_content_archive/101-1.json", "missing": []},
        "markdown": "<!-- section_id: section-1-example -->\n### Example\nText",
        "jsonld": "{\"@type\":\"WebPage\"}"
    }]

    prompt = tripwire._build_llm_verification_prompt(packet, candidates_with_content)
    assert "section_id" in prompt
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
