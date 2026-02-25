import os
import sys
import json
import pytest
import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

# --- 1. IMPORT LOGIC ---
# Dynamically find the tripwire module regardless of its specific filename
import tripwire as tripwire

# --- 2. FIXTURES ---

@pytest.fixture
def mock_embeddings_file(tmp_path):
    """Creates a dummy semantic embeddings file for testing."""
    file_path = tmp_path / "Semantic_Embeddings_Output.json"
    data = {
        "udid-1": {
            "metadata": {"title": "Test Page"},
            "chunks": [
                {"chunk_id": "U1_C1", "text": "Penalty is $100.", "embedding": [0.1] * 1536},
                {"chunk_id": "U1_C2", "text": "Fees apply.", "embedding": [0.2] * 1536}
            ]
        }
    }
    file_path.write_text(json.dumps(data))
    return file_path

@pytest.fixture
def sample_diff(tmp_path):
    """Creates a sample unified diff file."""
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
    """Verifies that the diff parser correctly extracts change context."""
    hunks = tripwire.parse_diff_hunks(str(sample_diff))
    assert len(hunks) == 1
    assert "Penalty" in hunks[0]['added']
    assert hunks[0]['hunk_index'] == 1

def test_power_words_detection():
    """Verifies that legal triggers are correctly identified."""
    text = "A penalty of $250,000 is mandatory under the Archives Act 1983."
    results = tripwire.detect_power_words(text)
    assert results['has_strong_trigger'] is True
    assert 'penalty' in results['found']
    assert 'dollar_amount' in results['found']

def test_calculate_final_score_gating():
    """Tests the Phase C Weighted Assessment (Strength vs Noise)."""
    # Case 1: Weak signal + Low Similarity = No Boost
    weak_power = {'weak_only': True, 'has_strong_trigger': False, 'score': 0.05}
    assert tripwire.calculate_final_score(0.10, weak_power) == 0.10
    
    # Case 2: Strong trigger = Boost allowed even at low base
    strong_power = {'weak_only': False, 'has_strong_trigger': True, 'score': 0.25}
    assert tripwire.calculate_final_score(0.10, strong_power) > 0.10

@patch('tripwire.client.embeddings.create')
def test_calculate_similarity_structure(mock_openai, mock_embeddings_file, sample_diff):
    """Tests the full analysis pipeline return structure."""
    # Mock OpenAI response
    mock_openai.return_value.data = [MagicMock(embedding=[0.1]*1536)]
    
    with patch('tripwire.SEMANTIC_EMBEDDINGS_FILE', str(mock_embeddings_file)):
        result = tripwire.calculate_similarity(str(sample_diff), source_priority="High")
        
        assert result['status'] == 'success'
        assert 'final_score' in result
        assert 'threshold_passing_candidates' in result
        # Check for the multi-impact flags
        assert 'impact_count' in result
        assert 'multi_impact_likely' in result

def test_generate_handover_packets_data_integrity(tmp_path):
    """Ensures the JSON packet contains the Chunk IDs and Text Tom needs."""
    tripwire.HANDOVER_DIR = str(tmp_path / "handover")
    
    analysis = {
        "final_score": 0.75,
        "matched_udid": "udid-1",
        "threshold_passing_candidates": [
            {
                "udid": "udid-1",
                "rank": 1,
                "aggregated_final_score": 0.75,
                "supporting_chunks": [{"chunk_id": "U1_C1"}, {"chunk_id": "U1_C2"}],
                "best_chunk": {"chunk_id": "U1_C1", "text": "Existing fee text", "headline_alt": "Fees"}
            }
        ]
    }
    
    paths = tripwire.generate_handover_packets(
        "Trade Marks Act", "High", "tm.diff", analysis, datetime.datetime.now().isoformat()
    )
    
    assert len(paths) == 1
    with open(paths[0], 'r') as f:
        packet = json.load(f)
        candidate = packet['llm_handover']['candidates'][0]
        
        # Validation for Tom's Pipeline
        assert candidate['udid'] == "udid-1"
        assert "U1_C1" in candidate['relevant_chunk_ids']
        # If you added the 'best_chunk_text' fix:
        if 'best_chunk_text' in candidate:
            assert candidate['best_chunk_text'] == "Existing fee text"

def test_noise_suppression_low_similarity(tmp_path, mock_embeddings_file):
    """
    ISSUE 1: Adversarial Noise.
    Ensures that administrative changes (page numbers, etc.) do not 
    hallucinate impacts even if they appear in high-priority sources.
    """
    # Create a 'junk' diff that is purely administrative
    noise_diff = tmp_path / "administrative_noise.diff"
    noise_diff.write_text("--- a\n+++ b\n@@ -1,1 +1,1 @@\n-Page 54 of 102\n+Page 55 of 103\n")
    
    # Mock OpenAI to return a very low similarity embedding
    with patch('tripwire.client.embeddings.create') as mock_emb:
        mock_emb.return_value.data = [MagicMock(embedding=[0.01]*1536)]
        
        with patch('tripwire.SEMANTIC_EMBEDDINGS_FILE', str(mock_embeddings_file)):
            result = tripwire.calculate_similarity(str(noise_diff), source_priority="High")
            
            # Even for 'High' priority, if score is near zero and no power words exist,
            # it should not suggest a massive impact.
            assert result['final_score'] < 0.20
            assert result['multi_impact_likely'] is False

def test_handover_batching_limit_logic(tmp_path):
    """
    ISSUE 2: Context Window / Batching.
    Ensures that if 50 pages are impacted, they are split into multiple 
    packets to prevent 'Lost-in-the-Middle' syndrome for Tom's LLM.
    """
    # Force a small batch size for the test
    with patch('tripwire.MAX_CANDIDATES_PER_PACKET', 2):
        tripwire.HANDOVER_DIR = str(tmp_path / "batches")
        
        # Mock analysis with 5 candidates
        analysis = {
            "final_score": 0.8,
            "threshold_passing_candidates": [{"udid": f"U{i}", "rank": i} for i in range(5)],
            "change_text": "Massive Legislative Overhaul"
        }
        
        paths = tripwire.generate_handover_packets(
            "Omnibus Bill", "High", "bill.diff", analysis, "2026-02-25T12:00:00"
        )
        
        # With 5 candidates and batch size of 2, we expect 3 files (2+2+1)
        assert len(paths) == 3
        assert "batch_01_of_03.json" in paths[0]
        assert "batch_03_of_03.json" in paths[2]
