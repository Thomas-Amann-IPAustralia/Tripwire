import json
import os
import re
from typing import Dict, List, Optional, Tuple

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from . import config
from .llm_client import get_openai_client
from .stage2_diff import extract_change_content

_semantic_cache = None


def detect_power_words(text):
    tiers = {
        'strong': [
            r'\bmust\b', r'\bshall\b', r'\bpenalt(?:y|ies)\b', r'\bfines?\b',
            r'\$\d+(?:,\d+)*', r'\bprohibited\b', r'\bmandatory\b', r'\brequired\b',
            r'\bobligation\b', r'archives\s+act\s+1983'
        ],
        'moderate': [
            r'\bwithin\b', r'\bdeadline\b', r'\bdeadlines\b', r'\bnotice\b',
            r'\bservice\b', r'\bevidence\b', r'\bdeclaration\b'
        ],
        'weak': [
            r'\bmay\b', r'\d+\s*days?\b'
        ]
    }
    text_l = (text or '').lower()
    by_tier = {'strong': [], 'moderate': [], 'weak': []}
    for tier, patterns in tiers.items():
        for pat in patterns:
            matches = re.findall(pat, text_l, re.IGNORECASE)
            for m in matches:
                if m not in by_tier[tier]:
                    by_tier[tier].append(m)

    found = by_tier['strong'] + by_tier['moderate'] + by_tier['weak']
    strong_count = len(by_tier['strong'])
    moderate_count = len(by_tier['moderate'])
    weak_count = len(by_tier['weak'])
    weak_only = weak_count > 0 and strong_count == 0 and moderate_count == 0
    raw_score = min(0.35, strong_count * 0.08 + moderate_count * 0.04 + weak_count * 0.02)

    return {
        'found': found,
        'power_words_found': found,
        'by_tier': by_tier,
        'strong_count': strong_count,
        'moderate_count': moderate_count,
        'weak_count': weak_count,
        'count': len(found),
        'weak_only': weak_only,
        'score': raw_score
    }


def calculate_final_score(page_base_similarity, power_word_analysis):
    """
    Returns an adjusted similarity score (similarity + uplift), capped at 1.0.
    """
    if isinstance(power_word_analysis, dict):
        boost = float(power_word_analysis.get('score', 0.0))
        weak_only = bool(power_word_analysis.get('weak_only', False))
        strong_count = int(power_word_analysis.get('strong_count', 0))

        # Avoid boosting very-low similarity matches purely on weak words.
        if weak_only and float(page_base_similarity) < 0.20:
            boost = 0.0
        elif strong_count == 0 and float(page_base_similarity) < 0.10:
            boost = min(boost, 0.02)
    else:
        boost = float(power_word_analysis or 0.0)

    return min(1.0, float(page_base_similarity) + boost)


def get_primary_handover_threshold_for_priority(priority: str) -> Optional[float]:
    p = (priority or '').strip().lower()
    if p == 'high':
        return None  # bypass primary-score gate for high-priority sources
    if p == 'medium':
        return config.MEDIUM_PRIMARY_HANDOVER_THRESHOLD
    return config.LOW_PRIMARY_HANDOVER_THRESHOLD


def should_generate_handover(primary_score: float,
                             candidate_count: int,
                             source_priority: str) -> Tuple[bool, str, Optional[float]]:
    """
    Handover policy:
      - High-priority sources: hand over if any threshold-passing candidates exist.
      - Medium/Low: require primary score to pass a priority-specific threshold.
    """
    if candidate_count <= 0:
        return False, "No candidates passed candidate_min_score", get_primary_handover_threshold_for_priority(source_priority)

    threshold = get_primary_handover_threshold_for_priority(source_priority)
    p = (source_priority or '').strip().lower()

    if p == 'high':
        return True, "High priority source: handover triggered when threshold-passing candidates exist", None

    if threshold is None:
        return True, "No primary handover threshold configured", None

    ok = float(primary_score) >= float(threshold)
    return ok, (
        f"Primary score {primary_score:.3f} {'>=' if ok else '<'} "
        f"{p or 'default'} threshold {threshold:.3f}"
    ), threshold


def _load_semantic_embeddings(mock_semantic_data=None):
    global _semantic_cache
    if mock_semantic_data:
        vectors = np.array(mock_semantic_data['embeddings'])
        udids = mock_semantic_data['udids']
        chunk_texts = mock_semantic_data.get('chunk_texts', [''] * len(udids))
        chunks_raw = mock_semantic_data.get('chunks_raw')
        if chunks_raw is None:
            chunks_raw = []
            for i, udid in enumerate(udids):
                chunks_raw.append({
                    'UDID': udid,
                    'Chunk_ID': f"{udid}-C{i+1:02d}",
                    'Chunk_Text': chunk_texts[i] if i < len(udids) else '',
                    'Headline_Alt': ''
                })
        return vectors, udids, chunk_texts, chunks_raw

    if _semantic_cache is None:
        if not os.path.exists(config.SEMANTIC_EMBEDDINGS_FILE):
            raise FileNotFoundError(f"Semantic embeddings file not found: {config.SEMANTIC_EMBEDDINGS_FILE}")
        with open(config.SEMANTIC_EMBEDDINGS_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)

        vectors = []
        for item in raw:
            emb = item.get('Chunk_Embedding')
            if isinstance(emb, str):
                emb = json.loads(emb)
            vectors.append(emb)

        _semantic_cache = {
            'vectors': np.array(vectors),
            'udids': [item.get('UDID', 'N/A') for item in raw],
            'chunk_texts': [item.get('Chunk_Text', '') for item in raw],
            'chunks_raw': raw
        }
        config.logger.info(f"Loaded {len(raw)} semantic chunks from {config.SEMANTIC_EMBEDDINGS_FILE}")

    return _semantic_cache['vectors'], _semantic_cache['udids'], _semantic_cache['chunk_texts'], _semantic_cache['chunks_raw']


def _embed_texts(texts: List[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, 1536))
    client = get_openai_client()
    response = client.embeddings.create(input=texts, model=config.SEMANTIC_MODEL)
    return np.array([d.embedding for d in response.data])


def _priority_to_source_weight(priority: str) -> float:
    p = (priority or '').strip().lower()
    if p == 'high':
        return 1.0
    if p == 'medium':
        return 0.6
    return 0.3


def _is_administrative_noise(text: str) -> bool:
    """Returns True if the text is purely administrative (Page X, Dates, etc)."""
    t = text.strip().lower()
    if len(t) < 5:
        return True
    if re.match(r'^page \d+( of \d+)?$', t):
        return True
    if re.match(r'^\d{1,2} [a-z]+ \d{4}$', t):
        return True
    return False


def calculate_similarity(diff_path, source_priority='Low', mock_semantic_data=None):
    """
    Recall-first candidate retrieval with Administrative Noise filtering.
    """
    change = extract_change_content(diff_path)
    diff_hunks = change.get('hunks', [])

    if not diff_hunks:
        return {
            'status': 'no_content',
            'change_text': '',
            'change_hunks': [],
            'power_words': detect_power_words(''),
            'page_base_similarity': 0.0,
            'page_final_score': 0.0,
            'candidate_min_score': config.CANDIDATE_MIN_SCORE,
            'threshold_passing_candidates': [],
            'impacted_pages': [],
            'candidate_count': 0,
            'multi_impact_likely': False,
            'should_handover': False,
            'handover_decision_reason': "No substantive hunks parsed",
            'primary_handover_threshold_used': get_primary_handover_threshold_for_priority(source_priority),
        }

    overall_power = detect_power_words(change.get('change_context', ''))

    substantive_hunks = []
    for h in diff_hunks:
        ctx = h.get('change_context', '')
        if _is_administrative_noise(ctx):
            h['is_noise'] = True
            h['power_words'] = {'found': [], 'chunk_similarity': 0.0, 'strong_count': 0, 'power_words_found': []}
            continue

        h['is_noise'] = False
        h['power_words'] = detect_power_words(ctx)
        substantive_hunks.append(h)

    if not substantive_hunks:
        return {
            'status': 'success',
            'change_text': change.get('change_context', ''),
            'change_hunks': [
                {
                    'hunk_index': h['hunk_index'],
                    'hunk_header': h.get('header', ''),
                    'hunk_text': h.get('change_context', ''),
                    'is_noise': True,
                    # Structured fields for downstream packet formatting:
                    'removed': h.get('removed_lines', []),
                    'added': h.get('added_lines', [])
                }
                for h in diff_hunks
            ],
            'power_words': overall_power,
            'page_base_similarity': 0.0,
            'page_final_score': 0.0,
            'candidate_count': 0,
            'should_handover': False,
            'handover_decision_reason': "All changes identified as administrative noise",
            'threshold_passing_candidates': [],
            'impacted_pages': []
        }

    try:
        hunk_vectors = _embed_texts([h['change_context'] for h in substantive_hunks])
    except Exception as e:
        return {
            'status': 'error',
            'message': str(e),
            'change_text': change.get('change_context', ''),
            'change_hunks': [],
            'power_words': overall_power,
            'impacted_pages': [],
            'threshold_passing_candidates': [],
            'candidate_count': 0,
            'multi_impact_likely': False,
            'should_handover': False,
            'handover_decision_reason': f"Embedding error: {e}"
        }

    try:
        corpus_vectors, udids, chunk_texts, chunks_raw = _load_semantic_embeddings(
            mock_semantic_data=mock_semantic_data
        )
        _ = chunk_texts
    except Exception as e:
        return {
            'status': 'missing_embeddings' if isinstance(e, FileNotFoundError) else 'similarity_error',
            'message': str(e),
            'change_text': change.get('change_context', ''),
            'change_hunks': [],
            'power_words': overall_power,
            'impacted_pages': [],
            'threshold_passing_candidates': [],
            'candidate_count': 0,
            'multi_impact_likely': False,
            'should_handover': False,
            'handover_decision_reason': str(e)
        }

    similarity_matrix = cosine_similarity(hunk_vectors, corpus_vectors)

    hunk_matches = []
    page_evidence: Dict[str, dict] = {}

    for hunk_row_idx, hunk in enumerate(substantive_hunks):
        chunk_similarities = similarity_matrix[hunk_row_idx]
        passing_chunk_indices = np.where(chunk_similarities >= config.HUNK_CHUNK_MIN_SIMILARITY)[0].tolist()

        diagnostic_indices = passing_chunk_indices.copy()
        if not diagnostic_indices and chunk_similarities.size > 0:
            diagnostic_indices = [int(np.argmax(chunk_similarities))]

        chunk_match_summaries = []

        for chunk_idx in diagnostic_indices:
            chunk_similarity = float(chunk_similarities[chunk_idx])
            chunk_meta = chunks_raw[chunk_idx] if chunks_raw and chunk_idx < len(chunks_raw) else {}
            page_udid = (udids[chunk_idx] if chunk_idx < len(udids) else chunk_meta.get('UDID')) or 'N/A'
            chunk_id = chunk_meta.get('Chunk_ID') or f"{page_udid}-UNK-{chunk_idx}"
            headline = chunk_meta.get('Headline_Alt') or chunk_meta.get('Page_Title') or ''

            passes = chunk_similarity >= config.HUNK_CHUNK_MIN_SIMILARITY
            chunk_match_summaries.append({
                'udid': page_udid,
                'chunk_id': chunk_id,
                'chunk_similarity': chunk_similarity,
                'headline_alt': headline,
                'passes_chunk_threshold': passes
            })

            if not passes:
                continue

            page_rec = page_evidence.setdefault(page_udid, {
                'udid': page_udid,
                'chunk_hits': 0,
                'matched_hunks': set(),
                'chunk_id_set': set(),
                'chunk_ids': [],
                'best_chunk_id': chunk_id,
                'best_headline': headline,
                'page_base_similarity': 0.0,
                'per_chunk': {},
            })

            page_rec['chunk_hits'] += 1
            page_rec['matched_hunks'].add(hunk['hunk_index'])

            if chunk_id not in page_rec['chunk_id_set']:
                page_rec['chunk_id_set'].add(chunk_id)
                page_rec['chunk_ids'].append(chunk_id)

            chunk_rec = page_rec['per_chunk'].setdefault(chunk_id, {
                'chunk_id': chunk_id,
                'headline': headline,
                'max_similarity': 0.0,
                'sum_similarity': 0.0,
                'matched_hunks': set(),
            })
            chunk_rec['headline'] = headline or chunk_rec.get('headline') or ''
            chunk_rec['max_similarity'] = max(chunk_rec['max_similarity'], chunk_similarity)
            chunk_rec['sum_similarity'] += chunk_similarity
            chunk_rec['matched_hunks'].add(hunk['hunk_index'])

        hunk_matches.append({
            'hunk_index': hunk['hunk_index'],
            'hunk_header': hunk.get('header', ''),
            'change_text': hunk.get('change_context', ''),
            'power_words_found': hunk.get('power_words', {}).get('power_words_found', []),
            'top_chunks': sorted(chunk_match_summaries, key=lambda x: x['chunk_similarity'], reverse=True)[:5]
        })

    impacted_pages = []
    for page_udid, page_rec in page_evidence.items():
        per_chunk = page_rec.get('per_chunk') or {}
        if per_chunk:
            best_chunk = max(
                per_chunk.values(),
                key=lambda c: (
                    len(c.get('matched_hunks') or []),
                    float(c.get('sum_similarity') or 0.0),
                    float(c.get('max_similarity') or 0.0),
                )
            )
            page_rec['best_chunk_id'] = best_chunk.get('chunk_id') or page_rec.get('best_chunk_id')
            page_rec['best_headline'] = best_chunk.get('headline') or page_rec.get('best_headline') or ''
            page_rec['page_base_similarity'] = float(best_chunk.get('max_similarity') or 0.0)

        distinct_hunks = len(page_rec['matched_hunks'])
        coverage_bonus = min(
            config.MAX_PAGE_COVERAGE_BONUS,
            max(0, distinct_hunks - 1) * config.PAGE_HUNK_COVERAGE_BONUS
        )
        density_bonus = min(
            config.MAX_PAGE_DENSITY_BONUS,
            max(0, page_rec['chunk_hits'] - 1) * config.PAGE_CHUNK_DENSITY_BONUS
        )

        power_adjusted = calculate_final_score(page_rec['page_base_similarity'], overall_power)
        power_uplift = max(0.0, power_adjusted - page_rec['page_base_similarity'])

        final_score = min(
            1.0,
            page_rec['page_base_similarity'] + coverage_bonus + density_bonus + power_uplift
        )

        impacted_pages.append({
            'udid': page_udid,
            'aggregated_page_base_similarity': float(page_rec['page_base_similarity']),
            'page_final_score': float(final_score),
            'chunk_hits': page_rec['chunk_hits'],
            'distinct_hunk_hits': distinct_hunks,
            'matched_hunk_indices': sorted(page_rec['matched_hunks']),
            'relevant_chunk_ids': page_rec['chunk_ids'][:config.MAX_RELEVANT_CHUNK_IDS_PER_CANDIDATE],
            'best_chunk_id': page_rec['best_chunk_id'],
            'best_headline': page_rec['best_headline'],
            'coverage_bonus': float(coverage_bonus),
            'density_bonus': float(density_bonus),
            'power_uplift': float(power_uplift),
        })

    impacted_pages.sort(key=lambda p: (p['page_final_score'], p['distinct_hunk_hits']), reverse=True)

    for rank, p in enumerate(impacted_pages, start=1):
        p['candidate_rank'] = rank

    threshold_passing_candidates = [
        p for p in impacted_pages if p['page_final_score'] >= config.CANDIDATE_MIN_SCORE
    ]

    primary = impacted_pages[0] if impacted_pages else None
    primary_page_final_score = float(primary['page_final_score']) if primary else 0.0
    candidate_count = len(threshold_passing_candidates)

    should_handover_result, handover_reason, primary_threshold_used = should_generate_handover(
        primary_score=primary_page_final_score,
        candidate_count=candidate_count,
        source_priority=source_priority
    )

    # NOTE: Preserve structured hunk info (removed/added arrays) for packet formatting.
    # This includes ALL hunks (noise + substantive), aligned with existing UI needs.
    change_hunks_structured = []
    for h in diff_hunks:
        change_hunks_structured.append({
            'hunk_index': h['hunk_index'],
            'hunk_header': h.get('header', ''),
            'hunk_text': h.get('change_context', ''),
            'is_noise': h.get('is_noise', False),
            'power_words_found': h.get('power_words', {}).get('power_words_found', []),
            'removed': h.get('removed_lines', []),
            'added': h.get('added_lines', []),
        })

    return {
        'status': 'success',
        'change_text': change.get('change_context', ''),
        'change_hunks': change_hunks_structured,
        'power_words': overall_power,
        'page_base_similarity': float(primary['aggregated_page_base_similarity']) if primary else 0.0,
        'page_final_score': primary_page_final_score,
        'primary_udid': primary['udid'] if primary else None,
        'primary_chunk_id': primary.get('best_chunk_id') if primary else None,
        'primary_headline': primary.get('best_headline') if primary else None,
        'hunk_matches': hunk_matches,
        'threshold_passing_candidates': threshold_passing_candidates,
        'impacted_pages': impacted_pages,
        'candidate_count': candidate_count,
        'multi_impact_likely': candidate_count > 1,
        'should_handover': should_handover_result,
        'handover_decision_reason': handover_reason,
        'filter_reason': None if should_handover_result else handover_reason,
        'primary_handover_threshold_used': primary_threshold_used,
        'candidate_min_score': config.CANDIDATE_MIN_SCORE,
        'hunk_chunk_min_similarity': config.HUNK_CHUNK_MIN_SIMILARITY
    }
