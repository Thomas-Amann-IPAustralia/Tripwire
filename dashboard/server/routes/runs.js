import { Router } from 'express';
import fs from 'fs';
import path from 'path';
import { db, dbGuard, FEEDBACK_PATH, REPO_ROOT } from '../db.js';

const SNAPSHOTS_SUBDIR = 'data/influencer_sources/snapshots';
const MAX_DIFF_CHARS = 200_000;

// The pipeline records where it wrote the diff, not the diff text itself,
// and the recorded path is absolute to the GitHub Actions runner. Re-root
// the trailing data/... segment onto this deployment's data directory.
function readDiffFile(diffPath) {
  if (!diffPath || typeof diffPath !== 'string') return null;
  const idx = diffPath.indexOf(SNAPSHOTS_SUBDIR);
  if (idx === -1) return null;

  const resolved = path.resolve(REPO_ROOT, diffPath.slice(idx));
  // Containment check — the recorded path is data, not something we trust.
  if (!resolved.startsWith(path.resolve(REPO_ROOT, SNAPSHOTS_SUBDIR) + path.sep)) return null;

  try {
    const text = fs.readFileSync(resolved, 'utf8');
    return text.length > MAX_DIFF_CHARS
      ? text.slice(0, MAX_DIFF_CHARS) + '\n… [diff truncated]'
      : text;
  } catch {
    return null; // file not synced to this deployment — show nothing rather than fail
  }
}

const router = Router();

const STAGE_REACHED_CASE = `
  CASE stage_reached
    WHEN 'stage1'          THEN 1
    WHEN 'scrape'          THEN 1
    WHEN 'stage2'          THEN 2
    WHEN 'stage3'          THEN 3
    WHEN 'stage4'          THEN 4
    WHEN 'stage5'          THEN 5
    WHEN 'stage6'          THEN 6
    WHEN 'stage6_complete' THEN 6
    ELSE 0
  END
`;

// LLM assessments moved out of pipeline_runs.details into their own table,
// keyed by (run_id, ipfr_page_id). A pipeline_runs row (one source in one run)
// can trigger several IPFR pages, so we surface the most severe verdict among
// the pages that source triggered as the representative verdict for the row.
const BEST_ASSESSMENT_ID = `(
  SELECT la2.id FROM llm_assessments la2
  WHERE la2.run_id = pipeline_runs.run_id
    AND pipeline_runs.triggered_pages LIKE '%' || la2.ipfr_page_id || '%'
  ORDER BY CASE la2.verdict
      WHEN 'CHANGE_REQUIRED' THEN 3
      WHEN 'UNCERTAIN'       THEN 2
      WHEN 'NO_CHANGE'       THEN 1
      ELSE 0
    END DESC, la2.confidence DESC
  LIMIT 1
)`;

const BASE_SELECT = `
  SELECT
    pipeline_runs.id AS id, pipeline_runs.run_id AS run_id,
    source_id, source_url, source_type, timestamp,
    ${STAGE_REACHED_CASE} AS stage_reached,
    outcome, triggered_pages, duration_seconds,
    la.verdict            AS verdict,
    la.confidence         AS confidence,
    la.reasoning          AS reasoning,
    la.suggested_changes  AS suggested_changes_json,
    json_extract(details, '$.stages.diff.diff_text')                   AS diff_text,
    json_extract(details, '$.stages.diff.diff_path')                   AS diff_path,
    json_extract(details, '$.stages.biencoder.candidate_pages[0].max_chunk_score') AS biencoder_max,
    json_extract(details, '$.stages.crossencoder.scored_pages[0].crossencoder_score') AS crossencoder_score,
    json_extract(details, '$.stages.crossencoder.scored_pages[0].reranked_score')     AS reranked_score,
    COALESCE(
      json_extract(details, '$.stages.relevance.rrf_score'),
      json_extract(details, '$.stages.relevance.top_candidates[0].final_score')
    ) AS rrf_score,
    json_extract(details, '$.stages.relevance.source_importance')     AS source_importance,
    json_extract(details, '$.stages.relevance.fast_pass_triggered')   AS fast_pass_triggered,
    json_extract(details, '$.stages.change_detection.significance')   AS significance,
    COALESCE(
      json_extract(details, '$.stages.crossencoder.scored_pages[0].page_id'),
      json_extract(details, '$.stages.relevance.top_candidates[0].page_id')
    ) AS ipfr_page_id,
    COALESCE(
      json_extract(details, '$.graph_propagated'),
      json_extract(details, '$.stages.crossencoder.graph_propagated') > 0
    ) AS graph_propagated
  FROM pipeline_runs
  LEFT JOIN llm_assessments la ON la.id = ${BEST_ASSESSMENT_ID}
`;

// lite mode drops the large text fields (diff text, LLM prose) so list views
// can fetch the full run history without a multi-megabyte payload.
function formatRun(row, { lite = false } = {}) {
  if (lite) {
    return {
      id: row.id,
      run_id: row.run_id,
      source_id: row.source_id,
      source_type: row.source_type,
      timestamp: row.timestamp,
      stage_reached: row.stage_reached,
      outcome: row.outcome,
      triggered_pages: safeParseJson(row.triggered_pages, []),
      duration_seconds: row.duration_seconds ?? null,
      verdict: row.verdict ?? null,
      confidence: row.confidence ?? null,
      ipfr_page_id: row.ipfr_page_id ?? null,
      biencoder_max: row.biencoder_max ?? null,
      crossencoder_score: row.crossencoder_score ?? null,
      reranked_score: row.reranked_score ?? null,
      significance: row.significance ?? null,
      fast_pass_triggered: row.fast_pass_triggered ?? false,
      graph_propagated: row.graph_propagated ?? false,
      scores: {
        rrf_score: row.rrf_score ?? null,
        source_importance: row.source_importance ?? null,
      },
    };
  }
  return {
    id: row.id,
    run_id: row.run_id,
    source_id: row.source_id,
    source_url: row.source_url,
    source_type: row.source_type,
    timestamp: row.timestamp,
    stage_reached: row.stage_reached,
    outcome: row.outcome,
    triggered_pages: safeParseJson(row.triggered_pages, []),
    duration_seconds: row.duration_seconds ?? null,
    verdict: row.verdict ?? null,
    confidence: row.confidence ?? null,
    reasoning: row.reasoning ?? null,
    suggested_changes: safeParseJson(row.suggested_changes_json, null),
    diff_text: row.diff_text ?? null,
    ipfr_page_id: row.ipfr_page_id ?? null,
    biencoder_max: row.biencoder_max ?? null,
    crossencoder_score: row.crossencoder_score ?? null,
    reranked_score: row.reranked_score ?? null,
    significance: row.significance ?? null,
    fast_pass_triggered: row.fast_pass_triggered ?? false,
    graph_propagated: row.graph_propagated ?? false,
    scores: {
      rrf_score: row.rrf_score ?? null,
      source_importance: row.source_importance ?? null,
    },
  };
}

function safeParseJson(val, fallback) {
  if (val == null) return fallback;
  if (typeof val === 'object') return val;
  try { return JSON.parse(val); } catch { return fallback; }
}

// GET /api/runs
router.get('/', (req, res) => {
  if (!dbGuard(res)) return;

  const { from, to, outcome, stage_reached_min, limit = 1000, offset = 0, fields } = req.query;
  const lite = fields === 'lite';

  const { conditions, params } = buildRunFilters(req.query);

  if (outcome) { conditions.push('outcome = ?'); params.push(outcome); }

  if (stage_reached_min) {
    conditions.push(`(${STAGE_REACHED_CASE}) >= ?`);
    params.push(Number(stage_reached_min));
  }

  const where = conditions.length ? `WHERE ${conditions.join(' AND ')}` : '';
  const sql = `${BASE_SELECT} ${where} ORDER BY timestamp DESC LIMIT ? OFFSET ?`;
  // Cap at 50k as a safety valve; lite mode exists so clients can fetch
  // the full history without truncation at the old 1000-row default.
  params.push(Math.min(Number(limit) || 1000, 50_000), Number(offset));

  try {
    const rows = db.prepare(sql).all(...params);
    const data = rows.map(row => formatRun(row, { lite }));
    res.json({ data, total: data.length, limit: Number(limit), offset: Number(offset) });
  } catch (err) {
    console.error('[runs] GET /:', err.message);
    res.status(500).json({ data: [], error: err.message });
  }
});

// Shared WHERE-clause builder for the list and summary endpoints, so both
// honour the same from/to/source_id/verdict filters. 'ERROR' is not an LLM
// verdict — treat it as outcome = 'error' so the FilterBar chip works.
function buildRunFilters(query) {
  const { from, to } = query;
  const sourceIds   = [query.source_id].flat().filter(Boolean);
  const allVerdicts = [query.verdict].flat().filter(Boolean);
  const wantsError  = allVerdicts.includes('ERROR');
  const verdicts    = allVerdicts.filter(v => v !== 'ERROR');

  const conditions = [];
  const params = [];

  if (from) { conditions.push('timestamp >= ?'); params.push(from); }
  if (to)   { conditions.push('timestamp <= ?'); params.push(to); }

  if (sourceIds.length === 1) {
    conditions.push('source_id = ?');
    params.push(sourceIds[0]);
  } else if (sourceIds.length > 1) {
    conditions.push(`source_id IN (${sourceIds.map(() => '?').join(',')})`);
    params.push(...sourceIds);
  }

  if (verdicts.length || wantsError) {
    const parts = [];
    if (verdicts.length) {
      parts.push(`la.verdict IN (${verdicts.map(() => '?').join(',')})`);
      params.push(...verdicts);
    }
    if (wantsError) parts.push(`outcome = 'error'`);
    conditions.push(parts.length > 1 ? `(${parts.join(' OR ')})` : parts[0]);
  }

  return { conditions, params };
}

// GET /api/runs/feedback — must come before /:run_id
router.get('/feedback', (req, res) => {
  try {
    const lines = fs.existsSync(FEEDBACK_PATH)
      ? fs.readFileSync(FEEDBACK_PATH, 'utf8').split('\n').filter(Boolean)
      : [];

    const feedbackRecords = lines.map(l => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);

    if (!db) {
      return res.json({ data: feedbackRecords });
    }

    const data = feedbackRecords.map(fb => {
      let runRecord = null;
      try {
        const row = db.prepare(`${BASE_SELECT} WHERE pipeline_runs.run_id = ? AND source_id = ?`).get(fb.run_id, fb.source_id);
        if (row) runRecord = formatRun(row);
      } catch { /* ignore */ }
      return { ...fb, run: runRecord };
    });

    res.json({ data });
  } catch (err) {
    console.error('[runs] GET /feedback:', err.message);
    res.status(500).json({ data: [], error: err.message });
  }
});

// GET /api/runs/summary — aggregates over ALL matching rows server-side, so
// funnel counts stay accurate no matter how large the run history grows
// (the row-level /api/runs endpoint is limited/paginated).
router.get('/summary', (req, res) => {
  if (!dbGuard(res)) return;

  const { conditions, params } = buildRunFilters(req.query);
  const where = conditions.length ? `WHERE ${conditions.join(' AND ')}` : '';
  // The verdict filter references the llm_assessments join alias; only pay
  // for the join when that filter is actually present.
  const needsJoin = where.includes('la.verdict');
  const joinClause = needsJoin ? `LEFT JOIN llm_assessments la ON la.id = ${BEST_ASSESSMENT_ID}` : '';

  try {
    const rows = db.prepare(`
      SELECT
        ${STAGE_REACHED_CASE} AS stage_int,
        outcome,
        COUNT(*) AS cnt
      FROM pipeline_runs
      ${joinClause}
      ${where}
      GROUP BY stage_int, outcome
    `).all(...params);

    const summary = {};
    for (let s = 1; s <= 6; s++) {
      summary[s] = { stage: s, total: 0, completed: 0, no_change: 0, error: 0 };
    }

    let totalRuns = 0;
    for (const row of rows) {
      const s = row.stage_int;
      if (s < 1 || s > 6) continue;
      totalRuns += row.cnt;
      summary[s].total += row.cnt;
      if (row.outcome === 'completed') summary[s].completed += row.cnt;
      else if (row.outcome === 'no_change') summary[s].no_change += row.cnt;
      else if (row.outcome === 'error') summary[s].error += row.cnt;
    }

    // Distinct IPFR pages triggered within the same filter window (Stage 7).
    const triggeredRows = db.prepare(`
      SELECT triggered_pages
      FROM pipeline_runs
      ${joinClause}
      ${where ? where + ' AND ' : 'WHERE '} triggered_pages IS NOT NULL AND triggered_pages != '[]'
    `).all(...params);

    const pageIds = new Set();
    for (const row of triggeredRows) {
      try {
        for (const id of JSON.parse(row.triggered_pages)) pageIds.add(id);
      } catch { /* malformed JSON — skip */ }
    }

    res.json({
      data: Object.values(summary),
      total_runs: totalRuns,
      triggered_page_count: pageIds.size,
      triggered_row_count: triggeredRows.length,
    });
  } catch (err) {
    console.error('[runs] GET /summary:', err.message);
    res.status(500).json({ data: [], error: err.message });
  }
});

// GET /api/runs/:run_id
router.get('/:run_id', (req, res) => {
  if (!dbGuard(res)) return;

  try {
    const rows = db.prepare(`${BASE_SELECT} WHERE pipeline_runs.run_id = ?`).all(req.params.run_id);

    if (!rows.length) return res.json({ data: null });

    // Full details JSON is only needed on the single-run detail view, so it is
    // fetched separately rather than carried through BASE_SELECT.
    const detailRows = db.prepare(
      `SELECT id, details FROM pipeline_runs WHERE run_id = ?`
    ).all(req.params.run_id);
    const detailsById = Object.fromEntries(
      detailRows.map(d => [d.id, safeParseJson(d.details, {})])
    );

    const formatted = rows.map(row => {
      const base = formatRun(row);
      base.details = detailsById[row.id] ?? {};
      // Older pipeline versions embedded diff_text in details; current ones
      // record a diff_path instead. Load the file only on this per-run
      // detail route — never for list queries.
      if (base.diff_text == null && row.diff_path) {
        base.diff_text = readDiffFile(row.diff_path);
      }
      return base;
    });

    res.json({ data: formatted });
  } catch (err) {
    console.error('[runs] GET /:run_id:', err.message);
    res.status(500).json({ data: null, error: err.message });
  }
});

export default router;
