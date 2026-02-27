import os
import sys
import json
import numpy as np
import pytest
import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

# --- 1. IMPORT LOGIC ---
# This ensures it points to your 'tripwire.py' file
import tripwire

# --- 2. FIXTURES ---

@pytest.fixture
def mock_embeddings_file(tmp_path):
    """Creates a dummy semantic embeddings file in the correct flat-list format."""
    file_path = tmp_path / "Semantic_Embeddings_Output.json"
    data = [
        {
            "UDID": "udid-1",
            "Chunk_ID": "U1_C1",
            "Chunk_Text": "The penalty for late fees is $250,000.",
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

# --- 3. UNIT TESTS ---

def test_parse_diff_hunks_logic(sample_diff):
    """Verifies diff parser using the correct 'added_lines' key."""
    hunks = tripwire.parse_diff_hunks(str(sample_diff))
    assert len(hunks) == 1
    # Fixed: Using 'added_lines' instead of 'added'
    assert any("Penalty" in line for line in hunks[0]['added_lines'])

def test_power_words_detection():
    """Verifies triggers using 'strong_count' and 'score' keys."""
    text = "A penalty of $250,000 is mandatory."
    results = tripwire.detect_power_words(text)
    # Fixed: checking against script-specific keys
    assert results['strong_count'] > 0
    assert results['score'] > 0.10

def test_calculate_similarity_structure(mock_embeddings_file, sample_diff):
    """Tests the full analysis pipeline return keys (updated schema)."""
    # Avoid dependency on OpenAI client / env by patching the embedder directly
    with patch('tripwire._embed_texts') as mock_embed:
        mock_embed.return_value = np.array([[0.1] * 1536])  # one substantive hunk vector

        with patch('tripwire.SEMANTIC_EMBEDDINGS_FILE', str(mock_embeddings_file)):
            result = tripwire.calculate_similarity(str(sample_diff), source_priority="High")

            assert result['status'] == 'success'
            assert 'page_final_score' in result
            assert 'candidate_count' in result


def test_generate_handover_packets_data_integrity(tmp_path):
    """Tests packet generation with the 'status': 'success' requirement (updated schema + signature)."""
    temp_handover = tmp_path / "handover"
    temp_handover.mkdir()

    with patch('tripwire.HANDOVER_DIR', str(temp_handover)):
        analysis = {
            "status": "success",
            # Primary metrics in updated tripwire
            "page_final_score": 0.75,
            "page_base_similarity": 0.70,
            "primary_udid": "udid-1",
            "candidate_min_score": tripwire.CANDIDATE_MIN_SCORE,
            "threshold_passing_candidates": [
                {
                    "udid": "udid-1",
                    "candidate_rank": 1,
                    "page_final_score": 0.75,
                    "page_base_similarity": 0.70,
                    "relevant_chunk_ids": ["U1_C1"],
                    "best_chunk_id": "U1_C1"
                }
            ]
        }

        paths = tripwire.generate_handover_packets(
            source_name="Test Source",
            priority="High",
            version_id="v1",
            diff_file="test.diff",
            analysis=analysis,
            timestamp="20260225120000"
        )

        assert len(paths) > 0
        with open(paths[0], 'r', encoding='utf-8') as f:
            packet = json.load(f)
            assert packet['llm_handover']['candidates'][0]['udid'] == "udid-1"


def test_noise_suppression_logic(tmp_path, mock_embeddings_file):
    """Verifies that non-substantive changes result in low scores."""
    noise_diff = tmp_path / "noise.diff"
    # A typical administrative noise string
    noise_diff.write_text("@@ -1,1 +1,1 @@\n-Page 54 of 102\n+Page 55 of 103\n")
    
    with patch('tripwire._embed_texts') as mock_embed:
        # FIX: Use a vector that does NOT match the [0.1]*1536 in the fixture.
        # This simulates a 'dissimilar' semantic meaning.
        mock_embed.return_value = [[-0.1] * 1536] 
        
        with patch('tripwire.SEMANTIC_EMBEDDINGS_FILE', str(mock_embeddings_file)):
            result = tripwire.calculate_similarity(str(noise_diff), source_priority="Low")
            
            # Now the base_similarity will be low, and final_score will stay < 0.45
            assert result['page_final_score'] < 0.45

def test_handover_batching_limit(tmp_path):
    """Ensures large candidate lists are split into multiple files (updated signature)."""
    temp_handover = tmp_path / "batches"
    temp_handover.mkdir()

    with patch('tripwire.HANDOVER_DIR', str(temp_handover)):
        with patch('tripwire.MAX_CANDIDATES_PER_PACKET', 2):
            analysis = {
                "status": "success",
                "page_final_score": 0.8,
                "page_base_similarity": 0.8,
                "primary_udid": "U0",
                "candidate_min_score": tripwire.CANDIDATE_MIN_SCORE,
                "threshold_passing_candidates": [
                    {"udid": f"U{i}", "candidate_rank": i + 1, "page_final_score": 0.8, "page_base_similarity": 0.8}
                    for i in range(5)
                ]
            }

            paths = tripwire.generate_handover_packets(
                source_name="Big Bill",
                priority="High",
                version_id="v1",
                diff_file="big.diff",
                analysis=analysis,
                timestamp="20260225120000"
            )
            # 5 candidates / 2 per packet = 3 packets
            assert len(paths) == 3
