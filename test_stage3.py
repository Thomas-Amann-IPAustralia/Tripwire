import os
import sys
import json
import types
import tempfile
import importlib.util
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parent
TRIPWIRE_PATH = ROOT / "tripwire_dropin.py"


def _install_stub_modules():
    """Install lightweight stubs so tripwire_dropin imports in CI without optional deps."""
    # selenium tree
    selenium = types.ModuleType("selenium")
    selenium.webdriver = types.ModuleType("selenium.webdriver")
    selenium.webdriver.chrome = types.ModuleType("selenium.webdriver.chrome")
    selenium.webdriver.chrome.service = types.ModuleType("selenium.webdriver.chrome.service")
    selenium.webdriver.chrome.service.Service = object
    selenium.webdriver.support = types.ModuleType("selenium.webdriver.support")
    selenium.webdriver.support.ui = types.ModuleType("selenium.webdriver.support.ui")
    selenium.webdriver.support.ui.WebDriverWait = object
    selenium.webdriver.support.expected_conditions = types.ModuleType("selenium.webdriver.support.expected_conditions")
    selenium.webdriver.common = types.ModuleType("selenium.webdriver.common")
    selenium.webdriver.common.by = types.ModuleType("selenium.webdriver.common.by")
    selenium.webdriver.common.by.By = object
    selenium.webdriver.ChromeOptions = object
    selenium.webdriver.Chrome = object

    sys.modules.setdefault("selenium", selenium)
    sys.modules.setdefault("selenium.webdriver", selenium.webdriver)
    sys.modules.setdefault("selenium.webdriver.chrome", selenium.webdriver.chrome)
    sys.modules.setdefault("selenium.webdriver.chrome.service", selenium.webdriver.chrome.service)
    sys.modules.setdefault("selenium.webdriver.support", selenium.webdriver.support)
    sys.modules.setdefault("selenium.webdriver.support.ui", selenium.webdriver.support.ui)
    sys.modules.setdefault("selenium.webdriver.support.expected_conditions", selenium.webdriver.support.expected_conditions)
    sys.modules.setdefault("selenium.webdriver.common", selenium.webdriver.common)
    sys.modules.setdefault("selenium.webdriver.common.by", selenium.webdriver.common.by)

    # webdriver manager
    wdm = types.ModuleType("webdriver_manager")
    wdm.chrome = types.ModuleType("webdriver_manager.chrome")
    class _ChromeDriverManager:
        def install(self):
            return "chromedriver"
    wdm.chrome.ChromeDriverManager = _ChromeDriverManager
    sys.modules.setdefault("webdriver_manager", wdm)
    sys.modules.setdefault("webdriver_manager.chrome", wdm.chrome)

    # selenium_stealth
    ss = types.ModuleType("selenium_stealth")
    ss.stealth = lambda *a, **k: None
    sys.modules.setdefault("selenium_stealth", ss)

    # markdownify
    mdm = types.ModuleType("markdownify")
    mdm.markdownify = lambda html, **kwargs: html
    sys.modules.setdefault("markdownify", mdm)

    # bs4/docx are usually installed, but provide minimal stubs if not
    if "bs4" not in sys.modules:
        bs4 = types.ModuleType("bs4")
        class _BS:
            def __init__(self, *a, **k): pass
            def find(self, *a, **k): return None
            def find_all(self, *a, **k): return []
            def prettify(self): return ""
            @property
            def body(self): return None
        bs4.BeautifulSoup = _BS
        sys.modules.setdefault("bs4", bs4)
    if "docx" not in sys.modules:
        docx = types.ModuleType("docx")
        class _Doc:
            paragraphs = []
        docx.Document = lambda *a, **k: _Doc()
        sys.modules.setdefault("docx", docx)

    # openai stub to avoid API key requirement at import time
    openai = types.ModuleType("openai")
    class _Embeddings:
        def create(self, *a, **k):
            raise RuntimeError("OpenAI stub called before monkeypatch in tests")
    class _OpenAI:
        def __init__(self, *a, **k):
            self.embeddings = _Embeddings()
    openai.OpenAI = _OpenAI
    sys.modules.setdefault("openai", openai)


def _load_tripwire_module():
    _install_stub_modules()
    spec = importlib.util.spec_from_file_location("tripwire_under_test", TRIPWIRE_PATH)
    module = importlib.util.module_from_spec(spec)
    from typing import Tuple as _Tuple
    module.Tuple = _Tuple  # patch missing import in current drop-in file
    sys.modules["tripwire_under_test"] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def tripwire(monkeypatch):
    mod = _load_tripwire_module()
    # isolate directories/files for packet generation tests
    tmp_root = tempfile.TemporaryDirectory()
    root = Path(tmp_root.name)
    monkeypatch.setattr(mod, "HANDOVER_DIR", str(root / "handover_packets"), raising=False)
    monkeypatch.setattr(mod, "DIFF_DIR", str(root / "diff_archive"), raising=False)
    # Shim missing helpers/constants in current drop-in if absent
    if not hasattr(mod, "_truncate_text"):
        monkeypatch.setattr(mod, "_truncate_text", lambda text, max_chars: (text or "")[:max_chars], raising=False)
    if not hasattr(mod, "_estimate_packet_size"):
        monkeypatch.setattr(mod, "_estimate_packet_size", lambda packet: (len(json.dumps(packet)), max(1, len(json.dumps(packet)) // 4)), raising=False)
    if not hasattr(mod, "_trim_packet_for_llm"):
        monkeypatch.setattr(mod, "_trim_packet_for_llm", lambda packet: packet, raising=False)
    for k, v in {
        "MAX_HUNK_PREVIEW_CHARS": 400,
        "MAX_CHANGE_TEXT_CHARS": 2000,
        "HANDOVER_PACKET_TARGET_MAX_CHARS": 60000,
        "HANDOVER_PACKET_HARD_MAX_CHARS": 120000,
    }.items():
        if not hasattr(mod, k):
            monkeypatch.setattr(mod, k, v, raising=False)
    os.makedirs(mod.HANDOVER_DIR, exist_ok=True)
    os.makedirs(mod.DIFF_DIR, exist_ok=True)
    yield mod
    tmp_root.cleanup()


class _EmbResp:
    def __init__(self, vectors):
        self.data = [types.SimpleNamespace(embedding=v) for v in vectors]


class _FakeEmbeddingsClient:
    def __init__(self, vectors_to_return):
        self._vectors = vectors_to_return
        self.embeddings = types.SimpleNamespace(create=self._create)

    def _create(self, input, model):
        # return vectors corresponding to number of hunk texts requested
        vecs = self._vectors[: len(input)]
        return _EmbResp(vecs)


def _write_temp_diff(text: str) -> str:
    fd, raw_path = tempfile.mkstemp(suffix=".diff")
    os.close(fd)
    Path(raw_path).write_text(text, encoding="utf-8")
    return raw_path


def _three_hunk_diff_text():
    return (
        "--- a/source.txt\n"
        "+++ b/source.txt\n"
        "@@ -1,2 +1,2 @@\n"
        "-old trademark opposition wording\n"
        "+new trade mark opposition must include evidence\n"
        "@@ -10,2 +10,2 @@\n"
        "-old fees\n"
        "+penalty applies within 30 days\n"
        "@@ -20,2 +20,2 @@\n"
        "-old archive ref\n"
        "+Archives Act 1983 may apply\n"
    )


def test_dropin_has_main_entrypoint(tripwire):
    assert hasattr(tripwire, "main")
    assert callable(tripwire.main)


def test_parse_diff_hunks_returns_three_hunks(tripwire):
    diff_path = ROOT / "multi_impact_three_hunks.diff"
    hunks = tripwire.parse_diff_hunks(str(diff_path))
    assert len(hunks) == 3
    assert [h["hunk_index"] for h in hunks] == [1, 2, 3]
    assert all(h.get("change_context") for h in hunks)


def test_priority_gating_high_bypasses_threshold_but_requires_candidates(tripwire):
    ok, reason, thr = tripwire.should_generate_handover_for_priority("High", primary_score=0.01, impact_count=2)
    assert ok is True
    assert "bypassed" in reason.lower()
    assert thr is None

    ok2, reason2, thr2 = tripwire.should_generate_handover_for_priority("High", primary_score=0.99, impact_count=0)
    assert ok2 is False
    assert "no candidates" in reason2.lower()
    assert thr2 is None  # high priority still bypasses score gate, but candidate gate failed


def test_priority_gating_medium_and_low_filter_noise(tripwire):
    ok_m, _, thr_m = tripwire.should_generate_handover_for_priority("Medium", primary_score=0.46, impact_count=1)
    ok_l, _, thr_l = tripwire.should_generate_handover_for_priority("Low", primary_score=0.46, impact_count=1)
    assert ok_m is True
    assert ok_l is False
    assert pytest.approx(thr_m, rel=1e-6) == 0.45
    assert pytest.approx(thr_l, rel=1e-6) == 0.50


def test_calculate_similarity_returns_all_threshold_passing_candidates_and_high_priority_handover(tripwire, monkeypatch):
    # Three hunk embeddings generated by fake client (2D for easy cosine intuition)
    hunk_vectors = [
        [1.0, 0.0],   # hunk 1 -> page A
        [0.0, 1.0],   # hunk 2 -> page B
        [0.7, 0.7],   # hunk 3 -> page C (and some A/B overlap)
    ]
    monkeypatch.setattr(tripwire, "client", _FakeEmbeddingsClient(hunk_vectors), raising=False)

    # Mock corpus chunks (4 chunks across 3 pages)
    mock_semantic_data = {
        "embeddings": np.array([
            [1.0, 0.0],    # A chunk 1 strong for hunk1
            [0.0, 1.0],    # B chunk 1 strong for hunk2
            [0.7, 0.7],    # C chunk 1 strong for hunk3
            [0.6, 0.8],    # B chunk 2 also plausible for hunk3
        ]),
        "udids": ["UDID_A", "UDID_B", "UDID_C", "UDID_B"],
        "chunk_texts": ["A text", "B text", "C text", "B2 text"],
        "chunks_raw": [
            {"UDID": "UDID_A", "Chunk_ID": "A_1", "Chunk_Text": "A text", "Headline_Alt": "A Head"},
            {"UDID": "UDID_B", "Chunk_ID": "B_1", "Chunk_Text": "B text", "Headline_Alt": "B Head"},
            {"UDID": "UDID_C", "Chunk_ID": "C_1", "Chunk_Text": "C text", "Headline_Alt": "C Head"},
            {"UDID": "UDID_B", "Chunk_ID": "B_2", "Chunk_Text": "B2 text", "Headline_Alt": "B Head 2"},
        ],
    }

    diff_path = _write_temp_diff(_three_hunk_diff_text())
    result = tripwire.calculate_similarity(diff_path, mock_semantic_data=mock_semantic_data, source_priority="High")

    assert result["status"] == "success"
    assert result["impact_count"] >= 1
    assert result["should_handover"] is True  # High priority bypasses primary score gate when candidates exist
    assert result["source_priority"].lower() == "high"

    candidates = result.get("threshold_passing_candidates", [])
    assert candidates, "Expected at least one threshold-passing candidate"
    # All returned threshold-passing candidates must meet the threshold
    assert all(c["aggregated_final_score"] >= tripwire.CANDIDATE_MIN_SCORE for c in candidates)

    # Hunk summaries should report threshold-based retrieval, not top-k-only behavior
    hunk_matches = result.get("hunk_matches", [])
    assert len(hunk_matches) == 3
    assert all("chunk_similarity_threshold" in h for h in hunk_matches)


def test_generate_handover_packets_batches_without_truncating_threshold_passing_candidates(tripwire, monkeypatch):
    monkeypatch.setattr(tripwire, "MAX_CANDIDATES_PER_PACKET", 2, raising=False)
    monkeypatch.setattr(tripwire, "MAX_RELEVANT_CHUNK_IDS_PER_CANDIDATE", 10, raising=False)

    # 5 candidates should become 3 packets (2 + 2 + 1)
    candidates = []
    for i in range(5):
        candidates.append({
            "udid": f"UDID_{i}",
            "rank": i + 1,
            "aggregated_final_score": 0.60 - (i * 0.02),
            "aggregated_base_similarity": 0.50 - (i * 0.01),
            "matched_hunk_indices": [1, 2] if i % 2 == 0 else [2],
            "chunk_hits": 2,
            "distinct_hunk_hits": 2 if i % 2 == 0 else 1,
            "coverage_bonus": 0.03,
            "density_bonus": 0.01,
            "supporting_chunks": [
                {"chunk_id": f"C{i}_1"},
                {"chunk_id": f"C{i}_2"},
            ],
            "best_chunk": {"chunk_id": f"C{i}_1", "headline_alt": f"Headline {i}"},
        })

    analysis = {
        "status": "success",
        "final_score": 0.60,
        "base_similarity": 0.50,
        "matched_udid": "UDID_0",
        "power_words": {"found": ["must", "penalty"], "count": 2, "score": 0.3},
        "candidate_min_score": tripwire.CANDIDATE_MIN_SCORE,
        "source_priority": "High",
        "primary_handover_threshold_used": None,
        "impact_count": 5,
        "multi_impact_likely": True,
        "handover_decision_reason": "High priority source: bypassed primary score gate (candidates exist)",
        "change_text": "Example diff change text",
        "hunk_matches": [
            {
                "hunk_index": 1,
                "header": "@@ -1 +1 @@",
                "change_preview": "must update evidence",
                "power_words_found": ["must"],
                "chunk_similarity_threshold": tripwire.CANDIDATE_MIN_SCORE,
                "passing_chunk_count": 4,
                "top_pages": [{"udid": "UDID_0", "score": 0.6}],
            }
        ],
        "threshold_passing_candidates": candidates,
        "change_hunks": [
            {"hunk_index": 1, "hunk_header": "@@ -1 +1 @@", "change_text": "must update evidence", "power_words_found": ["must"]}
        ],
    }

    paths = tripwire.generate_handover_packets(
        source_name="Trade Marks Legislation",
        priority="High",
        diff_file="example.diff",
        analysis=analysis,
        timestamp="2026-02-25T12:00:00",
        version_id="v123",
    )

    assert len(paths) == 3
    all_udids = []
    for p in paths:
        packet = json.loads(Path(p).read_text(encoding="utf-8"))
        assert "llm_handover" in packet
        assert packet["llm_handover"]["packeting_mode"].startswith("candidate_batching")
        assert packet["analysis"]["candidate_min_score"] == tripwire.CANDIDATE_MIN_SCORE
        assert packet["source"]["version_id"] == "v123"
        assert "power_words_found" in packet["analysis"]
        all_udids.extend(packet["llm_handover"]["candidate_udids"])

    assert sorted(all_udids) == sorted([f"UDID_{i}" for i in range(5)])


def test_generate_handover_packets_candidate_entries_include_chunk_ids(tripwire):
    analysis = {
        "status": "success",
        "final_score": 0.7,
        "base_similarity": 0.6,
        "matched_udid": "U1",
        "power_words": {"found": [], "count": 0, "score": 0.0},
        "candidate_min_score": tripwire.CANDIDATE_MIN_SCORE,
        "source_priority": "Medium",
        "primary_handover_threshold_used": 0.45,
        "impact_count": 1,
        "multi_impact_likely": False,
        "handover_decision_reason": "Primary score 0.700 >= medium threshold 0.450",
        "change_text": "change",
        "hunk_matches": [],
        "threshold_passing_candidates": [
            {
                "udid": "U1",
                "rank": 1,
                "aggregated_final_score": 0.7,
                "aggregated_base_similarity": 0.6,
                "matched_hunk_indices": [1],
                "chunk_hits": 1,
                "distinct_hunk_hits": 1,
                "coverage_bonus": 0.0,
                "density_bonus": 0.0,
                "supporting_chunks": [{"chunk_id": "U1_C1"}, {"chunk_id": "U1_C2"}],
                "best_chunk": {"chunk_id": "U1_C1", "headline_alt": "Title"},
            }
        ],
    }
    paths = tripwire.generate_handover_packets("src", "Medium", "d.diff", analysis, "2026-02-25T12:00:00")
    packet = json.loads(Path(paths[0]).read_text(encoding="utf-8"))
    cand = packet["llm_handover"]["candidates"][0]
    assert cand["udid"] == "U1"
    assert cand["relevant_chunk_ids"] == ["U1_C1", "U1_C2"]
    assert cand["best_chunk_id"] == "U1_C1"


def test_power_words_basic_detection_and_scoring_helpers(tripwire):
    p = tripwire.detect_power_words("Applicants must respond within 30 days or pay a penalty.")
    assert p["count"] >= 3
    assert "must" in p["found"]
    assert tripwire.calculate_final_score(0.4, p["score"]) >= 0.4
    assert tripwire.calculate_final_score(0.95, 0.2) == 1.0
