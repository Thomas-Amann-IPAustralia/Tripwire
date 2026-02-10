"""
Test suite for Tripwire Stage 3: Semantic Analysis & Relevance Gate
Consolidated version with E5-optimized thresholds and error handling.

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

@pytest.fixture
def mock_semantic_data():
    """Load pre-generated mock semantic embeddings"""
    if not os.path.exists(MOCK_DATA_PATH):
        pytest.skip(f"Mock data not found. Run: python generate_mock_data.py")
    
    with open(MOCK_DATA_PATH, 'rb') as f:
        return pickle.load(f)

class TestDiffParsing:
    """Test diff file parsing and content extraction"""
    
    def test_extract_additions_and_removals(self):
        """Should correctly parse additions and removals from diff"""
        result = extract_change_content('test_fixtures/diffs/high_relevance_trademark.diff')
        [cite_start]assert 'authorisation' in result['removed']  # Old text [cite: 33]
        [cite_start]assert '$150,000' in result['added']      # New text [cite: 35]
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
    """Test final score calculation and threshold logic using 90/10 weighting"""
    
    def test_calculate_final_score_weighting(self):
        """Should weight semantic 90% and power words 10%"""
        base_sim = 0.85
        power_score = 0.4
        final = calculate_final_score(base_sim, power_score)
        # (0.85 * 0.90) + (0.4 * 0.10) = 0.765 + 0.04 = 0.805
        assert abs(final - 0.805) < 0.001

    def test_high_semantic_low_power(self):
        """High semantic similarity should pass even without power words"""
        # 0.92 * 0.9 = 0.828 (Clears the 0.82 threshold)
        final = calculate_final_score(0.92, 0.0)
        assert final >= 0.82
        assert should_generate_handover(final, threshold=0.82)

    def test_low_semantic_high_power(self):
        """Power words should boost marginal matches by 10%"""
        final_without = calculate_final_score(0.60, 0.0)
        final_with = calculate_final_score(0.60, 1.0)
        assert final_with > final_without
        assert abs((final_with - final_without) - 0.10) < 0.001

    def test_threshold_logic(self):
        """Should correctly apply threshold boundaries"""
        assert should_generate_handover(0.85, threshold=0.82) == True
        assert should_generate_handover(0.75, threshold=0.82) == False
        assert should_generate_handover(0.82, threshold=0.82) == True

class TestEndToEnd:
    """Test complete workflow with E5-optimized expectations"""
    
    def test_high_relevance_trademark_match(self, mock_semantic_data):
        """Should match trademark infringement diff to IPFR-001"""
        result = calculate_similarity(
            'test_fixtures/diffs/high_relevance_trademark.diff',
            mock_semantic_data=mock_semantic_data
        )
        assert result['status'] == 'success'
        [cite_start]assert result['matched_udid'] == 'IPFR-001' # [cite: 33, 35]
        assert result['base_similarity'] > 0.85 
        assert result['should_handover'] == True

    def test_noise_only_filtered(self, mock_semantic_data):
        """Timestamp-only changes should not trigger handover"""
        result = calculate_similarity(
            'test_fixtures/diffs/noise_only.diff',
            mock_semantic_data=mock_semantic_data
        )
        [cite_start]assert result['base_similarity'] < 0.78 # [cite: 22]
        assert result['should_handover'] == False

class TestErrorHandling:
    """Test error handling and edge cases for Stage 3 robustness"""
    
    def test_missing_diff_file(self, mock_semantic_data):
        """Should raise FileNotFoundError when diff doesn't exist"""
        with pytest.raises(FileNotFoundError):
            extract_change_content('nonexistent_file.diff')

    def test_invalid_semantic_data(self):
        """Should handle malformed semantic data gracefully"""
        result = calculate_similarity(
            'test_fixtures/diffs/high_relevance_trademark.diff',
            mock_semantic_data={'embeddings': np.zeros((1, 768)), 'udids': ['ERR-01']}
        )
        # Should not crash, status should reflect attempt
        assert 'status' in result

if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
