import { Router } from 'express';
import fs from 'fs';
import path from 'path';
import { REPO_ROOT } from '../db.js';

const router = Router();

const LLM_REPORTS_DIR = path.join(REPO_ROOT, 'data', 'LLM Reports');

function readReports() {
  if (!fs.existsSync(LLM_REPORTS_DIR)) return [];

  const files = fs.readdirSync(LLM_REPORTS_DIR)
    .filter(f => f.endsWith('.json'))
    .sort()
    .reverse();

  const reports = [];
  for (const file of files) {
    try {
      const raw = fs.readFileSync(path.join(LLM_REPORTS_DIR, file), 'utf8');
      const report = JSON.parse(raw);
      report._filename = file;
      reports.push(report);
    } catch {
      // skip malformed files
    }
  }
  return reports;
}

// GET /api/llm-reports
router.get('/', (req, res) => {
  try {
    const all = readReports();

    const { verdict, run_id, page_id } = req.query;
    let filtered = all;
    if (verdict) filtered = filtered.filter(r => r.verdict === verdict);
    if (run_id)  filtered = filtered.filter(r => r.run_id === run_id);
    if (page_id) filtered = filtered.filter(r => r.ipfr_page_id === page_id);

    const verdictCounts = { CHANGE_REQUIRED: 0, UNCERTAIN: 0, NO_CHANGE: 0 };
    for (const r of all) {
      if (r.verdict in verdictCounts) verdictCounts[r.verdict]++;
    }

    res.json({
      data: filtered,
      total: filtered.length,
      all_count: all.length,
      verdict_counts: verdictCounts,
    });
  } catch (err) {
    console.error('[llm-reports] GET /:', err.message);
    res.status(500).json({ data: [], error: err.message });
  }
});

// GET /api/llm-reports/:filename
router.get('/:filename', (req, res) => {
  const filename = path.basename(req.params.filename);
  if (!filename.endsWith('.json')) return res.status(400).json({ error: 'invalid filename' });

  const filePath = path.join(LLM_REPORTS_DIR, filename);
  if (!fs.existsSync(filePath)) return res.status(404).json({ error: 'not found' });

  try {
    const report = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    res.json({ data: report });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

export default router;
