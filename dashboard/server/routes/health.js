import { Router } from 'express';
import { db, dbGuard } from '../db.js';

const router = Router();

// GET /api/health/summary
router.get('/summary', (req, res) => {
  if (!dbGuard(res)) return;

  try {
    const cutoff30 = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString();

    const lastRun = db.prepare(`
      SELECT run_id, timestamp, outcome, duration_seconds
      FROM pipeline_runs
      ORDER BY timestamp DESC LIMIT 1
    `).get();

    const errorRate = db.prepare(`
      SELECT
        SUM(CASE WHEN outcome = 'error' THEN 1 ELSE 0 END) * 1.0 / MAX(COUNT(*), 1) AS rate,
        COUNT(*) AS total
      FROM pipeline_runs
      WHERE timestamp >= ?
    `).get(cutoff30);

    const sourcesMonitored = db.prepare(`
      SELECT COUNT(DISTINCT source_id) AS cnt FROM pipeline_runs
    `).get();

    const llmSchemaFailures = db.prepare(`
      SELECT COUNT(*) AS cnt FROM pipeline_runs
      WHERE timestamp >= ?
        AND json_extract(details, '$.stages.llm_assessment.schema_valid') = 0
    `).get(cutoff30);

    const crossEncoderTruncations = db.prepare(`
      SELECT COUNT(*) AS cnt FROM pipeline_runs
      WHERE timestamp >= ?
        AND json_extract(details, '$.stages.crossencoder.truncation_warnings') IS NOT NULL
        AND json_extract(details, '$.stages.crossencoder.truncation_warnings') != '[]'
    `).get(cutoff30);

    // Sources with consecutive failures >= 2
    const allSources = db.prepare(`
      SELECT DISTINCT source_id FROM pipeline_runs
    `).all();

    const consecutiveFailures = [];
    for (const { source_id } of allSources) {
      const recentRuns = db.prepare(`
        SELECT outcome FROM pipeline_runs
        WHERE source_id = ?
        ORDER BY timestamp DESC LIMIT 10
      `).all(source_id);

      let cf = 0;
      for (const r of recentRuns) {
        if (r.outcome === 'error') cf++;
        else break;
      }
      if (cf >= 2) consecutiveFailures.push({ source_id, consecutive_failures: cf });
    }

    res.json({
      data: {
        last_run: lastRun ?? null,
        error_rate_30d: errorRate?.rate ?? 0,
        total_sources_monitored: sourcesMonitored?.cnt ?? 0,
        llm_schema_failures_30d: llmSchemaFailures?.cnt ?? 0,
        cross_encoder_truncations_30d: crossEncoderTruncations?.cnt ?? 0,
        sources_with_consecutive_failures: consecutiveFailures,
      },
    });
  } catch (err) {
    console.error('[health] GET /summary:', err.message);
    res.status(500).json({ data: null, error: err.message });
  }
});

// GET /api/health/runs
router.get('/runs', (req, res) => {
  if (!dbGuard(res)) return;

  const { limit = 50, offset = 0 } = req.query;

  try {
    const rows = db.prepare(`
      SELECT
        run_id,
        MIN(timestamp) AS start_time,
        SUM(duration_seconds) AS total_duration,
        COUNT(*) AS sources_checked,
        SUM(CASE WHEN outcome = 'completed' THEN 1 ELSE 0 END) AS sources_completed,
        SUM(CASE WHEN outcome = 'no_change' THEN 1 ELSE 0 END) AS sources_no_change,
        SUM(CASE WHEN outcome = 'error'     THEN 1 ELSE 0 END) AS sources_errored,
        SUM(CASE WHEN triggered_pages IS NOT NULL AND triggered_pages != '[]' THEN 1 ELSE 0 END) AS alerts_generated
      FROM pipeline_runs
      GROUP BY run_id
      ORDER BY start_time DESC
      LIMIT ? OFFSET ?
    `).all(Number(limit), Number(offset));

    const data = rows.map(r => ({
      ...r,
      status: r.sources_errored > 0
        ? (r.sources_errored === r.sources_checked ? 'error' : 'partial')
        : 'ok',
    }));

    res.json({ data, limit: Number(limit), offset: Number(offset) });
  } catch (err) {
    console.error('[health] GET /runs:', err.message);
    res.status(500).json({ data: [], error: err.message });
  }
});

// GET /api/health/ingestion
router.get('/ingestion', (req, res) => {
  if (!dbGuard(res)) return;

  try {
    // Check if ingestion_runs table exists
    const tableExists = db.prepare(`
      SELECT name FROM sqlite_master WHERE type='table' AND name='ingestion_runs'
    `).get();

    if (!tableExists) {
      return res.json({ data: null, error: 'ingestion_runs table not found' });
    }

    const lastRun = db.prepare(`
      SELECT run_id, MAX(timestamp) AS timestamp FROM ingestion_runs
    `).get();

    if (!lastRun || !lastRun.run_id) {
      return res.json({ data: null });
    }

    const summary = db.prepare(`
      SELECT
        run_id,
        MIN(timestamp) AS start_time,
        COUNT(*) AS total_pages,
        SUM(CASE WHEN outcome = 'ingested' THEN 1 ELSE 0 END) AS pages_ingested,
        SUM(CASE WHEN outcome = 'skipped'  THEN 1 ELSE 0 END) AS pages_skipped,
        SUM(CASE WHEN outcome = 'stub'     THEN 1 ELSE 0 END) AS stubs,
        SUM(CASE WHEN outcome = 'error'    THEN 1 ELSE 0 END) AS errors,
        SUM(CASE WHEN status  = 'duplicate' THEN 1 ELSE 0 END) AS duplicates,
        SUM(COALESCE(boilerplate_bytes_stripped, 0)) AS boilerplate_bytes,
        SUM(COALESCE(keyphrase_count, 0)) AS keyphrases_total,
        SUM(COALESCE(duration_seconds, 0)) AS total_duration
      FROM ingestion_runs
      WHERE run_id = ?
    `).get(lastRun.run_id);

    res.json({ data: summary ?? null });
  } catch (err) {
    console.error('[health] GET /ingestion:', err.message);
    res.status(500).json({ data: null, error: err.message });
  }
});

export default router;
