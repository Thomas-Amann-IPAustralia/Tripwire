import { Router } from 'express';
import fs from 'fs';
import { parse } from 'csv-parse/sync';
import { stringify } from 'csv-stringify/sync';
import { db, REGISTRY_PATH } from '../db.js';

const router = Router();

const CSV_COLUMNS = ['source_id', 'url', 'title', 'source_type', 'importance', 'check_frequency', 'notes', 'force_selenium'];

function readRegistry() {
  if (!fs.existsSync(REGISTRY_PATH)) return [];
  const content = fs.readFileSync(REGISTRY_PATH, 'utf8');
  return parse(content, { columns: true, skip_empty_lines: true, trim: true });
}

function writeRegistry(records) {
  const existingColumns = records.length > 0 ? Object.keys(records[0]) : CSV_COLUMNS;
  const output = stringify(records, { header: true, columns: existingColumns });
  fs.writeFileSync(REGISTRY_PATH, output, 'utf8');
}

// GET /api/sources
router.get('/', (req, res) => {
  try {
    const sources = readRegistry();

    if (!db) {
      return res.json({ data: sources });
    }

    const cutoff = new Date(Date.now() - 90 * 24 * 60 * 60 * 1000).toISOString();

    const data = sources.map(src => {
      let stats = {
        total_checks: 0,
        last_checked: null,
        last_changed: null,
        consecutive_failures: 0,
        alert_count: 0,
        stage_distribution: {},
        check_history: [],
      };

      try {
        const agg = db.prepare(`
          SELECT
            COUNT(*) AS total_checks,
            MAX(timestamp) AS last_checked,
            MAX(CASE WHEN outcome = 'completed' THEN timestamp END) AS last_changed,
            SUM(CASE WHEN outcome = 'error' THEN 1 ELSE 0 END) AS total_errors,
            SUM(CASE WHEN json_extract(details, '$.stages.llm_assessment.verdict') = 'CHANGE_REQUIRED' THEN 1 ELSE 0 END) AS alert_count
          FROM pipeline_runs
          WHERE source_id = ?
        `).get(src.source_id);

        if (agg) {
          stats.total_checks = agg.total_checks;
          stats.last_checked = agg.last_checked;
          stats.last_changed = agg.last_changed;
          stats.alert_count = agg.alert_count;
        }

        // Consecutive failures: count from the most recent run backwards
        const recentRuns = db.prepare(`
          SELECT outcome FROM pipeline_runs
          WHERE source_id = ?
          ORDER BY timestamp DESC LIMIT 20
        `).all(src.source_id);

        let cf = 0;
        for (const r of recentRuns) {
          if (r.outcome === 'error') cf++;
          else break;
        }
        stats.consecutive_failures = cf;

        // Per-stage outcome distribution
        const stageDist = db.prepare(`
          SELECT
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
            END AS stage_int,
            outcome,
            COUNT(*) AS cnt
          FROM pipeline_runs
          WHERE source_id = ?
          GROUP BY stage_int, outcome
        `).all(src.source_id);

        for (const row of stageDist) {
          const key = `s${row.stage_int}`;
          if (!stats.stage_distribution[key]) stats.stage_distribution[key] = {};
          stats.stage_distribution[key][row.outcome] = row.cnt;
        }

        // Check history last 90 days
        stats.check_history = db.prepare(`
          SELECT run_id, timestamp, outcome,
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
            END AS stage_reached,
            json_extract(details, '$.stages.llm_assessment.verdict') AS verdict
          FROM pipeline_runs
          WHERE source_id = ? AND timestamp >= ?
          ORDER BY timestamp ASC
        `).all(src.source_id, cutoff);
      } catch (err) {
        console.error(`[sources] stats for ${src.source_id}:`, err.message);
      }

      return { ...src, ...stats };
    });

    res.json({ data });
  } catch (err) {
    console.error('[sources] GET /:', err.message);
    res.status(500).json({ data: [], error: err.message });
  }
});

// POST /api/sources
router.post('/', (req, res) => {
  try {
    const incoming = req.body;
    if (!incoming || !incoming.source_id) {
      return res.status(400).json({ error: 'source_id is required' });
    }

    const records = readRegistry();
    const idx = records.findIndex(r => r.source_id === incoming.source_id);

    // Merge: preserve all existing columns, update matching ones
    if (idx >= 0) {
      records[idx] = { ...records[idx], ...incoming };
    } else {
      const newRecord = {};
      const cols = records.length > 0 ? Object.keys(records[0]) : CSV_COLUMNS;
      for (const col of cols) {
        newRecord[col] = incoming[col] ?? '';
      }
      records.push(newRecord);
    }

    writeRegistry(records);
    res.json({ success: true });
  } catch (err) {
    console.error('[sources] POST /:', err.message);
    res.status(500).json({ error: err.message });
  }
});

export default router;
