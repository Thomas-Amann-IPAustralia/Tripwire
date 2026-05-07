import { Router } from 'express';
import fs from 'fs';
import path from 'path';
import { db, dbGuard, REPO_ROOT } from '../db.js';

const router = Router();

const LLM_REPORTS_DIR = path.join(REPO_ROOT, 'data', 'LLM Reports');

// Read any JSON files committed to the repo that predate the SQLite table.
// Returns an array keyed by `run_id + ipfr_page_id` for deduplication.
function readJsonFileReports() {
  if (!fs.existsSync(LLM_REPORTS_DIR)) return [];
  const reports = [];
  for (const file of fs.readdirSync(LLM_REPORTS_DIR)) {
    if (!file.endsWith('.json')) continue;
    try {
      const raw = fs.readFileSync(path.join(LLM_REPORTS_DIR, file), 'utf8');
      const r = JSON.parse(raw);
      r._source = 'file';
      reports.push(r);
    } catch { /* skip malformed */ }
  }
  return reports;
}

function formatRow(row) {
  return {
    id: row.id,
    run_id: row.run_id,
    ipfr_page_id: row.ipfr_page_id,
    verdict: row.verdict,
    confidence: row.confidence,
    reasoning: row.reasoning,
    suggested_changes: (() => {
      try { return JSON.parse(row.suggested_changes); } catch { return []; }
    })(),
    model: row.model,
    prompt_tokens: row.prompt_tokens,
    completion_tokens: row.completion_tokens,
    total_tokens: row.total_tokens,
    processing_time_seconds: row.processing_time_seconds,
    retries: row.retries,
    schema_valid: !!row.schema_valid,
    generated_at: row.generated_at,
    _source: 'db',
  };
}

// GET /api/llm-reports
router.get('/', (req, res) => {
  const { verdict, run_id, page_id } = req.query;

  // --- Primary: SQLite ---
  let dbReports = [];
  if (db) {
    try {
      const conditions = [];
      const params = [];
      if (verdict) { conditions.push('verdict = ?');       params.push(verdict); }
      if (run_id)  { conditions.push('run_id = ?');        params.push(run_id); }
      if (page_id) { conditions.push('ipfr_page_id = ?'); params.push(page_id); }
      const where = conditions.length ? `WHERE ${conditions.join(' AND ')}` : '';
      dbReports = db
        .prepare(`SELECT * FROM llm_assessments ${where} ORDER BY generated_at DESC`)
        .all(...params)
        .map(formatRow);
    } catch (err) {
      // Table may not exist yet on older databases — fall through to file fallback.
      console.warn('[llm-reports] SQLite query failed (table may not exist yet):', err.message);
    }
  }

  // --- Fallback: JSON files (for reports predating the SQLite table) ---
  let fileReports = [];
  try {
    fileReports = readJsonFileReports();
    // Remove any that are already in the DB result set.
    const dbKeys = new Set(dbReports.map(r => `${r.run_id}::${r.ipfr_page_id}`));
    fileReports = fileReports.filter(r => !dbKeys.has(`${r.run_id}::${r.ipfr_page_id}`));
    // Apply filters to file reports too.
    if (verdict) fileReports = fileReports.filter(r => r.verdict === verdict);
    if (run_id)  fileReports = fileReports.filter(r => r.run_id  === run_id);
    if (page_id) fileReports = fileReports.filter(r => r.ipfr_page_id === page_id);
  } catch { /* ignore file read errors */ }

  const all = [...dbReports, ...fileReports].sort(
    (a, b) => (b.generated_at ?? '').localeCompare(a.generated_at ?? '')
  );

  // Verdict counts across the full unfiltered set (use dbReports + all file reports).
  const verdictCounts = { CHANGE_REQUIRED: 0, UNCERTAIN: 0, NO_CHANGE: 0 };
  let allCount = 0;
  if (db) {
    try {
      const rows = db.prepare(
        `SELECT verdict, COUNT(*) as cnt FROM llm_assessments GROUP BY verdict`
      ).all();
      for (const row of rows) {
        if (row.verdict in verdictCounts) verdictCounts[row.verdict] += row.cnt;
        allCount += row.cnt;
      }
    } catch { /* ignore */ }
  }
  // Also add file-sourced reports to the total count.
  for (const r of readJsonFileReports()) {
    if (r.verdict in verdictCounts) verdictCounts[r.verdict]++;
    allCount++;
  }

  res.json({
    data: all,
    total: all.length,
    all_count: allCount || all.length,
    verdict_counts: verdictCounts,
  });
});

// GET /api/llm-reports/:id  (numeric DB id or filename)
router.get('/:id', (req, res) => {
  const { id } = req.params;

  if (db && /^\d+$/.test(id)) {
    try {
      const row = db.prepare('SELECT * FROM llm_assessments WHERE id = ?').get(Number(id));
      if (row) return res.json({ data: formatRow(row) });
    } catch { /* fall through */ }
  }

  // Try as a JSON filename.
  const filename = path.basename(id.endsWith('.json') ? id : `${id}.json`);
  const filePath = path.join(LLM_REPORTS_DIR, filename);
  if (fs.existsSync(filePath)) {
    try {
      return res.json({ data: JSON.parse(fs.readFileSync(filePath, 'utf8')) });
    } catch (err) {
      return res.status(500).json({ error: err.message });
    }
  }

  res.status(404).json({ error: 'not found' });
});

export default router;
