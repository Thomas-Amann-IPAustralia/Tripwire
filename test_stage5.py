import csv
import json
import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = "/mnt/data/tripwire.py"
spec = importlib.util.spec_from_file_location("tripwire_stage5_under_test", MODULE_PATH)
tripwire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tripwire)


@pytest.fixture
def stage5_env(tmp_path, monkeypatch):
    handover_dir = tmp_path / "handover_packets"
    update_dir = tmp_path / "llm_update_suggestions"
    audit_log = tmp_path / "audit_log.csv"
    handover_dir.mkdir()
    update_dir.mkdir()

    monkeypatch.setattr(tripwire, "HANDOVER_DIR", str(handover_dir))
    monkeypatch.setattr(tripwire, "UPDATE_SUGGESTIONS_DIR", str(update_dir))
    monkeypatch.setattr(tripwire, "AUDIT_LOG", str(audit_log))
    monkeypatch.setattr(tripwire, "STAGE5_LLM_MODEL", "test-stage5-model")

    return {
        "tmp_path": tmp_path,
        "handover_dir": handover_dir,
        "update_dir": update_dir,
        "audit_log": audit_log,
    }


def _write_audit_row(source_name: str, version_id: str, diff_file: str):
    tripwire.append_audit_row({
        "Timestamp": "2026-03-11T10:00:00",
        "Source_Name": source_name,
        "Priority": "High",
        "Status": "Success",
        "Change_Detected": "Yes",
        "Version_ID": version_id,
        "Diff_File": diff_file,
        "Outcome": "analysis_complete",
        "Reason": "Seed row for Stage 5 tests",
    })


def _write_packet(handover_dir: Path, packet_id: str, source_name: str, version_id: str, diff_file: str):
    packet = {
        "packet_id": packet_id,
        "source_change_details": {
            "version_id": version_id,
            "diff_file": diff_file,
            "source": {"name": source_name},
            "hunks": [
                {
                    "hunk_id": 1,
                    "hunk_index": 1,
                    "location_header": "Section 1",
                    "removed": ["Old rule"],
                    "added": ["New rule"],
                }
            ],
        },
    }
    path = handover_dir / f"{packet_id}.json"
    path.write_text(json.dumps(packet), encoding="utf-8")
    return path


def _write_verification_file(path: Path, packet_id: str, per_candidate: list[dict]):
    payload = {
        "packet_id": packet_id,
        "per_candidate": per_candidate,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _candidate(udid: str, confirmed_chunk_ids: list[str], *, additional=None, best_chunk_id: str = ""):
    return {
        "udid": udid,
        "best_chunk_id": best_chunk_id or (confirmed_chunk_ids[0] if confirmed_chunk_ids else ""),
        "matched_hunk_indices": [1],
        "pass1_result": {
            "decision": "impact",
            "chunk_id": best_chunk_id or (confirmed_chunk_ids[0] if confirmed_chunk_ids else ""),
        },
        "pass2_result": {
            "confirmed_update_chunk_ids": confirmed_chunk_ids,
            "additional_chunks_to_review": additional or [],
        },
    }


def _patch_markdown_resolution(monkeypatch, markdown_by_udid: dict[str, str], tmp_path: Path):
    markdown_paths = {}
    for udid, markdown in markdown_by_udid.items():
        md_path = tmp_path / f"{udid}.md"
        md_path.write_text(markdown, encoding="utf-8")
        markdown_paths[udid] = md_path

    def fake_resolve(udid: str, prefer_test_files: bool = True):
        return {
            "udid": udid,
            "markdown_path": str(markdown_paths[udid]),
            "jsonld_path": "",
            "missing": [],
        }

    monkeypatch.setattr(tripwire, "resolve_ipfr_content_files", fake_resolve)
    monkeypatch.setattr(tripwire, "_read_text_file", lambda path, max_chars=40000: Path(path).read_text(encoding="utf-8"))


def _patch_llm(monkeypatch):
    def fake_llm(prompt: str, model: str):
        chunk_id = prompt.split("Chunk ID:", 1)[1].splitlines()[0].strip()
        return {
            "update_required": True,
            "reason": f"Drafted update for {chunk_id}",
            "proposed_replacement_text": f"Updated content for {chunk_id}",
        }

    monkeypatch.setattr(tripwire, "_call_llm_json_with_model", fake_llm)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _read_audit_rows(audit_log: Path):
    with audit_log.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_stage5_one_confirmed_chunk_on_one_page(stage5_env, monkeypatch):
    source_name = "WIPO"
    version_id = "v1"
    diff_file = "wipo.diff"
    packet_id = "packet-one-page"

    _write_audit_row(source_name, version_id, diff_file)
    _write_packet(stage5_env["handover_dir"], packet_id, source_name, version_id, diff_file)

    verification_path = _write_verification_file(
        stage5_env["tmp_path"] / "verification_one.json",
        packet_id,
        [_candidate("101-2", ["101-2-C01"])],
    )

    _patch_markdown_resolution(
        monkeypatch,
        {
            "101-2": "# Page\n\n<!-- chunk_id: 101-2-C01 -->\nOriginal chunk text\n",
        },
        stage5_env["tmp_path"],
    )
    _patch_llm(monkeypatch)

    outputs = tripwire.run_llm_update_suggestions_for_verification_files([str(verification_path)])

    assert len(outputs) == 1
    payload = _read_json(Path(outputs[0]))
    assert payload["status"] == "Suggestion Generated"
    assert len(payload["pages"]) == 1
    page = payload["pages"][0]
    assert page["udid"] == "101-2"
    assert len(page["confirmed_update_suggestions"]) == 1
    assert page["confirmed_update_suggestions"][0]["chunk_id"] == "101-2-C01"
    assert page["confirmed_update_suggestions"][0]["status"] == "suggested"

    rows = _read_audit_rows(stage5_env["audit_log"])
    assert rows[-1]["AI Update Suggestion Status"] == "Suggestion Generated"
    assert rows[-1]["AI Update Suggested Chunks"] == "101-2:101-2-C01"


def test_stage5_multiple_confirmed_chunks_on_one_page(stage5_env, monkeypatch):
    source_name = "WIPO"
    version_id = "v2"
    diff_file = "multi-one-page.diff"
    packet_id = "packet-multi-chunks"

    _write_audit_row(source_name, version_id, diff_file)
    _write_packet(stage5_env["handover_dir"], packet_id, source_name, version_id, diff_file)

    verification_path = _write_verification_file(
        stage5_env["tmp_path"] / "verification_multi_chunks.json",
        packet_id,
        [_candidate("101-2", ["101-2-C01", "101-2-C02"])],
    )

    _patch_markdown_resolution(
        monkeypatch,
        {
            "101-2": (
                "# Page\n\n"
                "<!-- chunk_id: 101-2-C01 -->\nFirst chunk\n\n"
                "<!-- chunk_id: 101-2-C02 -->\nSecond chunk\n"
            ),
        },
        stage5_env["tmp_path"],
    )
    _patch_llm(monkeypatch)

    outputs = tripwire.run_llm_update_suggestions_for_verification_files([str(verification_path)])

    payload = _read_json(Path(outputs[0]))
    page = payload["pages"][0]
    drafted_ids = [item["chunk_id"] for item in page["confirmed_update_suggestions"]]
    assert drafted_ids == ["101-2-C01", "101-2-C02"]
    assert payload["status"] == "Suggestion Generated"


def test_stage5_confirmed_chunks_across_multiple_pages_from_one_diff(stage5_env, monkeypatch):
    source_name = "WIPO"
    version_id = "v3"
    diff_file = "single-diff-multi-page.diff"
    packet_id = "packet-multi-page"

    _write_audit_row(source_name, version_id, diff_file)
    _write_packet(stage5_env["handover_dir"], packet_id, source_name, version_id, diff_file)

    verification_path = _write_verification_file(
        stage5_env["tmp_path"] / "verification_multi_page.json",
        packet_id,
        [
            _candidate("101-2", ["101-2-C01"]),
            _candidate("102-1", ["102-1-C03"]),
        ],
    )

    _patch_markdown_resolution(
        monkeypatch,
        {
            "101-2": "# Page A\n\n<!-- chunk_id: 101-2-C01 -->\nChunk A\n",
            "102-1": "# Page B\n\n<!-- chunk_id: 102-1-C03 -->\nChunk B\n",
        },
        stage5_env["tmp_path"],
    )
    _patch_llm(monkeypatch)

    outputs = tripwire.run_llm_update_suggestions_for_verification_files([str(verification_path)])

    assert len(outputs) == 1
    payload = _read_json(Path(outputs[0]))
    assert payload["diff_file"] == diff_file
    assert sorted(page["udid"] for page in payload["pages"]) == ["101-2", "102-1"]

    chunk_pairs = sorted(
        (page["udid"], item["chunk_id"])
        for page in payload["pages"]
        for item in page["confirmed_update_suggestions"]
    )
    assert chunk_pairs == [("101-2", "101-2-C01"), ("102-1", "102-1-C03")]


def test_stage5_additional_review_chunks_present_but_not_drafted(stage5_env, monkeypatch):
    source_name = "WIPO"
    version_id = "v4"
    diff_file = "additional-review.diff"
    packet_id = "packet-additional-review"

    _write_audit_row(source_name, version_id, diff_file)
    _write_packet(stage5_env["handover_dir"], packet_id, source_name, version_id, diff_file)

    verification_path = _write_verification_file(
        stage5_env["tmp_path"] / "verification_additional_review.json",
        packet_id,
        [
            _candidate(
                "101-2",
                ["101-2-C01"],
                additional=[{"chunk_id": "101-2-C99", "reason": "Possible follow-up"}],
            )
        ],
    )

    _patch_markdown_resolution(
        monkeypatch,
        {
            "101-2": "# Page\n\n<!-- chunk_id: 101-2-C01 -->\nConfirmed chunk\n",
        },
        stage5_env["tmp_path"],
    )
    _patch_llm(monkeypatch)

    outputs = tripwire.run_llm_update_suggestions_for_verification_files([str(verification_path)])

    payload = _read_json(Path(outputs[0]))
    page = payload["pages"][0]
    drafted_ids = [item["chunk_id"] for item in page["confirmed_update_suggestions"]]
    review_ids = [item["chunk_id"] for item in page["additional_chunks_to_review"]]

    assert drafted_ids == ["101-2-C01"]
    assert review_ids == ["101-2-C99"]
    assert "101-2-C99" not in drafted_ids


def test_stage5_unresolved_chunk_id_produces_partial_suggestion_generated(stage5_env, monkeypatch):
    source_name = "WIPO"
    version_id = "v5"
    diff_file = "partial-suggestion.diff"
    packet_id = "packet-partial"

    _write_audit_row(source_name, version_id, diff_file)
    _write_packet(stage5_env["handover_dir"], packet_id, source_name, version_id, diff_file)

    verification_path = _write_verification_file(
        stage5_env["tmp_path"] / "verification_partial.json",
        packet_id,
        [_candidate("101-2", ["101-2-C01", "101-2-C404"])],
    )

    _patch_markdown_resolution(
        monkeypatch,
        {
            "101-2": "# Page\n\n<!-- chunk_id: 101-2-C01 -->\nResolvable chunk\n",
        },
        stage5_env["tmp_path"],
    )
    _patch_llm(monkeypatch)

    outputs = tripwire.run_llm_update_suggestions_for_verification_files([str(verification_path)])

    payload = _read_json(Path(outputs[0]))
    assert payload["status"] == "Partial Suggestion Generated"

    page = payload["pages"][0]
    by_chunk_id = {item["chunk_id"]: item for item in page["confirmed_update_suggestions"]}
    assert by_chunk_id["101-2-C01"]["status"] == "suggested"
    assert by_chunk_id["101-2-C404"]["status"] == "unresolved_chunk"
    assert by_chunk_id["101-2-C404"]["reason"] == "Could not resolve authoritative markdown chunk text for this chunk_id."

    rows = _read_audit_rows(stage5_env["audit_log"])
    assert rows[-1]["AI Update Suggestion Status"] == "Partial Suggestion Generated"
