import os
import sys
import json
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

@patch('tripwire.OpenAI')
def test_calculate_similarity_structure(mock_openai_class, mock_embeddings_file, sample_diff):
    """Tests the full analysis pipeline return keys."""
    # Setup Mock OpenAI
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_client.embeddings.create.return_value.data = [MagicMock(embedding=[0.1]*1536)]
    
    with patch('tripwire.SEMANTIC_EMBEDDINGS_FILE', str(mock_embeddings_file)):
        # Passing mock_semantic_data=None to force file loading from our temp file
        result = tripwire.calculate_similarity(str(sample_diff), source_priority="High")
        
        assert result['status'] == 'success'
        assert 'final_score' in result
        assert 'impact_count' in result

def test_generate_handover_packets_data_integrity(tmp_path):
    """Tests packet generation with the 'status': 'success' requirement."""
    temp_handover = tmp_path / "handover"
    temp_handover.mkdir()
    
    with patch('tripwire.HANDOVER_DIR', str(temp_handover)):
        analysis = {
            "status": "success", # Requirement for function to run
            "final_score": 0.75,
            "primary_udid": "udid-1",
            "threshold_passing_candidates": [
                {
                    "udid": "udid-1",
                    "candidate_rank": 1,
                    "aggregated_final_score": 0.75,
                    "relevant_chunk_ids": ["U1_C1"]
                }
            ]
        }
        
        paths = tripwire.generate_handover_packets(
            "Test Source", "High", "test.diff", analysis, "20260225120000"
        )
        
        assert len(paths) > 0
        with open(paths[0], 'r') as f:
            packet = json.load(f)
            assert packet['llm_handover']['candidates'][0]['udid'] == "udid-1"

def test_noise_suppression_logic(tmp_path, mock_embeddings_file):
    """Verifies that non-substantive changes result in low scores."""
    noise_diff = tmp_path / "noise.diff"
    noise_diff.write_text("@@ -1,1 +1,1 @@\n-Page 1\n+Page 2\n")
    
    with patch('tripwire._embed_texts') as mock_embed:
        # Mock a very low similarity vector
        mock_embed.return_value = [[0.9] * 1536] 
        with patch('tripwire.SEMANTIC_EMBEDDINGS_FILE', str(mock_embeddings_file)):
            # Force low base similarity via mock
            result = tripwire.calculate_similarity(str(noise_diff), source_priority="Low")
            # Final score should be low if no power words match administrative text
            assert result['final_score'] < 0.45 

def test_handover_batching_limit(tmp_path):
    """Ensures large candidate lists are split into multiple files."""
    temp_handover = tmp_path / "batches"
    temp_handover.mkdir()
    
    with patch('tripwire.HANDOVER_DIR', str(temp_handover)):
        with patch('tripwire.MAX_CANDIDATES_PER_PACKET', 2):
            analysis = {
                "status": "success",
                "final_score": 0.8,
                "threshold_passing_candidates": [{"udid": f"U{i}", "rank": i} for i in range(5)]
            }
            
            paths = tripwire.generate_handover_packets(
                "Big Bill", "High", "big.diff", analysis, "20260225120000"
            )
            # 5 candidates / 2 per packet = 3 packets
            assert len(paths) == 3
