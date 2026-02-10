"""
Test suite for Tripwire Stage 3: Semantic Analysis & Relevance Gate

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
        
        assert 'authorisation' in result['removed']  # Old text
        assert '$150,000' in result['added']  # New text
        assert len(result['change_context']) > 0
    
    def test_empty_diff(self):
        """Should handle diffs with no content gracefully"""
        result = extract_change_content('test_fixtures/diffs/noise_only.diff')
        
        # Should still extract something even if minimal
        assert 'change_context' in result


class TestPowerWordDetection:
    """Test power word scanning and scoring"""
    
    def test_detect_multiple_power_words(self):
        """Should find multiple power words in text"""
        text = "Applicants must submit within 30 days. Penalties may include $5,000 fine."
        result = detect_power_words(text)
        
        assert result['count'] >= 4  # must, days, penalties, fine, $5,000
        assert 'must' in result['found']
        assert result['score'] > 0
    
    def test_detect_shall_and_may(self):
        """Should detect legal modal verbs"""
        text = "You shall comply. You may appeal. This is mandatory."
        result = detect_power_words(text)
        
        assert 'shall' in result['found']
        assert 'may' in result['found']
        assert 'mandatory' in result['found']
    
    def test_detect_dollar_amounts(self):
        """Should detect dollar amounts"""
        text = "Penalties up to $150,000 for violations"
        result = detect_power_words(text)
        
        assert any('$' in word for word in result['found'])
    
    def test_detect_time_periods(self):
        """Should detect day-based deadlines"""
        text = "Submit within 30 days or 60 days maximum"
        result = detect_power_words(text)
        
        assert any('day' in word for word in result['found'])
    
    def test_detect_archives_act(self):
        """Should detect specific act references"""
        text = "Under the Archives Act 1983, records must be preserved"
        result = detect_power_words(text)
        
        assert any('Archives Act' in word for word in result['found'])
    
    def test_no_power_words(self):
        """Should return zero score for text without power words"""
        text = "The weather is nice today. Sunny skies expected."
        result = detect_power_words(text)
        
        assert result['count'] == 0
        assert result['score'] == 0.0
    
    def test_power_word_score_capped(self):
        """Power word score should be capped at 1.0"""
        text = " must shall may penalty fine prohibited mandatory required " * 10
        result = detect_power_words(text)
        
        assert result['score'] <= 1.0


class TestScoringLogic:
    """Test final score calculation and threshold logic"""
    
    def test_calculate_final_score_weighting(self):
        """Should weight semantic 75% and power words 25%"""
        base_sim = 0.8
        power_score = 0.4
        
        final = calculate_final_score(base_sim, power_score)
        
        # (0.8 * 0.75) + (0.4 * 0.25) = 0.6 + 0.1 = 0.7
        assert abs(final - 0.7) < 0.01
    
    def test_high_semantic_low_power(self):
        """High semantic similarity should pass even without power words"""
        final = calculate_final_score(0.9, 0.0)
        
        assert final >= 0.67  # 0.9 * 0.75 = 0.675
        assert should_generate_handover(final, threshold=0.60)
    
    def test_low_semantic_high_power(self):
        """Power words should boost marginal matches"""
        final_without = calculate_final_score(0.60, 0.0)
        final_with = calculate_final_score(0.60, 1.0)
        
        assert final_with > final_without
        assert (final_with - final_without) == 0.25  # Exactly 25% boost
    
    def test_threshold_logic(self):
        """Should correctly apply threshold"""
        assert should_generate_handover(0.75, threshold=0.70) == True
        assert should_generate_handover(0.65, threshold=0.70) == False
        assert should_generate_handover(0.70, threshold=0.70) == True  # Equal passes


class TestEndToEnd:
    """Test complete workflow with mock data"""
    
    def test_high_relevance_trademark_match(self, mock_semantic_data):
        """Should match trademark infringement diff to IPFR-001"""
        result = calculate_similarity(
            'test_fixtures/diffs/high_relevance_trademark.diff',
            mock_semantic_data=mock_semantic_data
        )
        
        assert result['status'] == 'success'
        assert result['matched_udid'] == 'IPFR-001'
        assert result['base_similarity'] > 0.60  # Should be quite similar
        assert result['should_handover'] == True
        
        # Should detect power words
        assert result['power_words']['count'] > 0
        assert '$150,000' in str(result['power_words']['found'])
    
    def test_power_words_boost_marginal_match(self, mock_semantic_data):
        """Power words should boost a marginal semantic match"""
        result = calculate_similarity(
            'test_fixtures/diffs/power_words_heavy.diff',
            mock_semantic_data=mock_semantic_data
        )
        
        assert result['status'] == 'success'
        
        # Even if base similarity is lower, power words should boost score
        assert result['power_words']['count'] >= 4
        assert result['final_score'] > result['base_similarity']
    
    def test_noise_only_filtered(self, mock_semantic_data):
        """Timestamp-only changes should not trigger handover"""
        result = calculate_similarity(
            'test_fixtures/diffs/noise_only.diff',
            mock_semantic_data=mock_semantic_data
        )
        
        assert result['status'] == 'success'
        
        # Minimal content should have low similarity
        assert result['base_similarity'] < 0.50
        assert result['should_handover'] == False
        assert result['filter_reason'] is not None
    
    def test_unrelated_content_filtered(self, mock_semantic_data):
        """Sports/weather news should not match IP content"""
        result = calculate_similarity(
            'test_fixtures/diffs/unrelated_content.diff',
            mock_semantic_data=mock_semantic_data
        )
        
        assert result['status'] == 'success'
        
        # Should have very low similarity to IP content
        assert result['base_similarity'] < 0.40
        assert result['should_handover'] == False


class TestErrorHandling:
    """Test error handling and edge cases"""
    
    def test_missing_diff_file(self, mock_semantic_data):
        """Should handle missing diff file gracefully"""
        with pytest.raises(Exception):
            extract_change_content('nonexistent.diff')
    
    def test_no_content_in_diff(self):
        """Should handle empty diff content"""
        # Create a minimal diff with no actual changes
        result = extract_change_content('test_fixtures/diffs/noise_only.diff')
        
        # Should not crash, should return empty strings
        assert isinstance(result, dict)
        assert 'change_context' in result


if __name__ == '__main__':
    # Run tests with verbose output
    pytest.main([__file__, '-v', '--tb=short'])
