import { Router } from 'express';
import { db, dbGuard } from '../db.js';

const router = Router();

const ROW_WARN_THRESHOLD = 100_000;

function firstKeyword(sql) {
  return sql
    .replace(/--[^\n]*/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .trim()
    .split(/\s+/)[0]
    .toUpperCase();
}

function safeValue(v) {
  if (v === null || v === undefined) return null;
  if (Buffer.isBuffer(v)) return `<BLOB ${v.length} B>`;
  if (typeof v === 'object') return String(v);
  return v;
}

router.post('/', (req, res) => {
  if (!dbGuard(res)) return;

  const { sql } = req.body;
  if (!sql || typeof sql !== 'string' || !sql.trim()) {
    return res.status(400).json({ error: 'sql_required', message: 'Query is required.' });
  }

  if (firstKeyword(sql) !== 'SELECT') {
    return res.status(400).json({
      error: 'readonly',
      message: 'Only SELECT queries are allowed.',
    });
  }

  try {
    const stmt = db.prepare(sql.trim());
    const columns = stmt.columns().map(c => c.name);
    const rows = stmt.all();
    const rowCount = rows.length;

    res.json({
      columns,
      rows: rows.slice(0, ROW_WARN_THRESHOLD).map(row =>
        columns.map(c => safeValue(row[c]))
      ),
      rowCount,
      rowWarning: rowCount > ROW_WARN_THRESHOLD
        ? `Query matched ${rowCount.toLocaleString()} rows — results truncated to ${ROW_WARN_THRESHOLD.toLocaleString()}.`
        : null,
    });
  } catch (err) {
    res.status(400).json({ error: 'query_error', message: err.message });
  }
});

export default router;
