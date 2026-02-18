"""
Test suite for Tripwire Stage 3: Semantic Analysis & Relevance Gate
Consolidated version with OpenAI thresholds and error handling.

Run with: pytest test_stage3.py -v
"""

import pytest
import sys
import os
import pickle
import numpy as np

# Add parent directory to path to import tripwire functions
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tripwire import (
    extract_change_content,
    detect_power_words,
    calculate_final_score,
    should_generate_handover,
    calculate_similarity
)

# Load mock semantic data
MOCK_DATA_PATH = 'test_fixtures/mock_semantic_data.pkl'

# --- NEW THRESHOLD CONSTANTS ---
# text-embedding-3-small typically requires lower thresholds than E5
V3_SMALL_THRESHOLD = 0.45

@pytest.fixture
def mock_semantic_data():
    """Load pre-generated mock semantic embeddings"""
    if not os.path.exists(MOCK_DATA_PATH):
        pytest.skip(f"Mock data not found. Run: python generate_mock_data.py")
    
    with open(MOCK_DATA_PATH, 'rb') as f:
        data = pickle.load(f)
        # Verify dimension change: 768 -> 1536
        assert data['embeddings'].shape[1] == 1536, "Mock data must be regenerated for v3-small"
        return data

class TestDiffParsing:
    """Test diff file parsing and content extraction"""
    
    def test_extract_additions_and_removals(self):
        """Should correctly parse additions and removals from diff"""
        result = extract_change_content('test_fixtures/diffs/high_relevance_trademark.diff')
        assert 'authorisation' in result['removed']  
        assert '$150,000' in result['added']      
        assert len(result['change_context']) > 0

    def test_empty_diff(self):
        """Should handle diffs with no substantive changes gracefully"""
        result = extract_change_content('test_fixtures/diffs/noise_only.diff')
        assert 'change_context' in result

class TestPowerWordDetection:
    """Test power word scanning and scoring"""
    
    def test_detect_multiple_power_words(self):
        """Should find multiple power words in text"""
        text = "Applicants must submit within 30 days. Penalties may include $5,000 fine."
        result = detect_power_words(text)
        assert result['count'] >= 4
        assert 'must' in result['found']
        assert result['score'] > 0

    def test_detect_dollar_amounts(self):
        """Should detect dollar amounts with commas"""
        text = "Penalties up to $150,000 for violations"
        result = detect_power_words(text)
        assert any('$150,000' in word for word in result['found'])

    def test_detect_archives_act(self):
        """Should detect specific act references (case-insensitive)"""
        text = "Under the Archives Act 1983, records must be preserved"
        result = detect_power_words(text)
        # Matches against the lowercase output of detect_power_words
        assert any('archives act 1983' in word.lower() for word in result['found'])

class TestScoringLogic:
    """Test final score calculation and threshold logic using additive boost"""

    def test_calculate_final_score_additive(self):
        """Should add power word score directly to base similarity"""
        base_sim = 0.50
        power_score = 0.30
        final = calculate_final_score(base_sim, power_score)
        # 0.50 + 0.30 = 0.80
        assert abs(final - 0.80) < 0.001

    def test_boost_visible_in_score(self):
        """Boost should be the exact difference between scores with and without power words"""
        base_sim = 0.40
        boost = 0.15
        final_without = calculate_final_score(base_sim, 0.0)
        final_with = calculate_final_score(base_sim, boost)
        assert abs((final_with - final_without) - boost) < 0.001

    def test_high_semantic_low_power(self):
        """High semantic similarity should pass threshold without power words"""
        final = calculate_final_score(0.50, 0.0)
        assert final >= V3_SMALL_THRESHOLD
        assert should_generate_handover(final, threshold=V3_SMALL_THRESHOLD)

    def test_low_semantic_boost_crosses_threshold(self):
        """Power words should be able to push a marginal match over the threshold"""
        base_sim = 0.40  # Below 0.45 threshold on its own
        final = calculate_final_score(base_sim, 0.15)
        # 0.40 + 0.15 = 0.55 — clearly over threshold
        assert final >= V3_SMALL_THRESHOLD
        assert should_generate_handover(final, threshold=V3_SMALL_THRESHOLD) == True

    def test_score_capped_at_one(self):
        """Score should never exceed 1.0 regardless of inputs"""
        final = calculate_final_score(0.95, 0.90)
        assert final == 1.0

    def test_threshold_logic(self):
        """Should correctly apply threshold boundaries"""
        assert should_generate_handover(0.50, threshold=V3_SMALL_THRESHOLD) == True
        assert should_generate_handover(0.35, threshold=V3_SMALL_THRESHOLD) == False
        assert should_generate_handover(0.45, threshold=V3_SMALL_THRESHOLD) == True

class TestEndToEnd:
    """Test complete workflow with v3-small expectations"""
    
    def test_high_relevance_trademark_match(self, mock_semantic_data):
        """Should match trademark infringement diff to IPFR-001"""
        result = calculate_similarity(
            'test_fixtures/diffs/high_relevance_trademark.diff',
            mock_semantic_data=mock_semantic_data
        )
        assert result['status'] == 'success'
        assert result['matched_udid'] == 'IPFR-001' 
        assert result['base_similarity'] > 0.4
        assert result['should_handover'] == True

    def test_noise_only_filtered(self, mock_semantic_data):
        """Timestamp-only changes should not trigger handover"""
        result = calculate_similarity(
            'test_fixtures/diffs/noise_only.diff',
            mock_semantic_data=mock_semantic_data
        )
        # Noise usually drops significantly with v3-small
        assert result['base_similarity'] < 0.35 
        assert result['should_handover'] == False

class TestErrorHandling:
    """Test error handling and edge cases for Stage 3 robustness"""
    
    def test_missing_diff_file(self, mock_semantic_data):
        """Should raise FileNotFoundError when diff doesn't exist"""
        with pytest.raises(FileNotFoundError):
            extract_change_content('nonexistent_file.diff')

    def test_invalid_semantic_data(self):
        """Should handle malformed semantic data gracefully"""
        # Updated dimension to 1536 for v3-small
        result = calculate_similarity(
            'test_fixtures/diffs/high_relevance_trademark.diff',
            mock_semantic_data={'embeddings': np.zeros((1, 1536)), 'udids': ['ERR-01']}
        )
        assert 'status' in result

if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
