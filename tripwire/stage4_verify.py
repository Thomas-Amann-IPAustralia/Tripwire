import json
import os
import re
import datetime
from typing import Dict, List, Optional, Tuple

from . import config
from .config import logger
from .utils import (
    canonical_chunk_id,
    format_relevant_diff_text,
    _list_to_semicolon,
    _now_iso,
    _decision_to_human,
    _confidence_to_human,
    _compute_overlap_metrics,
)
from .audit import update_audit_row_by_key, append_audit_row
from .llm_client import _call_llm_json
from .ipfr_content import (
    resolve_ipfr_content_files,
    parse_markdown_chunks,
    extract_chunk_window,
    build_chunk_index,
    _normalise_chunk_id_list,
    _read_text_file,
)


def _format_diff_block_for_candidate(packet: dict, candidate: dict) -> str:
    """Return the compact diff block most relevant to this candidate page."""
    hunks = (packet.get("source_change_details", {}) or {}).get("hunks", []) or []
    matched = set(candidate.get("matched_hunk_indices") or [])
    if matched:
        hunks = [h for h in hunks if (h.get("hunk_id") in matched) or (h.get("hunk_index") in matched)]

    hunk_text_parts = []
    for h in hunks:
        removed = "\n".join([f"- {ln}" for ln in (h.get("removed") or []) if str(ln).strip()])
        added = "\n".join([f"+ {ln}" for ln in (h.get("added") or []) if str(ln).strip()])
        header = h.get("location_header") or h.get("hunk_header") or f"hunk {h.get('hunk_id', '')}"
        hunk_text_parts.append(f"{header}\n{removed}\n{added}".strip())
    return "\n\n".join(hunk_text_parts).strip()


def _score_chunk_for_pass1(chunk_text: str, diff_text: str, preferred_chunk_id: str = "", chunk_id: str = "") -> float:
    """Cheap lexical scorer to pick the best Stage 4 Pass 1 chunk from Stage 3 candidates."""
    chunk_text = (chunk_text or "").strip().lower()
    diff_text = (diff_text or "").strip().lower()
    if not chunk_text:
        return -1.0

    token_pattern = r"[a-z0-9][a-z0-9'_-]{2,}"
    chunk_tokens = set(re.findall(token_pattern, chunk_text))
    diff_tokens = set(re.findall(token_pattern, diff_text))
    overlap = len(chunk_tokens & diff_tokens)

    diff_phrases = [p.strip().lower() for p in re.split(r"[\n\.]+", diff_text) if len(p.strip()) >= 12]
    phrase_hits = sum(1 for phrase in diff_phrases if phrase and phrase in chunk_text)

    score = float(overlap) + (phrase_hits * 4.0)
    if preferred_chunk_id and chunk_id and chunk_id == preferred_chunk_id:
        score += 0.5
    return score


def _select_chunk_matched_hunks(chunk: Dict[str, str], packet: dict, candidate: dict) -> List[int]:
    """Estimate which matched diff hunks most likely affect a specific chunk."""
    candidate_hunks = (packet.get("source_change_details", {}) or {}).get("hunks", []) or []
    allowed_hunks = set(candidate.get("matched_hunk_indices") or [])
    if allowed_hunks:
        candidate_hunks = [
            h for h in candidate_hunks
            if (h.get("hunk_id") in allowed_hunks) or (h.get("hunk_index") in allowed_hunks)
        ]

    chunk_text = (chunk.get("text") or "").strip()
    if not chunk_text:
        return sorted(int(x) for x in allowed_hunks if isinstance(x, int))

    scored: List[tuple] = []
    for h in candidate_hunks:
        hunk_id = h.get("hunk_id") if h.get("hunk_id") is not None else h.get("hunk_index")
        if hunk_id is None:
            continue
        diff_text = []
        diff_text.extend([str(x) for x in (h.get("removed") or []) if str(x).strip()])
        diff_text.extend([str(x) for x in (h.get("added") or []) if str(x).strip()])
        score = _score_chunk_for_pass1(
            chunk_text=chunk_text,
            diff_text="\n".join(diff_text),
            preferred_chunk_id=str(candidate.get("best_chunk_id") or ""),
            chunk_id=str(chunk.get("chunk_id") or ""),
        )
        scored.append((float(score), int(hunk_id)))

    positive = sorted({hid for score, hid in scored if score > 0.0})
    if positive:
        return positive

    if scored:
        scored.sort(reverse=True)
        best_score = scored[0][0]
        if best_score >= 0.0:
            return sorted({hid for score, hid in scored if score == best_score})

    return sorted(int(x) for x in allowed_hunks if isinstance(x, int))


def _build_chunk_verification_targets(
    packet: dict,
    candidate: dict,
    chunks: List[Dict[str, str]],
    max_current_chars: int = 420,
    side_max_chars: int = 120,
) -> List[Dict[str, object]]:
    """Build compact chunk-level verification targets for Pass 2."""
    if not chunks:
        return []

    chunk_lookup = {
        canonical_chunk_id(c.get("chunk_id")): c
        for c in chunks
        if c.get("chunk_id")
    }

    stage3_ids = _normalise_chunk_id_list(candidate.get("relevant_chunk_ids") or [])
    preferred_chunk_id = str(candidate.get("best_chunk_id") or "").strip()
    existing_canons = {canonical_chunk_id(x) for x in stage3_ids}
    if preferred_chunk_id and canonical_chunk_id(preferred_chunk_id) not in existing_canons:
        stage3_ids.insert(0, preferred_chunk_id)

    out: List[Dict[str, object]] = []
    seen = set()
    for cid in stage3_ids:
        canon = canonical_chunk_id(cid)
        if not canon or canon in seen:
            continue
        seen.add(canon)
        chunk = chunk_lookup.get(canon)
        if not chunk:
            continue
        window = extract_chunk_window(
            chunks,
            chunk.get("chunk_id", ""),
            fallback_max_chars=max_current_chars,
            side_max_chars=side_max_chars,
        )
        matched_hunk_ids = _select_chunk_matched_hunks(chunk, packet, candidate)
        out.append({
            "chunk_id": chunk.get("chunk_id", ""),
            "matched_hunk_ids": matched_hunk_ids,
            "current_snippet": (window.get("current") or "").strip(),
            "before_snippet": (window.get("before") or "").strip(),
            "after_snippet": (window.get("after") or "").strip(),
        })
    return out


def _select_pass1_target_chunk_id(packet: dict, candidate: dict, chunks: List[Dict[str, str]]) -> str:
    """Pick the best chunk for Pass 1 from Stage 3 relevant_chunk_ids."""
    if not chunks:
        return str(candidate.get("best_chunk_id") or "").strip()

    chunk_lookup = {
        canonical_chunk_id(c.get("chunk_id")): c
        for c in chunks
        if c.get("chunk_id")
    }

    preferred_chunk_id = str(candidate.get("best_chunk_id") or "").strip()
    stage3_ids = [str(x).strip() for x in (candidate.get("relevant_chunk_ids") or []) if str(x).strip()]
    if preferred_chunk_id and preferred_chunk_id not in stage3_ids:
        stage3_ids.insert(0, preferred_chunk_id)
    if not stage3_ids:
        return preferred_chunk_id

    hunks = (packet.get("source_change_details", {}) or {}).get("hunks", []) or []
    matched = set(candidate.get("matched_hunk_indices") or [])
    if matched:
        hunks = [h for h in hunks if (h.get("hunk_id") in matched) or (h.get("hunk_index") in matched)]
    diff_text = format_relevant_diff_text(hunks)

    scored = []
    for cid in stage3_ids:
        resolved = chunk_lookup.get(canonical_chunk_id(cid))
        if not resolved:
            continue
        score = _score_chunk_for_pass1(
            chunk_text=resolved.get("text", ""),
            diff_text=diff_text,
            preferred_chunk_id=preferred_chunk_id,
            chunk_id=resolved.get("chunk_id", ""),
        )
        scored.append((score, resolved.get("chunk_id", "")))

    if not scored:
        return preferred_chunk_id
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1] or preferred_chunk_id


def _build_llm_pass1_prompt(packet: dict, candidate: dict, window: dict, target_chunk_id: str = "") -> str:
    """Pass 1: Verify whether the external change materially impacts the candidate page."""
    packet_id = packet.get("packet_id", "")
    src = (packet.get("source_change_details", {}) or {}).get("source", {}) or {}
    source_name = src.get("name", "")
    priority = src.get("monitoring_priority", "")

    diff_block = _format_diff_block_for_candidate(packet, candidate)
    before = (window.get("before") or "").strip()
    current = (window.get("current") or "").strip()
    after = (window.get("after") or "").strip()
    target_chunk_id = (target_chunk_id or candidate.get("best_chunk_id") or "").strip()

    return f"""System Role: You are a Technical Content Auditor for the IP First Response (IPFR) platform.

Your job is to verify whether the external source update below materially impacts this candidate IPFR page.

Prototype purpose:
- detect all materially impacted IPFR pages
- keep Stage 4 snippet-based for token efficiency
- use this local evidence window to decide PAGE impact only
- do not assume only the target chunk can be impacted later

External source context:
- Source: {source_name}
- Priority: {priority}
- Packet: {packet_id}

External change (candidate-relevant diff hunks):
{diff_block}

Candidate IPFR page:
- UDID: {candidate.get("udid")}
- Local target chunk_id used for this page-level check: {target_chunk_id}

IPFR local evidence window from the actual page markdown:
[Before] preceding chunk for context only
[Current] local target chunk used to test whether the page is impacted
[After] following chunk for context only

Use [Before] and [After] to understand whether the page guidance around [Current] has shifted.
You are deciding PAGE impact here, not final chunk scope.

[Before]
{before}

[Current]
{current}

[After]
{after}

Decision rules:
- Return "impact" if the external change updates, contradicts, invalidates, materially expands, or materially narrows the meaning of the IPFR guidance on this page.
- Return "impact" if the page would become incomplete or misleading without reflecting the external change, even if multiple chunks may later need review.
- Return "no_impact" if the change is unrelated to this page's guidance.
- Return "uncertain" only if the evidence window is insufficient to decide whether the page is impacted.
- If legal thresholds, legal preconditions, scope of protection, scope of infringement, or required actions have changed, treat that as impact.

Return JSON ONLY with this schema:
{{
  "decision": "impact|no_impact|uncertain",
  "udid": "{candidate.get("udid")}",
  "chunk_id": "{target_chunk_id}",
  "confidence": "high|medium|low",
  "reason": "brief explanation grounded in the evidence window",
  "evidence_quote": "a short quote (<=25 words) from the evidence window that supports your decision"
}}
"""


def _build_llm_pass2_prompt(
    packet: dict,
    candidate: dict,
    pass1_result: dict,
    chunk_targets: List[dict],
    page_chunk_index: Optional[List[dict]] = None,
    stage3_relevant_chunk_ids: Optional[List[str]] = None
) -> str:
    """Pass 2: adjudicate Stage 3 candidate chunks individually after page impact is confirmed."""
    packet_id = packet.get("packet_id", "")
    src = (packet.get("source_change_details", {}) or {}).get("source", {}) or {}
    source_name = src.get("name", "")
    priority = src.get("monitoring_priority", "")

    diff_summary = _format_diff_block_for_candidate(packet, candidate)

    if stage3_relevant_chunk_ids is None:
        stage3_relevant_chunk_ids = candidate.get("relevant_chunk_ids") or []
    stage3_relevant_chunk_ids = _normalise_chunk_id_list(stage3_relevant_chunk_ids)[:80]

    pass1_json = json.dumps(pass1_result, ensure_ascii=False)
    targets_json = json.dumps(chunk_targets, ensure_ascii=False)
    page_index_json = json.dumps((page_chunk_index or [])[:80], ensure_ascii=False)

    return f"""System Role: You are a Technical Content Auditor for the IP First Response (IPFR) platform.

Context:
- Packet: {packet_id}
- Source: {source_name}
- Priority: {priority}
- Candidate Page UDID: {candidate.get("udid", "")}
- Candidate Best Chunk: {candidate.get("best_chunk_id", "")}

Pass 1 already CONFIRMED that this candidate page is materially impacted.
Your task now is to identify ALL materially impacted chunks on this page from the Stage 3 candidate set.

Inputs:
1) Confirmed page impact (Pass 1):
{pass1_json}

2) Candidate-relevant source diff hunks:
{diff_summary}

3) Chunk-level verification targets for this page.
Each target is one Stage 3 candidate chunk with its own local snippet window and matched_hunk_ids.
These are the ONLY Stage 3 chunks you may reject.
{targets_json}

4) Full page chunk index for optional follow-up only (chunk_id + snippet):
{page_index_json}

5) Stage 3 retrieval-suggested chunk IDs:
{json.dumps(stage3_relevant_chunk_ids, ensure_ascii=False)}

Instructions:
- Adjudicate every chunk in the chunk-level verification targets list individually.
- Confirm a chunk only if at least one matched diff hunk materially affects that chunk's snippet.
- Reject a Stage 3 chunk only if its own snippet shows it is not materially affected.
- If you are not confident enough to reject a chunk after assessing its own snippet, do NOT reject it. Put it in additional_chunks_to_review instead.
- Include matched_hunk_ids for every confirmed chunk and every additional chunk to review.
- If different hunks affect different chunks on the same page, keep them separate.
- Do not collapse multiple impacted chunks into one chunk just because they are on the same page.
- You may nominate additional chunks not in the Stage 3 list, but only if the page chunk index shows why.
- Keep reasons short and evidence-based.
- Evidence quotes must be <= 25 words and copied from the relevant snippet where possible.

Return JSON ONLY in this shape:

{{
  "confirmed_updates": [
    {{
      "chunk_id": "chunk_id",
      "matched_hunk_ids": [1, 2],
      "reason": "short explanation",
      "evidence_quote": "<=25 words from the snippet"
    }}
  ],
  "rejected_stage3_chunk_ids": ["chunk_id", ...],
  "additional_chunks_to_review": [
    {{
      "chunk_id": "...",
      "matched_hunk_ids": [3],
      "reason": "short explanation",
      "evidence_quote": "<=25 words from the snippet"
    }}
  ],
  "notes": "optional short notes for the human editor"
}}
"""


def _build_llm_verification_prompt(packet: dict, candidates_with_content: List[dict]) -> str:
    """Deprecated (kept for backwards compatibility).

    Stage 4 is now implemented as a two-pass per-candidate workflow:
    - Pass 1: verify impact using a narrow evidence window
    - Pass 2: scope additional chunks to review using a compact chunk index

    This function returns a minimal summary only.
    """
    packet_id = packet.get("packet_id", "")
    cand_list = [c.get("udid") for c in candidates_with_content if isinstance(c, dict)]
    return f"Tripwire Stage 4 uses two-pass verification. Packet {packet_id}. Candidates: {cand_list}"


def verify_handover_packet_with_llm(packet_path: str, prefer_test_files: bool = True) -> Optional[str]:
    """Verify one handover packet using a two-pass LLM workflow (prototype).

    Prototype mode: loads TOP_N_VERIFICATION_CANDIDATES candidates from the packet.
    Note: when productionising, we will switch to verifying EVERY candidate.
    """
    if not packet_path or not os.path.exists(packet_path):
        return None

    with open(packet_path, "r", encoding="utf-8") as f:
        packet = json.load(f)

    targets = packet.get("llm_verification_targets", []) or []
    if not targets:
        return None

    ranked_targets = [t for t in targets if isinstance(t, dict) and t.get("udid")]
    ranked_targets.sort(key=lambda x: (x.get("candidate_rank") is None, x.get("candidate_rank", 9999)))

    resolvable_targets = []
    unresolved_targets = []
    for t in ranked_targets:
        udid = t.get("udid")
        resolved = resolve_ipfr_content_files(udid, prefer_test_files=prefer_test_files)
        if resolved.get("missing"):
            unresolved_targets.append({
                "udid": udid,
                "candidate_rank": t.get("candidate_rank"),
                "resolved_files": resolved,
            })
            continue

        t_copy = dict(t)
        t_copy["_pre_resolved_files"] = resolved
        resolvable_targets.append(t_copy)

    top_targets = resolvable_targets[:max(1, config.TOP_N_VERIFICATION_CANDIDATES)]

    per_candidate_results = []
    impacted_pages = []
    any_uncertain = False
    missing_any = bool(unresolved_targets)

    for t in top_targets:
        udid = t.get("udid")
        resolved = t.get("_pre_resolved_files") or resolve_ipfr_content_files(udid, prefer_test_files=prefer_test_files)
        if resolved.get("missing"):
            missing_any = True

        md_text = _read_text_file(resolved.get("markdown_path")) or ""
        chunks = parse_markdown_chunks(md_text)

        pass1_target_chunk_id = _select_pass1_target_chunk_id(packet, t, chunks)
        window = extract_chunk_window(chunks, pass1_target_chunk_id or (t.get("best_chunk_id") or ""))

        prompt1 = _build_llm_pass1_prompt(packet, t, window, target_chunk_id=pass1_target_chunk_id)
        pass1 = _call_llm_json(prompt1)

        decision = (pass1.get("decision") or "uncertain").strip().lower()
        confidence = (pass1.get("confidence") or "").strip().lower()
        reason = (pass1.get("reason") or "").strip()

        if decision not in ("impact", "no_impact", "uncertain"):
            decision = "uncertain"

        if not str(pass1.get("chunk_id") or "").strip() and pass1_target_chunk_id:
            pass1["chunk_id"] = pass1_target_chunk_id

        if decision == "uncertain":
            any_uncertain = True

        pass2 = None
        review_ids = []
        chunk_targets = []
        page_chunk_index = []

        if decision == "impact":
            page_chunk_index = build_chunk_index(chunks)
            chunk_targets = _build_chunk_verification_targets(packet, t, chunks)
            stage3_ids = t.get("relevant_chunk_ids") or []
            prompt2 = _build_llm_pass2_prompt(
                packet,
                t,
                pass1,
                chunk_targets,
                page_chunk_index=page_chunk_index,
                stage3_relevant_chunk_ids=stage3_ids,
            )
            pass2 = _call_llm_json(prompt2)

            pass2_failed = (
                isinstance(pass2, dict)
                and (pass2.get("decision") == "uncertain" or pass2.get("overall_decision") == "uncertain")
                and "LLM" in str(pass2.get("reason") or "")
            )
            if pass2_failed:
                logger.warning(
                    f"Pass 2 failed for {udid} (reason: {pass2.get('reason')}). "
                    f"Promoting Pass 1 suggested_review_chunk_ids to additional_chunks_to_review."
                )
                p1_review_ids = pass1.get("suggested_review_chunk_ids") or []
                pass2 = {
                    "decision": "uncertain",
                    "overall_decision": "uncertain",
                    "confidence": "low",
                    "reason": pass2.get("reason", "Pass 2 LLM call failed."),
                    "confirmed_updates": [],
                    "rejected_stage3_chunk_ids": [],
                    "additional_chunks_to_review": [
                        {
                            "chunk_id": cid,
                            "reason": "Pass 2 failed; promoted from Pass 1 suggested_review_chunk_ids for human review.",
                        }
                        for cid in p1_review_ids
                        if str(cid or "").strip()
                    ],
                    "_pass2_fallback": True,
                }

            assessed_chunk_ids = {
                canonical_chunk_id(item.get("chunk_id"))
                for item in chunk_targets
                if isinstance(item, dict) and item.get("chunk_id")
            }

            legacy = pass2.get("suggested_review_chunk_ids")
            if isinstance(legacy, list) and legacy:
                review_ids = [str(x) for x in legacy]
            else:
                confirmed_updates = _extract_confirmed_updates(
                    pass2,
                    fallback_chunk_id=(pass1.get("chunk_id") or pass1_target_chunk_id or t.get("best_chunk_id") or "") if not chunk_targets else ""
                )
                review_ids.extend([
                    str(item.get("chunk_id")).strip()
                    for item in confirmed_updates
                    if str(item.get("chunk_id") or "").strip()
                ])

                additional = _extract_additional_chunks_to_review(pass2)
                review_ids.extend([
                    str(item.get("chunk_id")).strip()
                    for item in additional
                    if str(item.get("chunk_id") or "").strip()
                ])

                rejected = []
                for cid in (pass2.get("rejected_stage3_chunk_ids") or []):
                    canon = canonical_chunk_id(cid)
                    if canon and canon in assessed_chunk_ids:
                        rejected.append(str(cid).strip())
                if isinstance(pass2, dict):
                    pass2["rejected_stage3_chunk_ids"] = rejected

                confirmed_ids = {canonical_chunk_id(x) for x in review_ids}
                rejected_ids = {canonical_chunk_id(x) for x in rejected}
                for target in chunk_targets:
                    cid = str(target.get("chunk_id") or "").strip()
                    canon = canonical_chunk_id(cid)
                    if cid and canon not in confirmed_ids and canon not in rejected_ids:
                        review_ids.append(cid)
                        confirmed_ids.add(canon)

            seen = set()
            review_ids = [x for x in review_ids if x and not (x in seen or seen.add(x))]

            verified_cid = (pass1.get("chunk_id") or pass1_target_chunk_id or t.get("best_chunk_id") or "").strip()
            if verified_cid and verified_cid not in review_ids and not chunk_targets:
                review_ids.insert(0, verified_cid)

            impacted_pages.append({
                "udid": udid,
                "chunk_id": (pass1.get("chunk_id") or pass1_target_chunk_id or t.get("best_chunk_id")),
                "confidence": confidence or "medium",
                "reason": reason or "External change impacts IPFR guidance.",
                "suggested_review_chunk_ids": review_ids
            })

        per_candidate_results.append({
            "udid": udid,
            "candidate_rank": t.get("candidate_rank"),
            "best_chunk_id": t.get("best_chunk_id"),
            "pass1_target_chunk_id": pass1_target_chunk_id,
            "matched_hunk_indices": t.get("matched_hunk_indices", []),
            "resolved_files": resolved,
            "chunk_count": len(chunks),
            "evidence_window": window,
            "chunk_verification_targets": chunk_targets,
            "page_chunk_index": page_chunk_index,
            "pass1_result": pass1,
            "pass2_result": pass2,
        })

    if impacted_pages:
        overall_decision = "impact"
    elif missing_any:
        overall_decision = "uncertain"
    elif any_uncertain:
        overall_decision = "uncertain"
    else:
        overall_decision = "no_impact"

    llm_result = {
        "overall_decision": overall_decision,
        "confidence": ("high" if impacted_pages else ("low" if any_uncertain else "high")),
        "impacted_pages": impacted_pages,
        "note": "Prototype: verified top N candidates only. Production will verify every candidate."
    }

    os.makedirs(config.LLM_VERIFY_DIR, exist_ok=True)
    out_path = os.path.join(config.LLM_VERIFY_DIR, f"verification_{packet.get('packet_id','packet')}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "packet_id": packet.get("packet_id"),
            "verified_at": datetime.datetime.now().isoformat(),
            "top_n_candidates_loaded": config.TOP_N_VERIFICATION_CANDIDATES,
            "llm_result": llm_result,
            "per_candidate": per_candidate_results,
            "skipped_unresolved_candidates": unresolved_targets
        }, f, indent=2, ensure_ascii=False)

    return out_path


def run_llm_verification_for_packets(
    handover_paths: List[str],
    prefer_test_files: bool = True,
    top_n_candidates: Optional[int] = None
) -> List[str]:
    """
    Runs LLM verification for each packet (prototype) and links results back into audit_log.csv.

    Args:
        handover_paths: List of handover packet JSON paths.
        prefer_test_files: If True, verification will prefer *_test.md fixtures when resolving IPFR archive pages.
        top_n_candidates: Optional override for TOP_N_VERIFICATION_CANDIDATES (used to limit candidates passed to LLM).
    Returns:
        List of verification result JSON paths created.
    """
    if top_n_candidates is not None:
        try:
            config.TOP_N_VERIFICATION_CANDIDATES = int(top_n_candidates)
        except Exception:
            logger.warning(f"Ignoring invalid top_n_candidates={top_n_candidates!r}")

    if not handover_paths:
        return []

    results: List[str] = []

    for packet_path in handover_paths:
        try:
            if not packet_path or not os.path.exists(packet_path):
                continue

            with open(packet_path, "r", encoding="utf-8") as f:
                packet = json.load(f)

            src = (packet.get("source_change_details", {}) or {}).get("source", {}) or {}
            source_name = src.get("name", "") or ""
            priority = src.get("monitoring_priority", "") or ""
            version_id = (packet.get("source_change_details", {}) or {}).get("version_id", "") or ""
            diff_file = (packet.get("source_change_details", {}) or {}).get("diff_file", "") or ""
            packet_id = packet.get("packet_id", "") or ""

            result_path = verify_handover_packet_with_llm(
                packet_path,
                prefer_test_files=prefer_test_files
            )
            if not result_path:
                continue

            verified_at = _now_iso()

            with open(result_path, "r", encoding="utf-8") as rf:
                verification_doc = json.load(rf)
            llm_res = (verification_doc.get("llm_result") or {})

            overall_decision = (llm_res.get("overall_decision") or "uncertain")
            confidence = (llm_res.get("confidence") or "")

            impacted = llm_res.get("impacted_pages") or []
            verified_udids = []
            verified_sections = []
            for it in impacted:
                if isinstance(it, dict):
                    u = it.get("udid")
                    if u:
                        verified_udids.append(u)
                    sid = it.get("section_identifier")
                    if sid:
                        verified_sections.append(f"{it.get('udid','')}: {sid}".strip())

            predicted_udids = []
            for t in (packet.get("llm_verification_targets") or []):
                if isinstance(t, dict) and t.get("udid"):
                    predicted_udids.append(t.get("udid"))

            metrics = _compute_overlap_metrics(predicted_udids, verified_udids)

            ai_decision_human = _decision_to_human(overall_decision)
            ai_conf_human = _confidence_to_human(confidence)

            change_summary = (llm_res.get("external_change_summary") or "").strip()
            if not change_summary:
                change_summary = "AI verification completed."

            human_review = "Yes" if ai_decision_human in ("Impact Confirmed", "Uncertain", "Error") else "No"

            updates = {
                "AI Verification Run": "Yes",
                "AI Verification Time": verified_at,
                "AI Model Used": config.LLM_MODEL,
                "AI Decision": ai_decision_human,
                "AI Confidence": ai_conf_human,
                "AI Change Summary": change_summary[:500],
                "AI Verification File": result_path,
                "Human Review Needed": human_review,

                "AI Verified Impact Pages": _list_to_semicolon(metrics["ver_set"]),
                "AI vs Similarity Overlap Score": f"{metrics['overlap']:.3f}" if metrics.get("overlap") is not None else "",
                "AI vs Similarity Precision": (f"{metrics['precision']:.3f}" if metrics.get("precision") is not None else "n/a"),
                "AI vs Similarity Recall": (f"{metrics['recall']:.3f}" if metrics.get("recall") is not None else "n/a"),
                "Overlap Details": metrics.get("details") or "",
            }

            updated = update_audit_row_by_key(
                source_name=source_name,
                version_id=version_id,
                diff_file=diff_file,
                updates=updates
            )
            if not updated:
                append_audit_row({
                    "Timestamp": verified_at,
                    "Source_Name": source_name,
                    "Priority": priority,
                    "Status": "Success",
                    "Change_Detected": "Yes",
                    "Version_ID": version_id,
                    "Diff_File": diff_file,
                    "Outcome": "verification_only",
                    "Reason": f"Audit row not found for packet {packet_id}. Linked verification appended.",
                    **updates
                })

            results.append(result_path)

        except Exception as e:
            logger.error(f"LLM verification failed for {packet_path}: {e}")

    return results


def _extract_confirmed_updates(pass2_result: Optional[dict], fallback_chunk_id: str = "") -> List[dict]:
    """Return deduplicated confirmed chunk records from a Pass 2 result."""
    if not isinstance(pass2_result, dict):
        return [{"chunk_id": fallback_chunk_id, "matched_hunk_ids": [], "reason": "", "evidence_quote": ""}] if fallback_chunk_id else []

    confirmed_updates = pass2_result.get("confirmed_updates") or []
    out: List[dict] = []

    if isinstance(confirmed_updates, list) and confirmed_updates:
        for item in confirmed_updates:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("chunk_id") or "").strip()
            if not cid:
                continue
            matched_hunk_ids = [
                int(x) for x in (item.get("matched_hunk_ids") or [])
                if isinstance(x, int) or (isinstance(x, str) and str(x).strip().isdigit())
            ]
            out.append({
                "chunk_id": cid,
                "matched_hunk_ids": matched_hunk_ids,
                "reason": str(item.get("reason") or "").strip(),
                "evidence_quote": str(item.get("evidence_quote") or "").strip(),
            })

    if out:
        seen = set()
        deduped = []
        for item in out:
            cid = item.get("chunk_id")
            if cid and cid not in seen:
                deduped.append(item)
                seen.add(cid)
        return deduped

    confirmed = pass2_result.get("confirmed_update_chunk_ids") or []
    if isinstance(confirmed, list):
        legacy = []
        seen = set()
        for cid in confirmed:
            cid = str(cid).strip()
            if cid and cid not in seen:
                legacy.append({
                    "chunk_id": cid,
                    "matched_hunk_ids": [],
                    "reason": "",
                    "evidence_quote": "",
                })
                seen.add(cid)
        if legacy:
            return legacy

    fallback_chunk_id = (fallback_chunk_id or "").strip()
    if fallback_chunk_id:
        return [{
            "chunk_id": fallback_chunk_id,
            "matched_hunk_ids": [],
            "reason": "",
            "evidence_quote": "",
        }]

    return []


def _extract_confirmed_update_chunk_ids(pass2_result: Optional[dict], fallback_chunk_id: str = "") -> List[str]:
    """Backward-compatible list-only accessor for confirmed chunk ids."""
    return [item.get("chunk_id", "") for item in _extract_confirmed_updates(pass2_result, fallback_chunk_id) if item.get("chunk_id")]


def _extract_additional_chunks_to_review(pass2_result: Optional[dict]) -> List[dict]:
    out: List[dict] = []
    if not isinstance(pass2_result, dict):
        return out
    additional = pass2_result.get("additional_chunks_to_review") or []
    if not isinstance(additional, list):
        return out

    seen = set()
    for item in additional:
        if isinstance(item, dict) and item.get("chunk_id"):
            cid = str(item.get("chunk_id")).strip()
            if not cid or cid in seen:
                continue
            matched_hunk_ids = [
                int(x) for x in (item.get("matched_hunk_ids") or [])
                if isinstance(x, int) or (isinstance(x, str) and str(x).strip().isdigit())
            ]
            out.append({
                "chunk_id": cid,
                "matched_hunk_ids": matched_hunk_ids,
                "reason": str(item.get("reason") or "").strip(),
                "evidence_quote": str(item.get("evidence_quote") or "").strip(),
            })
            seen.add(cid)
        elif item:
            cid = str(item).strip()
            if cid and cid not in seen:
                out.append({"chunk_id": cid, "matched_hunk_ids": [], "reason": "", "evidence_quote": ""})
                seen.add(cid)
    return out


def _extract_pass1_confirmed_chunk_ids(pass1_result: Optional[dict], fallback_chunk_id: str = "") -> List[str]:
    """Return deduplicated Pass 1 confirmed chunk ids from a Pass 1 result."""
    out: List[str] = []
    if isinstance(pass1_result, dict):
        explicit = pass1_result.get("pass1_confirmed_chunk_ids") or []
        if isinstance(explicit, list):
            out.extend([str(x).strip() for x in explicit if str(x).strip()])

        decision = str(pass1_result.get("decision") or "").strip().lower()
        inferred_chunk_id = str(pass1_result.get("chunk_id") or "").strip()
        if decision == "impact" and inferred_chunk_id:
            out.append(inferred_chunk_id)

    fallback_chunk_id = (fallback_chunk_id or "").strip()
    if fallback_chunk_id and not out and isinstance(pass1_result, dict):
        decision = str(pass1_result.get("decision") or "").strip().lower()
        if decision == "impact":
            out.append(fallback_chunk_id)

    seen = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def summarise_verification_file(verification_path: str) -> dict:
    """Summarise a Stage 4 verification artifact using production parsing rules."""
    if not verification_path or not os.path.exists(verification_path):
        return {
            "verification_file": verification_path,
            "verified_udids": [],
            "pass1_confirmed_chunk_ids": [],
            "confirmed_update_chunk_ids_pass2": [],
            "additional_review_chunk_ids": [],
            "per_candidate_summary": [],
        }

    with open(verification_path, "r", encoding="utf-8") as f:
        doc = json.load(f)

    verified_udids: List[str] = []
    pass1_confirmed_chunk_ids: List[str] = []
    confirmed_update_chunk_ids_pass2: List[str] = []
    additional_review_chunk_ids: List[str] = []
    per_candidate_summary: List[dict] = []

    impacted = (doc.get("llm_result") or {}).get("impacted_pages") or []
    for page in impacted:
        if isinstance(page, dict) and page.get("udid"):
            verified_udids.append(str(page.get("udid")))

    for cand in doc.get("per_candidate", []) or []:
        if not isinstance(cand, dict):
            continue
        pass1 = cand.get("pass1_result") or {}
        pass2 = cand.get("pass2_result") or {}
        best_chunk_id = str(cand.get("best_chunk_id") or "").strip()
        udid = str(cand.get("udid") or "").strip()

        pass1_ids = _extract_pass1_confirmed_chunk_ids(pass1, best_chunk_id)
        pass2_ids = _extract_confirmed_update_chunk_ids(pass2, best_chunk_id if pass1_ids else "")
        review_items = _extract_additional_chunks_to_review(pass2)
        review_ids = [item.get("chunk_id") for item in review_items if item.get("chunk_id")]

        pass1_confirmed_chunk_ids.extend(pass1_ids)
        confirmed_update_chunk_ids_pass2.extend(pass2_ids)
        additional_review_chunk_ids.extend(review_ids)

        per_candidate_summary.append({
            "udid": udid,
            "best_chunk_id": best_chunk_id,
            "pass1_decision": str(pass1.get("decision") or ""),
            "pass1_chunks": pass1_ids,
            "pass2_chunks": pass2_ids,
            "additional_review_chunks": review_ids,
        })

    def _dedupe_preserve(items: List[str]) -> List[str]:
        seen = set()
        return [x for x in items if x and not (x in seen or seen.add(x))]

    return {
        "verification_file": verification_path,
        "verified_udids": _dedupe_preserve(verified_udids),
        "pass1_confirmed_chunk_ids": _dedupe_preserve(pass1_confirmed_chunk_ids),
        "confirmed_update_chunk_ids_pass2": _dedupe_preserve(confirmed_update_chunk_ids_pass2),
        "additional_review_chunk_ids": _dedupe_preserve(additional_review_chunk_ids),
        "per_candidate_summary": per_candidate_summary,
    }


def summarise_verification_files(verification_paths: List[str]) -> dict:
    """Aggregate verification summaries across multiple Stage 4 artifacts."""
    verified_udids: List[str] = []
    pass1_confirmed_chunk_ids: List[str] = []
    confirmed_update_chunk_ids_pass2: List[str] = []
    additional_review_chunk_ids: List[str] = []
    per_candidate_summary: List[dict] = []

    file_summaries = []
    for path in verification_paths or []:
        summary = summarise_verification_file(path)
        file_summaries.append(summary)
        verified_udids.extend(summary.get("verified_udids") or [])
        pass1_confirmed_chunk_ids.extend(summary.get("pass1_confirmed_chunk_ids") or [])
        confirmed_update_chunk_ids_pass2.extend(summary.get("confirmed_update_chunk_ids_pass2") or [])
        additional_review_chunk_ids.extend(summary.get("additional_review_chunk_ids") or [])
        per_candidate_summary.extend(summary.get("per_candidate_summary") or [])

    def _dedupe_preserve(items: List[str]) -> List[str]:
        seen = set()
        return [x for x in items if x and not (x in seen or seen.add(x))]

    return {
        "file_summaries": file_summaries,
        "verified_udids": _dedupe_preserve(verified_udids),
        "pass1_confirmed_chunk_ids": _dedupe_preserve(pass1_confirmed_chunk_ids),
        "confirmed_update_chunk_ids_pass2": _dedupe_preserve(confirmed_update_chunk_ids_pass2),
        "additional_review_chunk_ids": _dedupe_preserve(additional_review_chunk_ids),
        "per_candidate_summary": per_candidate_summary,
    }
