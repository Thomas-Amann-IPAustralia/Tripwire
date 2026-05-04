import { Router } from 'express';
import fs from 'fs';
import { db, dbGuard, FEEDBACK_PATH } from '../db.js';

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

const BASE_SELECT = `
  SELECT
    id, run_id, source_id, source_url, source_type, timestamp,
    ${STAGE_REACHED_CASE} AS stage_reached,
    outcome, triggered_pages, duration_seconds,
    json_extract(details, '$.stages.llm_assessment.verdict')           AS verdict,
    json_extract(details, '$.stages.llm_assessment.confidence')        AS confidence,
    json_extract(details, '$.stages.llm_assessment.reasoning')         AS reasoning,
    json_extract(details, '$.stages.llm_assessment.suggested_changes') AS suggested_changes_json,
    json_extract(details, '$.stages.diff.diff_text')                   AS diff_text,
    json_extract(details, '$.stages.biencoder.candidate_pages[0].max_chunk_score') AS biencoder_max,
    json_extract(details, '$.stages.crossencoder.scored_pages[0].crossencoder_score') AS crossencoder_score,
    json_extract(details, '$.stages.crossencoder.scored_pages[0].reranked_score')     AS reranked_score,
    json_extract(details, '$.stages.relevance.rrf_score')             AS rrf_score,
    json_extract(details, '$.stages.relevance.source_importance')     AS source_importance,
    json_extract(details, '$.stages.relevance.fast_pass_triggered')   AS fast_pass_triggered,
    json_extract(details, '$.stages.change_detection.significance')   AS significance,
    json_extract(details, '$.stages.crossencoder.scored_pages[0].page_id') AS ipfr_page_id,
    json_extract(details, '$.stages.biencoder.candidate_pages')  AS biencoder_candidates_json,
    json_extract(details, '$.stages.crossencoder.scored_pages')  AS crossencoder_scores_json,
    json_extract(details, '$.graph_propagated')                  AS graph_propagated
  FROM pipeline_runs
`;

function formatRun(row) {
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

  const { from, to, outcome, stage_reached_min, limit = 1000, offset = 0 } = req.query;

  // source_id and verdict may arrive as repeated query params → arrays
  const sourceIds = [req.query.source_id].flat().filter(Boolean);
  const verdicts  = [req.query.verdict].flat().filter(Boolean);

  const conditions = [];
  const params = [];

  if (from) { conditions.push('timestamp >= ?'); params.push(from); }
  if (to)   { conditions.push('timestamp <= ?'); params.push(to); }
  if (outcome) { conditions.push('outcome = ?'); params.push(outcome); }

  if (sourceIds.length === 1) {
    conditions.push('source_id = ?');
    params.push(sourceIds[0]);
  } else if (sourceIds.length > 1) {
    conditions.push(`source_id IN (${sourceIds.map(() => '?').join(',')})`);
    params.push(...sourceIds);
  }

  if (verdicts.length === 1) {
    conditions.push(`json_extract(details, '$.stages.llm_assessment.verdict') = ?`);
    params.push(verdicts[0]);
  } else if (verdicts.length > 1) {
    conditions.push(
      `json_extract(details, '$.stages.llm_assessment.verdict') IN (${verdicts.map(() => '?').join(',')})`
    );
    params.push(...verdicts);
  }

  if (stage_reached_min) {
    conditions.push(`(${STAGE_REACHED_CASE}) >= ?`);
    params.push(Number(stage_reached_min));
  }

  const where = conditions.length ? `WHERE ${conditions.join(' AND ')}` : '';
  const sql = `${BASE_SELECT} ${where} ORDER BY timestamp DESC LIMIT ? OFFSET ?`;
  params.push(Number(limit), Number(offset));

  try {
    const rows = db.prepare(sql).all(...params);
    const data = rows.map(formatRun);
    res.json({ data, total: data.length, limit: Number(limit), offset: Number(offset) });
  } catch (err) {
    console.error('[runs] GET /:', err.message);
    res.status(500).json({ data: [], error: err.message });
  }
});

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
        const row = db.prepare(`${BASE_SELECT} WHERE run_id = ? AND source_id = ?`).get(fb.run_id, fb.source_id);
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

// GET /api/runs/summary
router.get('/summary', (req, res) => {
  if (!dbGuard(res)) return;

  const { from, to, source_id } = req.query;
  const conditions = [];
  const params = [];

  if (from) { conditions.push('timestamp >= ?'); params.push(from); }
  if (to)   { conditions.push('timestamp <= ?'); params.push(to); }
  if (source_id) { conditions.push('source_id = ?'); params.push(source_id); }

  const where = conditions.length ? `WHERE ${conditions.join(' AND ')}` : '';

  try {
    const rows = db.prepare(`
      SELECT
        ${STAGE_REACHED_CASE} AS stage_int,
        outcome,
        COUNT(*) AS cnt
      FROM pipeline_runs
      ${where}
      GROUP BY stage_int, outcome
    `).all(...params);

    const summary = {};
    for (let s = 1; s <= 6; s++) {
      summary[s] = { stage: s, total: 0, completed: 0, no_change: 0, error: 0 };
    }

    for (const row of rows) {
      const s = row.stage_int;
      if (s < 1 || s > 6) continue;
      summary[s].total += row.cnt;
      if (row.outcome === 'completed') summary[s].completed += row.cnt;
      else if (row.outcome === 'no_change') summary[s].no_change += row.cnt;
      else if (row.outcome === 'error') summary[s].error += row.cnt;
    }

    res.json({ data: Object.values(summary) });
  } catch (err) {
    console.error('[runs] GET /summary:', err.message);
    res.status(500).json({ data: [], error: err.message });
  }
});

// GET /api/runs/:run_id
router.get('/:run_id', (req, res) => {
  if (!dbGuard(res)) return;

  try {
    const rows = db.prepare(`
      SELECT *, ${STAGE_REACHED_CASE} AS stage_reached_int,
        json(details) AS details_parsed
      FROM pipeline_runs
      WHERE run_id = ?
    `).all(req.params.run_id);

    if (!rows.length) return res.json({ data: null });

    const formatted = rows.map(row => {
      const base = formatRun({ ...row, stage_reached: row.stage_reached_int });
      base.details = safeParseJson(row.details, {});
      return base;
    });

    res.json({ data: formatted });
  } catch (err) {
    console.error('[runs] GET /:run_id:', err.message);
    res.status(500).json({ data: null, error: err.message });
  }
});

export default router;
