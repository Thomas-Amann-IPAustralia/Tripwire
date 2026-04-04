# Re-exports all public names from the tripwire package for backwards compatibility.

# Config constants
from .config import (
    AUDIT_LOG,
    SOURCES_FILE,
    OUTPUT_DIR,
    DIFF_DIR,
    HANDOVER_DIR,
    SEMANTIC_EMBEDDINGS_FILE,
    CURRENT_RUN_MANIFEST,
    IPFR_CONTENT_ARCHIVE_DIR,
    LLM_VERIFY_DIR,
    UPDATE_SUGGESTIONS_DIR,
    TOP_N_VERIFICATION_CANDIDATES,
    LLM_MODEL,
    STAGE5_LLM_MODEL,
    TAGS_TO_EXCLUDE,
    SEMANTIC_MODEL,
    CANDIDATE_MIN_SCORE,
    MEDIUM_PRIMARY_HANDOVER_THRESHOLD,
    LOW_PRIMARY_HANDOVER_THRESHOLD,
    HUNK_CHUNK_MIN_SIMILARITY,
    MAX_CANDIDATES_PER_PACKET,
    MAX_RELEVANT_CHUNK_IDS_PER_CANDIDATE,
    PER_HUNK_SUMMARY_LIMIT,
    PAGE_HUNK_COVERAGE_BONUS,
    MAX_PAGE_COVERAGE_BONUS,
    PAGE_CHUNK_DENSITY_BONUS,
    MAX_PAGE_DENSITY_BONUS,
    AUDIT_HEADERS,
    logger,
)

# Utils
from .utils import (
    canonical_chunk_id,
    format_relevant_diff_text,
    _now_iso,
    _list_to_semicolon,
    _decision_to_human,
    _confidence_to_human,
    _compute_overlap_metrics,
)

# Audit
from .audit import (
    ensure_audit_log_headers,
    append_audit_row,
    update_audit_row_by_key,
    get_last_version_id,
    log_stage3_to_audit,
    log_to_audit,
)

# LLM client
from .llm_client import get_openai_client, _call_llm_json

# Stage 0
from .stage0_detect import fetch_stage0_metadata

# Stage 1
from .stage1_fetch import (
    initialize_driver,
    clean_html_content,
    fetch_webpage_content,
    sanitize_rss,
    fetch_legislation_metadata,
    download_legislation_content,
)

# Stage 2
from .stage2_diff import (
    get_diff,
    save_to_archive,
    save_diff_record,
    parse_diff_hunks,
    extract_change_content,
)

# Stage 3
from .stage3_score import (
    detect_power_words,
    calculate_final_score,
    get_primary_handover_threshold_for_priority,
    should_generate_handover,
    calculate_similarity,
    _embed_texts,
    _is_administrative_noise,
    _load_semantic_embeddings,
)

# Handover
from .handover import generate_handover_packets

# IPFR content
from .ipfr_content import (
    resolve_ipfr_content_files,
    parse_markdown_chunks,
    extract_chunk_window,
    build_chunk_index,
    _normalise_chunk_id_list,
    _read_text_file,
)

# Stage 4
from .stage4_verify import (
    run_llm_verification_for_packets,
    summarise_verification_files,
    summarise_verification_file,
    verify_handover_packet_with_llm,
    _extract_confirmed_updates,
    _extract_confirmed_update_chunk_ids,
    _extract_additional_chunks_to_review,
    _extract_pass1_confirmed_chunk_ids,
)

# Stage 5
from .stage5_suggest import run_llm_update_suggestions_for_verification_files

# Review queue
from .review_queue import (
    build_update_review_queue_rows_from_payload,
    write_update_review_queue_csv_from_suggestion_files,
)

# Manifest
from .manifest import write_current_run_manifest, write_github_summary

# Pipeline
from .pipeline import main

# Expose submodules for test-time patching (e.g. monkeypatch.setattr(tripwire.config, ...))
from . import config, stage5_suggest


# Compatibility shim: tests patch tripwire._call_llm_json_with_model; stage5 now calls
# _call_llm_json directly, so update tests to patch tripwire.stage5_suggest._call_llm_json.
# This shim preserves any remaining external callers.
def _call_llm_json_with_model(prompt, model):
    return _call_llm_json(prompt, model=model, fallback={
        "update_required": False,
        "reason": "LLM call failed or output was not valid JSON.",
        "proposed_replacement_text": ""
    })
