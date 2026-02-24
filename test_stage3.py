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
import json
import tripwire as tripwire_module
import datetime
import traceback
from pathlib import Path

# Add parent directory to path to import tripwire functions
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tripwire import (
    extract_change_content,
    detect_power_words,
    calculate_final_score,
    should_generate_handover,
    calculate_similarity,
    get_diff,
    generate_handover_packet
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

    def test_phaseb_extracts_three_distinct_hunks(self):
        """Phase B should preserve hunk boundaries for multi-impact analysis - verifies hunk-aware parsing is active."""
        result = extract_change_content('test_fixtures/diffs/multi_impact_three_hunks.diff')
    
        assert 'hunks' in result
        assert isinstance(result['hunks'], list)
        assert len(result['hunks']) == 3
    
        # Check hunk headers are preserved
        headers = [h.get('header', '') for h in result['hunks']]
        assert any('Trade marks enforcement guidance' in h for h in headers)
        assert any('Patent filing process' in h for h in headers)
        assert any('Design registration overview' in h for h in headers)
    
        # Backward compatibility still intact
        assert 'change_context' in result
        assert len(result['change_context']) > 0

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

    def test_phaseb_returns_impacted_pages_structure(self, mock_semantic_data):
        result = calculate_similarity(
            'test_fixtures/diffs/high_relevance_trademark.diff',
            mock_semantic_data=mock_semantic_data
        )
        assert result['status'] == 'success'
        assert 'impacted_pages' in result
        assert isinstance(result['impacted_pages'], list)
        assert len(result['impacted_pages']) >= 1
        assert 'top_chunks' in result
        assert isinstance(result['top_chunks'], list)

    def test_phaseb_primary_match_backwards_compatible(self, mock_semantic_data):
        result = calculate_similarity(
            'test_fixtures/diffs/high_relevance_trademark.diff',
            mock_semantic_data=mock_semantic_data
        )
        # keep legacy fields populated
        assert result.get('matched_udid') == 'IPFR-001'
        assert result.get('matched_chunk_id') is not None

    def test_multi_impact_three_hunks_detected(self, mock_semantic_data):
        """Phase B should surface multiple impacted pages from a 3-hunk diff."""
        result = calculate_similarity(
            'test_fixtures/diffs/multi_impact_three_hunks.diff',
            mock_semantic_data=mock_semantic_data
        )
    
        assert result['status'] == 'success'
    
        # New Phase B structures
        assert 'change_hunks' in result
        assert 'hunk_matches' in result
        assert 'impacted_pages' in result
        assert 'top_chunks' in result
    
        assert isinstance(result['change_hunks'], list)
        assert isinstance(result['hunk_matches'], list)
        assert isinstance(result['impacted_pages'], list)
        assert isinstance(result['top_chunks'], list)
    
        assert len(result['change_hunks']) == 3
        assert len(result['hunk_matches']) == 3
        assert len(result['impacted_pages']) >= 2
    
        # Phase B objective signal
        assert result['multi_impact_likely'] is True
        assert result['impact_count'] >= 2
    
        # Should still preserve primary match fields for compatibility
        assert result.get('matched_udid') is not None
        assert result.get('matched_chunk_id') is not None
    
        # Validate likely impacted pages include the intended mock targets
        impacted_udids = {p['udid'] for p in result['impacted_pages']}
        assert 'IPFR-001' in impacted_udids  # trademark
        assert 'IPFR-002' in impacted_udids  # patent
        # Design may vary slightly depending on embedding behavior; keep this soft:
        assert any(u in impacted_udids for u in ['IPFR-003'])

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

class TestPhaseBRealCorpus:
    """
    Integration tests for Phase B against the real Semantic_Embeddings_Output.json corpus.
    Requires:
      - test_fixtures/diffs/multi_impact_three_hunks.diff
      - Semantic_Embeddings_Output.json in repo root
      - OPENAI_API_KEY set (diff hunks are embedded live)
    """

    def _analysis_summary(self, analysis):
        if not isinstance(analysis, dict):
            return {"analysis_type": str(type(analysis))}

        return {
            "status": analysis.get("status"),
            "should_handover": analysis.get("should_handover"),
            "multi_impact_likely": analysis.get("multi_impact_likely"),
            "impact_count": analysis.get("impact_count"),
            "matched_udid": analysis.get("matched_udid"),
            "matched_chunk_id": analysis.get("matched_chunk_id"),
            "base_similarity": analysis.get("base_similarity"),
            "final_score": analysis.get("final_score"),
            "hunk_count": len(analysis.get("change_hunks", []) or []),
            "impacted_pages_count": len(analysis.get("impacted_pages", []) or []),
            "top_chunks_count": len(analysis.get("top_chunks", []) or []),
            "top_impacted_pages": (analysis.get("impacted_pages") or [])[:10],
            "top_chunks": (analysis.get("top_chunks") or [])[:10],
            "hunk_matches": (analysis.get("hunk_matches") or []),
            "power_words": analysis.get("power_words"),
        }

    def _print_json_block(self, title, payload):
        print(f"\n=== {title} ===")
        print(json.dumps(payload, indent=2, ensure_ascii=False))

    @pytest.mark.integration
    def test_phaseb_real_semantic_generates_and_prints_handover_packet(self):
        """
        Debug/inspection test:
        - Always attempts to generate a handover packet after successful analysis
          (even if should_handover=False) so you can inspect packet structure/content.
        """
        diff_path = "test_fixtures/diffs/multi_impact_three_hunks.diff"
        semantic_json_path = "Semantic_Embeddings_Output.json"

        analysis = None
        packet = None
        packet_path = None
        failure_info = None

        assert os.path.exists(diff_path), f"Missing diff fixture: {diff_path}"
        assert os.path.exists(semantic_json_path), (
            f"Missing {semantic_json_path}. Run pytest from repo root and ensure file exists in repo."
        )

        if not os.getenv("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY not set; skipping real semantic integration test")

        # Force reload from real file (avoid stale cache from prior tests)
        if hasattr(tripwire_module, "_semantic_cache"):
            tripwire_module._semantic_cache = None

        try:
            analysis = calculate_similarity(diff_path)

            # Print analysis summary early so it's visible even if packet generation fails later
            self._print_json_block("Phase B Real Semantic Analysis Summary", self._analysis_summary(analysis))

            assert isinstance(analysis, dict), "calculate_similarity() did not return a dict"
            assert analysis.get("status") == "success", f"Analysis failed: {analysis}"

            # Generate packet FOR INSPECTION (regardless of threshold)
            ts = datetime.datetime.now().isoformat()
            packet_path = generate_handover_packet(
                source_name="TEST - Synthetic Multi Hunk Diff",
                priority="Medium",
                diff_file=os.path.basename(diff_path),  # function usually expects filename label
                analysis=analysis,
                timestamp=ts,
            )

            assert packet_path, "generate_handover_packet returned empty path"
            assert os.path.exists(packet_path), f"Handover packet not created: {packet_path}"

            with open(packet_path, "r", encoding="utf-8") as f:
                packet = json.load(f)

            # Print compact summary first (easy to scan in Actions logs)
            packet_summary = {
                "packet_path": packet_path,
                "packet_id": packet.get("packet_id"),
                "packet_priority": packet.get("packet_priority"),
                "analysis_similarity_score": (packet.get("analysis") or {}).get("similarity_score"),
                "analysis_final_score": (packet.get("analysis") or {}).get("final_score"),
                "multi_impact_likely": (packet.get("analysis") or {}).get("multi_impact_likely"),
                "impact_count": (packet.get("analysis") or {}).get("impact_count"),
                "matched_udid": (packet.get("matched_chunk") or {}).get("udid"),
                "impacted_pages_count": len(packet.get("impacted_pages", []) or []),
                "top_chunks_count": len(packet.get("top_chunks", []) or []),
                "hunks_count": len((packet.get("change") or {}).get("hunks", []) or []),
            }
            self._print_json_block("Phase B Handover Packet Summary", packet_summary)

            # Print the full packet JSON (what you asked to inspect)
            self._print_json_block("Phase B Real Semantic Handover Packet", packet)

            # Save a copy for artifact upload
            os.makedirs("test_outputs", exist_ok=True)
            with open("test_outputs/phaseb_real_semantic_handover_packet.json", "w", encoding="utf-8") as f:
                json.dump(packet, f, indent=2, ensure_ascii=False)

            # Basic schema sanity checks (adjust if your packet shape differs)
            assert "analysis" in packet
            assert "change" in packet
            assert "matched_chunk" in packet

            # Phase B expected additions (if your implementation uses these names)
            assert "impacted_pages" in packet
            assert "top_chunks" in packet

            # Phase B evidence visibility
            assert "multi_impact_likely" in packet["analysis"]
            assert "impact_count" in packet["analysis"]
            assert isinstance((packet.get("change") or {}).get("hunks", []), list)

        except Exception as e:
            failure_info = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "traceback": traceback.format_exc(),
                "packet_path": packet_path,
            }
            raise

        finally:
            # Always print final debug info, even if the test fails
            final_debug = {
                "analysis_summary": self._analysis_summary(analysis) if analysis else None,
                "packet_path": packet_path,
                "packet_keys": sorted(list(packet.keys())) if isinstance(packet, dict) else None,
                "failure_info": failure_info,
            }
            self._print_json_block("Phase B Final Debug", final_debug)

            os.makedirs("test_outputs", exist_ok=True)
            with open("test_outputs/phaseb_real_semantic_final_debug.json", "w", encoding="utf-8") as f:
                json.dump(final_debug, f, indent=2, ensure_ascii=False)

    @pytest.mark.integration
    def test_phaseb_real_semantic_threshold_faithful_packet_generation(self):
        """
        Threshold-faithful variant:
        - Only generates a packet if analysis says should_handover=True
        - Useful to validate production-like behavior
        """
        diff_path = "test_fixtures/diffs/multi_impact_three_hunks.diff"
        semantic_json_path = "Semantic_Embeddings_Output.json"

        assert os.path.exists(diff_path), f"Missing diff fixture: {diff_path}"
        assert os.path.exists(semantic_json_path), f"Missing {semantic_json_path}"

        if not os.getenv("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY not set; skipping real semantic integration test")

        if hasattr(tripwire_module, "_semantic_cache"):
            tripwire_module._semantic_cache = None

        analysis = calculate_similarity(diff_path)
        self._print_json_block("Threshold-Faithful Analysis Summary", self._analysis_summary(analysis))

        assert analysis.get("status") == "success", f"Analysis failed: {analysis}"

        if not analysis.get("should_handover"):
            pytest.skip(
                f"Analysis succeeded but should_handover=False "
                f"(final_score={analysis.get('final_score')}, impact_count={analysis.get('impact_count')})"
            )

        ts = datetime.datetime.now().isoformat()
        packet_path = generate_handover_packet(
            source_name="TEST - Synthetic Multi Hunk Diff",
            priority="Medium",
            diff_file=os.path.basename(diff_path),
            analysis=analysis,
            timestamp=ts,
        )

        assert os.path.exists(packet_path), f"Handover packet was not created: {packet_path}"

        with open(packet_path, "r", encoding="utf-8") as f:
            packet = json.load(f)

        self._print_json_block("Threshold-Faithful Handover Packet", packet)

        os.makedirs("test_outputs", exist_ok=True)
        with open("test_outputs/phaseb_real_semantic_threshold_faithful_packet.json", "w", encoding="utf-8") as f:
            json.dump(packet, f, indent=2, ensure_ascii=False)

class TestPhaseCPowerWordGating:
    def test_weak_only_terms_do_not_overboost_low_semantic_match(self):
        # "may" + "30 days" alone are weak signals and should not rescue noise
        power = detect_power_words("An applicant may respond within 30 days.")
        base = 0.05
        final = calculate_final_score(base, power)

        assert power["weak_only"] is True
        assert final == pytest.approx(base)

    def test_strong_legal_trigger_can_still_boost_low_semantic_match(self):
        power = detect_power_words("Penalty of $150,000 applies. You must comply.")
        base = 0.05
        final = calculate_final_score(base, power)

        assert power["has_strong_trigger"] is True
        assert final > base  # strong trigger override is allowed

    def test_numeric_mode_is_backwards_compatible(self):
        # Existing tests and callers that pass a numeric boost should keep additive behaviour
        assert calculate_final_score(0.40, 0.15) == pytest.approx(0.55)


class TestPhaseCDiffGeneration:
    def test_get_diff_returns_unified_diff_text(self):
        with tempfile.TemporaryDirectory() as td:
            old_path = Path(td) / "old.txt"
            old_path.write_text("line1\nline2\nline3\n", encoding="utf-8")
            new_content = "line1\nline2 changed\nline3\n"

            diff_text = get_diff(str(old_path), new_content)

            assert diff_text is not None
            assert diff_text.startswith("--- ")
            assert "\n+++ " in diff_text
            assert "@@" in diff_text
            assert "-line2" in diff_text
            assert "+line2 changed" in diff_text


class TestPhaseCHandoverPacket:
    def test_packet_includes_llm_readiness_and_trims_large_text(self, monkeypatch):
        # Keep packet output isolated
        with tempfile.TemporaryDirectory() as td:
            monkeypatch.setattr(tripwire_module, "HANDOVER_DIR", td, raising=False)

            huge_text = "X" * 15000
            analysis = {
                "status": "success",
                "matched_udid": "B1000",
                "matched_chunk_id": "B1000::c1",
                "matched_chunk_raw": {
                    "UDID": "B1000",
                    "Chunk_ID": "B1000::c1",
                    "Main_Title": "Receiving a letter of demand",
                    "Headline_Alt": "Letter of demand basics",
                    "URL": "https://example.test/b1000",
                    "Chunk_Text": huge_text,
                    "Chunk_Context_Prepend": "context",
                    "Chunk_Token_Count": 999,
                },
                "matched_text": huge_text,
                "power_words": {"count": 2, "found": ["must", "$150,000"]},
                "base_similarity": 0.41,
                "final_score": 0.58,
                "threshold": 0.45,
                "should_handover": True,
                "multi_impact_likely": True,
                "impact_count": 3,
                "multi_impact_threshold": 0.40,
                "change_text": huge_text,
                "change_hunks": [
                    {
                        "hunk_index": 0,
                        "header": "@@ -1,3 +1,3 @@",
                        "change_preview": huge_text,
                    }
                ],
                "hunk_matches": [
                    {
                        "hunk_index": 0,
                        "top_chunks": [
                            {
                                "udid": "B1000",
                                "chunk_id": "B1000::c1",
                                "headline_alt": "Letter of demand basics",
                                "base_similarity": 0.41,
                                "final_score": 0.58,
                                "matched_terms": ["must"],
                            }
                        ],
                        "top_pages": [
                            {
                                "udid": "B1000",
                                "aggregated_final_score": 0.58,
                                "chunk_hits": 1,
                                "distinct_hunk_hits": 1,
                            }
                        ],
                    }
                ],
                "impacted_pages": [
                    {
                        "rank": 1,
                        "udid": "B1000",
                        "url": "https://example.test/b1000",
                        "title": "Receiving a letter of demand",
                        "aggregated_final_score": 0.58,
                        "aggregated_base_similarity": 0.41,
                        "coverage_bonus": 0.05,
                        "density_bonus": 0.02,
                        "chunk_hits": 2,
                        "distinct_hunk_hits": 1,
                        "matched_hunk_indices": [0],
                        "best_chunk": {
                            "chunk_id": "B1000::c1",
                            "headline_alt": "Letter of demand basics",
                            "final_score": 0.58,
                            "base_similarity": 0.41,
                            "chunk_text": huge_text,
                        },
                        "supporting_chunks": [
                            {
                                "chunk_id": "B1000::c2",
                                "final_score": 0.51,
                                "base_similarity": 0.39,
                                "chunk_text": huge_text,
                            }
                        ],
                    }
                ],
                "top_chunks": [
                    {
                        "rank": 1,
                        "udid": "B1000",
                        "chunk_id": "B1000::c1",
                        "headline_alt": "Letter of demand basics",
                        "base_similarity": 0.41,
                        "final_score": 0.58,
                        "chunk_text": huge_text,
                        "matched_terms": ["must", "$150,000"],
                    }
                ],
            }

            ts = datetime.datetime.now().isoformat()
            packet_path = generate_handover_packet("TEST_SOURCE", "High", "example.diff", analysis, ts)
            payload = json.loads(Path(packet_path).read_text(encoding="utf-8"))

            assert "llm_readiness" in payload
            assert payload["llm_readiness"]["estimated_tokens"] > 0
            assert "recommended_handling" in payload["llm_readiness"]

            # Large text should be truncated in multiple places
            assert len(payload["change"]["hunk"]) < len(huge_text)
            assert len(payload["matched_chunk"]["chunk_text"]) < len(huge_text)
            assert len(payload["impacted_pages"][0]["best_chunk"]["chunk_text"]) < len(huge_text)

@pytest.mark.integration
def test_phasec_real_corpus_packet_summary_prints(tmp_path):
    """
    Optional integration check for GitHub/local runs:
    - Requires Semantic_Embeddings_Output.json in repo root
    - Requires OPENAI_API_KEY (Tripwire embeds diff hunks live)
    Prints a compact packet summary including llm_readiness so Actions logs are easier to inspect.
    """
    semantic_json = "Semantic_Embeddings_Output.json"
    diff_path = "test_fixtures/diffs/multi_impact_three_hunks.diff"

    if not os.path.exists(semantic_json):
        pytest.skip(f"{semantic_json} not found in repo root")
    if not os.path.exists(diff_path):
        pytest.skip(f"{diff_path} not found")
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")

    # Isolate packet output
    orig_handover_dir = tripwire_module.HANDOVER_DIR
    tripwire_module.HANDOVER_DIR = str(tmp_path)
    try:
        analysis = tripwire_module.calculate_similarity(diff_path)  # uses real corpus file + live embeddings
        assert isinstance(analysis, dict)
        assert analysis.get("status") == "success"

        ts = datetime.datetime.now().isoformat()
        packet_path = generate_handover_packet("INTEGRATION_TEST", "Medium", os.path.basename(diff_path), analysis, ts)
        payload = json.loads(Path(packet_path).read_text(encoding="utf-8"))

        summary = {
            "status": analysis.get("status"),
            "should_handover": analysis.get("should_handover"),
            "multi_impact_likely": analysis.get("multi_impact_likely"),
            "impact_count": analysis.get("impact_count"),
            "matched_udid": analysis.get("matched_udid"),
            "matched_chunk_id": analysis.get("matched_chunk_id"),
            "final_score": analysis.get("final_score"),
            "impacted_pages_count": len(analysis.get("impacted_pages", []) or []),
            "top_chunks_count": len(analysis.get("top_chunks", []) or []),
            "packet_llm_readiness": payload.get("llm_readiness"),
        }
        print("\n=== PHASE C REAL-CORPUS SUMMARY ===")
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    finally:
        tripwire_module.HANDOVER_DIR = orig_handover_dir
        
if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
